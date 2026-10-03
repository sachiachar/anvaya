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
            downstream = asyncio.create_task(_to_browser(ws, session, owner, expand))
            cap = governance.limit(AGENT, "max_call_minutes", 20)
            done, pending = await asyncio.wait({upstream, downstream}, timeout=cap * 60,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if not done:  # the call reached its length limit (PR-10.2)
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
        if started is not None:  # record the call's minutes in the usage ledger
            minutes = (time.time() - started) / 60
            governance.record_usage(AGENT, settings.live_model, "direct", minutes=minutes,
                                    cost_usd=minutes * governance.LIVE_USD_PER_MINUTE)
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


async def _to_browser(ws: WebSocket, session, owner: dict, expand) -> None:
    heard, said = "", ""
    while True:
        received = 0
        async for message in session.receive():  # one model turn per iteration
            received += 1
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
