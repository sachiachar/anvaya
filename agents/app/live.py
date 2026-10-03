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
import json
import logging
import time
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types

from . import loops, proposals
from .config import settings
from .gemini import client
from .governance import core as governance
from .store import new_id, store

log = logging.getLogger(__name__)


AGENT = "saathi.voice"


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
    turn_tokens: dict[int, tuple[int, int]] = {}  # tokens in and out for each model turn
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice)
            )
        ),
        system_instruction=loops.live_instructions(
            owner, proposals.known(owner["id"]),
            sorted(store.list("turns", owner_id=owner["id"]), key=lambda t: t["at"]),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    try:
        async with client().aio.live.connect(model=settings.live_model, config=config) as session:
            started = time.time()
            await ws.send_json({"type": "ready"})
            upstream = asyncio.create_task(_from_browser(ws, session))
            downstream = asyncio.create_task(_to_browser(ws, session, owner, expand, turn_tokens))
            cap = governance.limit(AGENT, "max_call_minutes", 20)
            left = governance.remaining(AGENT)  # the person's voice minutes left today (PR-10.4)
            by_allowance = left is not None and left < cap
            planned = left if by_allowance else cap
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
            await ws.send_json({"type": "error", "detail": f"Live call failed: {e}"})
        except Exception:
            pass
    finally:
        if reserved:  # give back the held minutes; the actual minutes are counted below
            governance.release(AGENT, reserved)
        if started is not None:  # record the call's minutes and tokens in the usage ledger
            minutes = (time.time() - started) / 60
            governance.record_usage(AGENT, settings.live_model, "direct", minutes=minutes,
                                    tokens_in=sum(t[0] for t in turn_tokens.values()),
                                    tokens_out=sum(t[1] for t in turn_tokens.values()))
        try:
            await ws.close()
        except Exception:
            pass


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
                      turn_tokens: dict[int, tuple[int, int]]) -> None:
    heard, said = "", ""
    turn = 0
    while True:
        received = 0
        async for message in session.receive():  # one model turn per iteration
            received += 1
            if usage := message.usage_metadata:
                # Usage is per turn, not a running total. Keep the latest for this turn, so a turn
                # that reports more than once is still counted once.
                turn_tokens[turn] = (usage.prompt_token_count or 0, usage.response_token_count or 0)
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
                await ws.send_json({"type": "interrupted"})
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    if part.inline_data and part.inline_data.data:
                        await ws.send_bytes(part.inline_data.data)
            if content.turn_complete and (heard.strip() or said.strip()):
                # Save in the background so the conversation keeps flowing.
                asyncio.create_task(_save_exchange(ws, owner, heard.strip(), said.strip(), expand))
                heard, said = "", ""
        if received == 0:  # the session has ended
            return
        turn += 1


async def _save_exchange(ws: WebSocket, owner: dict, heard: str, said: str, expand) -> None:
    owner_id = owner["id"]
    try:
        found = await loops.extract_from_exchange(owner, heard, said, proposals.known(owner_id))
        moments, inventory, people = found.moments, found.inventory, found.people
    except Exception:
        log.exception("extraction failed; saving the exchange without proposals")
        moments, inventory, people = [], [], []

    now = time.time()
    source = {"source": "live call"}
    moment_docs = [d for m in moments if (d := proposals.save_moment(owner_id, m.model_dump(), source))]
    item_docs = [proposals.save_inventory(owner_id, i.model_dump(), source) for i in inventory]
    people_docs = [d for p in people if (d := proposals.save_person(owner_id, p.model_dump()))]
    owner_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now, "role": "owner",
                                     "text": heard or "…", "heard": heard,
                                     "attachment": {"kind": "voice", "name": "live call"}})
    ai_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now + 0.001,
                                  "role": "assistant", "text": said, "language": None,
                                  "proposals": {"moments": [m["id"] for m in moment_docs],
                                                "inventory": [i["id"] for i in item_docs],
                                                "people": [p["id"] for p in people_docs]}})
    try:
        await ws.send_json({"type": "turn", "owner_turn": owner_turn, "assistant_turn": expand(ai_turn)})
    except Exception:
        pass  # the call may have ended; the turn is saved and shows on reload
