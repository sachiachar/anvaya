"""Live voice calls: browser <-> this service <-> Gemini Live API.

The browser never talks to Gemini directly, so the API key stays on the server.

WebSocket protocol
  browser -> server  binary: 16 kHz mono 16-bit PCM from the microphone
                     text:   {"type": "end"} to hang up
  server -> browser  binary: 24 kHz mono 16-bit PCM, the assistant's voice
                     {"type": "ready"}                       call connected
                     {"type": "heard" | "said", "text"}      live captions
                     {"type": "interrupted"}                 owner started talking; stop playback
                     {"type": "turn", "owner_turn", "assistant_turn"}  after each exchange,
                                                             with proposals to keep or discard
                     {"type": "error", "detail"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types

from . import loops, proposals
from .config import settings
from .gemini import client_for
from .governance import core as governance
from .governance import keys
from .store import new_id, store

log = logging.getLogger(__name__)


AGENT = "saathi.voice"


# How long a call's request waits, after the call ends, for its last exchanges to finish saving.
SAVE_GRACE_SECONDS = 30
# After this long, a turn still marked "saving" is treated as saved (its save was lost), so nothing
# waits on it for ever.
SAVING_EXPIRES_SECONDS = 120


async def call(ws: WebSocket, owner: dict, expand: Callable[[dict], dict]) -> None:
    await ws.accept()
    try:
        # The gateway checks the agent's registry entry and the kill switch before connecting.
        governance.check_call(AGENT, settings.live_model)
    except governance.Refused as e:
        await ws.send_json({"type": "error", "detail": str(e)})
        await ws.close()
        return
    started: float | None = None
    reserved = 0.0
    reply_tokens: dict[int, tuple[int, int]] = {}  # tokens in and out for each model reply
    saves: set[asyncio.Task] = set()  # exchanges still being saved (their cards worked out)
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice)
            )
        ),
        system_instruction=await asyncio.to_thread(_instructions, owner),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    source = keys.PLATFORM
    try:
        async with contextlib.AsyncExitStack() as stack:
            # The person's own key first, ours only as failover (PR-10.10). The key that connects
            # is kept for the whole call, even if the person changes their key meanwhile.
            links = keys.chain()
            for i, (source, kind, key) in enumerate(links):
                if source == keys.PLATFORM and i > 0:
                    governance.check_allowance_now(AGENT)  # their key failed: our key counts again
                try:
                    session = await stack.enter_async_context(
                        client_for(kind, key).aio.live.connect(model=settings.live_model, config=config))
                    break
                except Exception as e:  # only a refused or exhausted key moves on, as for every call
                    if i == len(links) - 1 or not keys.fails_over(e):
                        raise
                    keys.failed(AGENT, source, getattr(e, "code", None))
            keys.worked(source)
            started = time.time()
            await ws.send_json({"type": "ready"})
            upstream = asyncio.create_task(_from_browser(ws, session))
            downstream = asyncio.create_task(_to_browser(ws, session, owner, expand, reply_tokens, saves))
            cap = governance.limit(AGENT, "max_call_minutes", 20)
            # The person's voice minutes left today (PR-10.4); not counted on their own key.
            left = governance.remaining(AGENT) if source == keys.PLATFORM else None
            by_allowance = left is not None and left < cap
            planned = left if by_allowance else cap
            if source == keys.PLATFORM:
                # Hold the planned minutes, so a second call at the same time sees what's really left.
                governance.reserve(AGENT, planned)
                reserved = planned
            done, pending = await asyncio.wait({upstream, downstream}, timeout=planned * 60,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if not done and by_allowance:  # the person's daily voice minutes ran out
                governance.record_blocked(AGENT, "allowance", "owner.voice_minutes", "Daily voice minutes used",
                                          user=governance.current_user())
                await ws.send_json({"type": "error", "detail": "You've used today's voice minutes. You can keep "
                                    "typing, and voice is back tomorrow, or you can ask for more."})
            elif not done:  # the call reached its length limit (PR-10.2)
                governance.record_blocked(AGENT, "limit", "max_call_minutes", f"Live call ended at {cap} minutes")
                await ws.send_json({"type": "error", "detail": f"This call has reached its {cap}-minute limit. "
                                    "You can start a new call or keep typing."})
            for task in done:
                if task.exception() and not isinstance(task.exception(), WebSocketDisconnect):
                    raise task.exception()
    except WebSocketDisconnect:
        return
    except Exception as e:  # report to the app instead of dropping the call silently
        log.exception("live call failed")
        try:
            await ws.send_json({"type": "error", "detail": f"Live call failed: {keys.redact(str(e))}"})
        except Exception:
            pass
    finally:
        if reserved:  # give back the held minutes; the actual minutes are counted below
            governance.release(AGENT, reserved)
        if started is not None:  # record the call's minutes and tokens in the usage ledger
            minutes = (time.time() - started) / 60
            governance.record_usage(AGENT, settings.live_model, "direct", minutes=minutes,
                                    tokens_in=sum(t[0] for t in reply_tokens.values()),
                                    tokens_out=sum(t[1] for t in reply_tokens.values()), key_source=source)
        try:
            await ws.close()
        except Exception:
            pass
        # Last, after the minutes are counted and the held ones given back: finish saving the last
        # exchanges before the request ends. On Cloud Run a finished request may get no more CPU,
        # which would leave them marked "saving" and the review waiting.
        if saves:
            await asyncio.wait(saves, timeout=SAVE_GRACE_SECONDS)


def _instructions(owner: dict) -> str:
    return loops.live_instructions(owner, proposals.known(owner["id"]),
                                   sorted(store.list("turns", owner_id=owner["id"]), key=lambda t: t["at"]))


async def _from_browser(ws: WebSocket, session) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes"):
            await session.send_realtime_input(
                audio=types.Blob(data=message["bytes"], mime_type="audio/pcm;rate=16000")
            )
        elif message.get("text") and json.loads(message["text"]).get("type") == "end":
            await session.send_realtime_input(audio_stream_end=True)
            return


async def _to_browser(ws: WebSocket, session, owner: dict, expand,
                      reply_tokens: dict[int, tuple[int, int]], saves: set[asyncio.Task] | None = None) -> None:
    heard, said = "", ""
    reply = 0  # numbers each model reply; an interrupted reply and the next one are counted apart
    while True:
        received = 0
        async for message in session.receive():  # one model turn per iteration
            received += 1
            if usage := message.usage_metadata:
                # Usage is per reply, not a running total. Keep the latest for this reply, so a reply
                # that reports more than once is still counted once. Thinking counts as output, as
                # in gemini.py.
                reply_tokens[reply] = (usage.prompt_token_count or 0,
                                      (usage.response_token_count or 0) + (usage.thoughts_token_count or 0))
            content = message.server_content
            if not content:
                continue
            if content.input_transcription and content.input_transcription.text:
                heard += content.input_transcription.text
                await ws.send_json({"type": "heard", "text": content.input_transcription.text})
            if content.output_transcription and content.output_transcription.text:
                said += content.output_transcription.text
                await ws.send_json({"type": "said", "text": content.output_transcription.text})
            if content.interrupted:
                reply += 1
                await ws.send_json({"type": "interrupted"})
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    if part.inline_data and part.inline_data.data:
                        await ws.send_bytes(part.inline_data.data)
            if content.turn_complete and (heard.strip() or said.strip()):
                # Save in the background so the conversation keeps flowing.
                task = asyncio.create_task(_save_exchange(ws, owner, heard.strip(), said.strip(), expand))
                if saves is not None:
                    saves.add(task)
                    task.add_done_callback(saves.discard)
                heard, said = "", ""
        if received == 0:  # the session has ended
            return
        reply += 1


async def _save_exchange(ws: WebSocket, owner: dict, heard: str, said: str, expand) -> None:
    """Save one exchange of a call. Its two turns are stored straight away, marked "saving", so the
    end-of-call review knows more is coming; the cards are added once the extractor has read the
    exchange, and then the mark is cleared. If the owner discarded the exchange meanwhile, no cards
    are made for it. The store calls run in a worker thread, so the call's audio never waits on them."""
    owner_id = owner["id"]
    owner_turn, ai_turn = await asyncio.to_thread(_start_exchange, owner_id, heard, said)
    try:
        known = await asyncio.to_thread(proposals.known, owner_id)
        found = await loops.extract_from_exchange(owner, heard, said, known)
        moments, inventory, people = found.moments, found.inventory, found.people
    except Exception:
        log.exception("extraction failed; saving the exchange without proposals")
        moments, inventory, people = [], [], []

    turns = await asyncio.to_thread(_finish_exchange, owner_id, owner_turn, ai_turn, moments, inventory, people, expand)
    if turns is None:
        return
    owner_turn, ai_turn = turns
    try:
        await ws.send_json({"type": "turn", "owner_turn": owner_turn, "assistant_turn": ai_turn})
    except Exception:
        pass  # the call may have ended; the turn is saved and shows on reload


def _start_exchange(owner_id: str, heard: str, said: str) -> tuple[dict, dict]:
    now = time.time()
    owner_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now, "role": "owner",
                                     "text": heard or "…", "heard": heard, "saving": True,
                                     "attachment": {"kind": "voice", "name": "live call"}})
    ai_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now + 0.001,
                                  "role": "assistant", "text": said, "language": None, "saving": True,
                                  "proposals": {"moments": [], "inventory": [], "people": []}})
    return owner_turn, ai_turn


def _finish_exchange(owner_id: str, owner_turn: dict, ai_turn: dict, moments, inventory, people,
                     expand) -> tuple[dict, dict] | None:
    """Add the cards and clear the "saving" mark. None if the owner discarded the exchange."""
    with proposals.saving(owner_id):
        if not store.get("turns", ai_turn["id"]) or not store.get("turns", owner_turn["id"]):
            return None  # discarded while Saathi was still reading it
        source = {"source": "live call"}
        moment_docs = [d for m in moments if (d := proposals.save_moment(owner_id, m.model_dump(), source))]
        item_docs = [proposals.save_inventory(owner_id, i.model_dump(), source) for i in inventory]
        people_docs = [d for p in people if (d := proposals.save_person(owner_id, p.model_dump()))]
        try:
            owner_turn = store.update("turns", owner_turn["id"], {"saving": False})
            ai_turn = store.update("turns", ai_turn["id"], {"saving": False,
                                                            "proposals": {"moments": [m["id"] for m in moment_docs],
                                                                          "inventory": [i["id"] for i in item_docs],
                                                                          "people": [p["id"] for p in people_docs]}})
            if not store.get("turns", ai_turn["id"]) or not store.get("turns", owner_turn["id"]):
                raise LookupError("discarded")
        except Exception:
            # Discarded just now, between the check above and here: take back the new waiting cards,
            # unless another turn still mentions them.
            mentioned = {i for t in store.list("turns", owner_id=owner_id)
                         for ids in (t.get("proposals") or {}).values() for i in ids}
            for collection, docs in (("moments", moment_docs), ("inventory", item_docs), ("people", people_docs)):
                for doc in docs:
                    current = store.get(collection, doc["id"])
                    if current and current.get("status") == "pending" and doc["id"] not in mentioned:
                        store.delete(collection, doc["id"])
            return None
        return owner_turn, expand(ai_turn)
