"""Live voice calls: browser <-> this service <-> Gemini Live API.

The browser never talks to Gemini directly, so the API key stays on the server.

WebSocket protocol
  browser -> server  text, first: {"type": "auth", "token": "<Firebase ID token>"} (never in the URL)
                     binary: 16 kHz mono 16-bit PCM from the microphone
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
import io
import json
import logging
import pathlib
import time
import traceback
import wave
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types
from pydantic import ValidationError

from . import gaps, grounding, guardian_inputs, loops, planner, proposals, voiceprint
from .config import settings
from .gemini import client_for, tokens
from .governance import core as governance
from .governance import keys
from .store import new_id, store

log = logging.getLogger(__name__)


def _failure(e: BaseException) -> str:
    """What went wrong and where, never the message: a validation error's message holds the model's
    output, which holds the owner's words. The error type, which fields (for a validation error)
    and the last few code locations it passed through, enough to find a bug."""
    what = type(e).__name__
    if isinstance(e, ValidationError):
        what += ": " + ", ".join(
            f"{'.'.join(map(str, d['loc'])) or '(root)'} {d['type']}" for d in e.errors(include_input=False))
    where = " < ".join(f"{pathlib.PurePath(f.filename).name}:{f.lineno} {f.name}"
                       for f in reversed(traceback.extract_tb(e.__traceback__)[-4:]))
    return f"{what} at {where}" if where else what


AGENT = "saathi.voice"


# The plan on a call (PR-2.12.3): Saathi reads the current step, and reports how the owner answered
# it, through these tools, so the plan and its tone can change during the call. It also says at once
# when it hears another voice (PR-2.13.2), so it doesn't wait for the extractor. All are in the
# registry and Saathi's voice policy; the gateway checks each call (check_tool).
OUTCOMES = ["answered", "unsure", "not_now", "declined"]
PLAN_TOOLS = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="current_step",
        description="The current step of the conversation plan: its topic, what to do, who starts it, its tone, "
                    "and a 'correct' note if your last words broke a rule. Call it at the start and after each answer."),
    types.FunctionDeclaration(
        name="report_step",
        description="Report how the owner answered the current step.",
        parameters=types.Schema(type=types.Type.OBJECT, required=["outcome"], properties={
            "outcome": types.Schema(type=types.Type.STRING, enum=OUTCOMES)})),
    types.FunctionDeclaration(
        name="voice_joined",
        description="Call at once when you hear a second person speak, or two voices at the same time. It gives "
                    "you the step to carry out next. Never guess or say who they are."),
])]
CORRECTED = "A spoken reply broke a conversation rule; Saathi was told to correct it"
# When the extractor hears another voice that Saathi didn't report, Saathi is told to read the plan
# now, not at its next answer (PR-2.13.2). A note from the app, never shown or stored.
VOICE_NOTE = ("Note from the app, not from the person: the plan has changed. Call current_step now, and carry "
              "out its step when they finish speaking. Don't reply to this note.")
# When the owner taps "Talk to Saathi", Saathi speaks first: Gemini Live would otherwise wait for
# their voice. On their first call ever Saathi introduces itself; on later calls it greets them. Either
# way it then reads the plan, which decides what comes next. A note from the app, never shown or stored.
INTRODUCE = ("Note from the app, not from the person: they have just started their first call with you. Speak "
             "first. Greet them warmly by name, then introduce yourself in two or three short sentences: you are "
             "Saathi, their companion in Anvaya; you help them gather what their family will one day need, such as "
             "the people who matter, their stories, where money matters are kept and their wishes; nothing is kept "
             "until they tap Keep, and they can stop at any time. Then call current_step: carry out its step if it "
             "opens with you; if it waits for them, invite them to share whatever they like. Don't mention this note.")
GREET = ("Note from the app, not from the person: they have just started a call with you. Speak first, with one "
         "short, warm greeting by name. Then call current_step: carry out its step if it opens with you; if it "
         "waits for them, invite them to share whatever they like. Don't mention this note.")
# The owner's side of each exchange, as audio, so the extractor can tell whether another voice spoke
# (PR-2.13.2). Held in memory only for that exchange, capped, and never stored.
MAX_EXCHANGE_AUDIO = 16000 * 2 * 90  # 90 seconds of 16 kHz 16-bit mono
# After an exchange without the owner's words, this much of their audio carries over to the next:
# enough to catch them starting to speak over Saathi, short enough to leave out older sounds.
CARRY_OVER_AUDIO = 16000 * 2 * 3  # 3 seconds


def _wav(pcm: bytes, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buffer.getvalue()

# How long a call's request waits, after the call ends, for its last exchanges to finish saving.
SAVE_GRACE_SECONDS = 30
# After this long, a turn still marked "saving" is treated as saved (its save was lost), so nothing
# waits on it for ever.
SAVING_EXPIRES_SECONDS = 120


async def call(ws: WebSocket, owner: dict, expand: Callable[[dict], dict], *, accepted: bool = False) -> None:
    if not accepted:  # with sign-in on, main.live_call already accepted it to read the token
        await ws.accept()
    model = governance.primary_model(AGENT)  # from the registry, never an environment variable
    try:
        # The gateway checks the agent's registry entry, its policy and the kill switch before connecting.
        governance.check_call(AGENT, model)
    except governance.Refused as e:
        await ws.send_json({"type": "error", "detail": str(e)})
        await ws.close()
        return
    started: float | None = None
    open_call: str | None = None
    reserved, reserved_on = 0.0, None
    reply_tokens: dict[int, tuple[int, int]] = {}  # tokens in and out for each model reply
    saves: set[asyncio.Task] = set()  # exchanges still being saved (their cards worked out)
    # The step Saathi is carrying out, any correction, whether Saathi heard another voice in this
    # exchange, and whether it already knows about another voice on this call.
    plan_state: dict = {"step": None, "correct": None, "voice_heard": False, "voice_told": False}
    heard_audio = bytearray()  # the owner's side of the current exchange, in memory only (PR-2.13.2)
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
        tools=PLAN_TOOLS,
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
                        client_for(kind, key).aio.live.connect(model=model, config=config))
                    break
                except Exception as e:  # only a refused or exhausted key moves on, as for every call
                    if i == len(links) - 1 or not keys.fails_over(e):
                        raise
                    keys.failed(AGENT, source, getattr(e, "code", None))
            keys.worked(source)
            started = time.time()
            open_call = governance.call_started(AGENT)  # the agents map shows the call while it lasts
            await ws.send_json({"type": "ready"})
            await _open_call(session, owner)
            upstream = asyncio.create_task(_from_browser(ws, session, heard_audio))
            downstream = asyncio.create_task(_to_browser(ws, session, owner, expand, reply_tokens, saves, plan_state,
                                                         heard_audio))
            cap = governance.limit(AGENT, "max_call_minutes", 20)
            # The person's voice minutes left today (PR-10.4); not counted on their own key.
            left = governance.remaining(AGENT) if source == keys.PLATFORM else None
            # And the service's voice minutes left today, for everyone together (PR-15.1).
            service_left = governance.service_remaining(AGENT) if source == keys.PLATFORM else None
            by_ceiling = service_left is not None and service_left < min(cap, left if left is not None else cap)
            by_allowance = not by_ceiling and left is not None and left < cap
            planned = service_left if by_ceiling else left if by_allowance else cap
            if source == keys.PLATFORM:
                # Hold the planned minutes, so a second call at the same time sees what's really left.
                reserved_on = governance.reserve(AGENT, planned)
                reserved = planned
            done, pending = await asyncio.wait({upstream, downstream}, timeout=planned * 60,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if not done and by_ceiling:  # the service's voice minutes for today ran out
                # A blocked call and a guardian event (PR-15.6), off the event loop: store writes wait.
                await asyncio.to_thread(governance.ceiling_reached, AGENT)
                await ws.send_json({"type": "error", "detail": "Voice calls are paused for today: the demo has "
                                    "reached its daily limit. You can keep typing, and voice is back tomorrow."})
            elif not done and by_allowance:  # the person's daily voice minutes ran out
                await asyncio.to_thread(governance.allowance_reached, AGENT)
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
        log.error("live call failed (%s)", _failure(e))  # no traceback: it can hold the owner's words
        try:
            await ws.send_json({"type": "error", "detail": f"Live call failed: {keys.redact(str(e))}"})
        except Exception:
            pass
    finally:
        if open_call:
            governance.call_ended(open_call)
        if reserved:  # give back the held minutes; the actual minutes are counted below
            governance.release(AGENT, reserved, reserved_on)  # on the day it was held, even after midnight
        if started is not None:  # record the call's minutes and tokens in the usage ledger
            minutes = (time.time() - started) / 60
            governance.record_usage(AGENT, model, "direct", minutes=minutes,
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
    """What's kept (no gaps: they feed only the plan, which Saathi reads with current_step); no
    earlier conversation, only Saathi's own last message if it's from the same conversation (e.g.
    a question from "Talk about this")."""
    last_said = gaps.recent(owner.get("last_said"))
    return loops.live_instructions(owner, proposals.known(owner["id"]), last_said["text"] if last_said else None)


async def _from_browser(ws: WebSocket, session, heard_audio: bytearray | None = None) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes"):
            if heard_audio is not None and len(heard_audio) < MAX_EXCHANGE_AUDIO:
                heard_audio.extend(message["bytes"][:MAX_EXCHANGE_AUDIO - len(heard_audio)])
            await session.send_realtime_input(
                audio=types.Blob(data=message["bytes"], mime_type="audio/pcm;rate=16000")
            )
        elif message.get("text") and json.loads(message["text"]).get("type") == "end":
            await session.send_realtime_input(audio_stream_end=True)
            return


def _plan_tool(owner_id: str, name: str, args: dict, plan_state: dict) -> dict:
    """Run one of Saathi's plan tools on a call. The gateway checks the tool first; a correction
    waiting from the last spoken reply goes out once, with the next step."""
    try:
        governance.check_tool(AGENT, name)
    except governance.Refused:
        return {"error": "This tool isn't allowed."}
    if name == "current_step":
        return _step_for_saathi(owner_id, plan_state)
    if name == "report_step" and plan_state.get("step") and args.get("outcome") in OUTCOMES:
        planner.step_outcome(owner_id, plan_state["step"], args["outcome"])
        return {"ok": True}
    if name == "voice_joined":
        # Code adds the warm step, once a call; the guardian's look and the card marks come when the
        # exchange is saved. Saathi gets the step straight back, so it can ask in its next words.
        plan_state["voice_heard"] = True
        plan = None
        if not plan_state.get("voice_told"):
            plan_state["voice_told"] = True
            plan = planner.voice_heard(owner_id, new_id("live"))
        return _step_for_saathi(owner_id, plan_state, plan)
    return {"ok": False}


def _step_for_saathi(owner_id: str, plan_state: dict, plan: dict | None = None) -> dict:
    """The plan's current step for Saathi, with a correction waiting from its last spoken reply (once)."""
    step = planner.view(plan or planner.current(owner_id))["current"]
    plan_state["step"] = step["id"] if step else None
    out = {"step": step} if step else {"step": None, "note": "No step right now: let them talk about anything."}
    if plan_state.get("correct"):
        out["correct"] = plan_state["correct"]
        plan_state["correct"] = None
    return out


async def _open_call(session, owner: dict) -> None:
    """Saathi speaks first: an introduction on the owner's first call ever, a greeting after that."""
    first = not owner.get("introduced_at")
    try:
        await session.send_realtime_input(text=INTRODUCE if first else GREET)
    except Exception:
        return  # the call may have ended; the owner can still start talking
    if first and await asyncio.to_thread(store.get, "owners", owner["id"]):
        await asyncio.to_thread(store.update, "owners", owner["id"], {"introduced_at": time.time()})


async def _tell_saathi(session, plan_state: dict) -> None:
    """Send the voice note now if nobody is speaking; otherwise when the current turn ends."""
    if plan_state.get("talking"):
        plan_state["note_waiting"] = True
        return
    plan_state["note_waiting"] = False
    try:
        await session.send_realtime_input(text=VOICE_NOTE)
    except Exception:
        pass  # the call may have ended; the step waits in the plan


async def _to_browser(ws: WebSocket, session, owner: dict, expand,
                      reply_tokens: dict[int, tuple[int, int]], saves: set[asyncio.Task] | None = None,
                      plan_state: dict | None = None, heard_audio: bytearray | None = None) -> None:
    heard, said = "", ""
    reply = 0  # numbers each model reply; an interrupted reply and the next one are counted apart
    while True:
        received = 0
        async for message in session.receive():  # one model turn per iteration
            received += 1
            if usage := message.usage_metadata:
                # Usage is per reply, not a running total. Keep the latest for this reply, so a reply
                # that reports more than once is still counted once.
                reply_tokens[reply] = tokens(usage)
            if (call := getattr(message, "tool_call", None)) and call.function_calls and plan_state is not None:
                responses = [types.FunctionResponse(id=fc.id, name=fc.name, response=await asyncio.to_thread(
                    _plan_tool, owner["id"], fc.name, dict(fc.args or {}), plan_state)) for fc in call.function_calls]
                await session.send_tool_response(function_responses=responses)
                continue
            content = message.server_content
            if not content:
                continue
            if plan_state is not None and (content.input_transcription or content.model_turn):
                plan_state["talking"] = True  # the owner or Saathi is speaking: a voice note waits
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
                # Save in the background so the conversation keeps flowing. The owner's audio goes with
                # the exchange their words are in. When none of their words were heard (Saathi spoke),
                # its last seconds carry over: they may have started speaking before Saathi's turn
                # ended, and those words reach the next exchange.
                owner_spoke = bool(heard.strip())
                audio = bytes(heard_audio) if heard_audio and owner_spoke else None
                if heard_audio is not None:
                    if owner_spoke:
                        heard_audio.clear()  # the next exchange starts empty
                    else:
                        del heard_audio[:-CARRY_OVER_AUDIO]
                heard_live = bool(plan_state and plan_state.get("voice_heard"))
                if plan_state is not None:
                    plan_state["voice_heard"] = False
                task = asyncio.create_task(_save_exchange(ws, owner, heard.strip(), said.strip(), expand, plan_state, audio,
                                                          heard_live=heard_live, session=session))
                if saves is not None:
                    saves.add(task)
                    task.add_done_callback(saves.discard)
                heard, said = "", ""
            if content.turn_complete and plan_state is not None:
                plan_state["talking"] = False
                if plan_state.get("note_waiting"):
                    await _tell_saathi(session, plan_state)
        if received == 0:  # the session has ended
            return
        reply += 1


async def _save_exchange(ws: WebSocket, owner: dict, heard: str, said: str, expand,
                         plan_state: dict | None = None, audio: bytes | None = None, *,
                         heard_live: bool = False, session=None) -> None:
    """Save one exchange of a call. Its two turns are stored straight away, marked "saving", so the
    end-of-call review knows more is coming; the cards are added once the extractor has read the
    exchange, and then the mark is cleared. If the owner discarded the exchange meanwhile, no cards
    are made for it. The store calls run in a worker thread, so the call's audio never waits on them."""
    owner_id = owner["id"]
    step = (plan_state or {}).get("step")  # the step Saathi was carrying out as it spoke
    # A spoken reply can't be taken back, so it's checked after the fact (the same rules as typed
    # chat): a broken rule is recorded for the team, and Saathi corrects it with its next step.
    if (problems := loops.reply_problems(heard, said)) and plan_state is not None:
        plan_state["correct"] = problems[0]
        await asyncio.to_thread(governance.record_intervention, AGENT, "add_check", CORRECTED)
    owner_turn, ai_turn = await asyncio.to_thread(_start_exchange, owner_id, heard, said)
    # The voice check runs beside the extractor, so it adds no wait: the start of the owner's speech
    # against their voiceprint. No answer (off, too little speech, the model down) holds nothing back.
    voice_check = asyncio.create_task(voiceprint.check(owner_id, audio)) if audio else None
    found, last_said = None, None
    try:
        known, last_said = await asyncio.to_thread(_known_and_last_said, owner_id)
        # With the owner's audio, the extractor also says whether another voice spoke; the audio goes no further.
        found = await loops.extract_from_exchange(owner, heard, said, known, **({"audio": _wav(audio)} if audio else {}))
        # Cards say only what was said: checked against the owner's words and the question they answered.
        # That question is Saathi's turn stored just before this one; owners.last_said can still be the
        # one before it, while the previous exchange is saving.
        question = await asyncio.to_thread(_question_before, owner_id, owner_turn["at"])
        found = grounding.ground(found, owner_words=heard, saathi_words=question, known=known)
        moments, inventory, people = found.moments, found.inventory, found.people
    except Exception as e:
        # No traceback: a validation error carries the model's output, which holds the owner's words.
        log.error("extraction failed (%s); saving the exchange without proposals", _failure(e))
        moments, inventory, people = [], [], []
    except BaseException:  # cancelled: the voice check stops too
        if voice_check is not None:
            voice_check.cancel()
        raise
    try:
        mismatch = voice_check is not None and await voice_check == "mismatch"
    except Exception:
        log.exception("voice check failed; nothing held back")
        mismatch = False

    turns = await asyncio.to_thread(_finish_exchange, owner_id, owner_turn, ai_turn, moments, inventory, people, expand,
                                    found, last_said, step, heard_live, mismatch)
    if turns is None:
        return
    # The extractor heard a voice Saathi didn't report, or the voice didn't match the owner's: if that
    # put the warm step first, Saathi is told now.
    if (session is not None and plan_state is not None
            and (getattr(found, "voices", "one") == "more" or mismatch) and not plan_state.get("voice_told")):
        plan_state["voice_told"] = True
        current = (await asyncio.to_thread(lambda: planner.view(planner.current(owner_id))))["current"]
        if current and current["source"] == "voice":
            await _tell_saathi(session, plan_state)
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


def _question_before(owner_id: str, at: float) -> str:
    """What Saathi said last before the owner spoke: the question a short answer ("Yes") confirms."""
    earlier = [t for t in store.list("turns", owner_id=owner_id) if t.get("role") == "assistant" and t["at"] < at]
    return (max(earlier, key=lambda t: t["at"]).get("text") or "") if earlier else ""


def _known_and_last_said(owner_id: str) -> tuple[dict, dict | None]:
    """What the extractor needs: what's kept, read fresh because earlier exchanges of this call
    changed it, and Saathi's last message, whose step this exchange answers."""
    return proposals.known(owner_id), (store.get("owners", owner_id) or {}).get("last_said")


def _finish_exchange(owner_id: str, owner_turn: dict, ai_turn: dict, moments, inventory, people,
                     expand, found=None, last_said: dict | None = None, step: str | None = None,
                     heard_live: bool = False, voice_mismatch: bool = False) -> tuple[dict, dict] | None:
    """Add the cards and clear the "saving" mark. None if the owner discarded the exchange. A voice
    that didn't match the owner's voiceprint holds the exchange's cards until the owner confirms."""
    with proposals.saving(owner_id):
        if not store.get("turns", ai_turn["id"]) or not store.get("turns", owner_turn["id"]):
            return None  # discarded while Saathi was still reading it
        source = {"source": "live call"}
        # The guardian sees each new piece of information first; only what it allows becomes a card.
        screened = guardian_inputs.screen(owner_words=owner_turn.get("heard") or "", moments=moments,
                                          inventory=inventory, people=people, turn_id=owner_turn["id"],
                                          agent="capture.extractor")
        moment_docs, item_docs, people_docs = proposals.save_screened(owner_id, screened, source)
        # The plan takes in the guardian's decisions, the owner's mood and how they answered the last step.
        # Another voice in this exchange (PR-2.13.2): Saathi heard it live, or the extractor did in the audio.
        in_audio = getattr(found, "voices", "one") == "more"
        voices = heard_live or in_audio
        card_docs = [*[("moments", d) for d in moment_docs], *[("inventory", d) for d in item_docs],
                     *[("people", d) for d in people_docs]]
        if voices:
            heard_by = [a for a, heard_it in ((AGENT, heard_live), ("capture.extractor", in_audio)) if heard_it]
            guardian_inputs.another_voice(owner_id, owner_turn["id"], heard_by[0], heard_by=heard_by,
                                          card_docs=card_docs)
        if voice_mismatch:
            guardian_inputs.voice_mismatch(owner_id, owner_turn["id"], AGENT, card_docs=card_docs)
        # A voice that isn't the owner's gets the same warm "who's with you" step as another voice.
        planner.after_turn(owner_id, owner_turn["id"], screened.outcomes, mood=getattr(found, "owner_mood", "calm"),
                           answered_step=(last_said or {}).get("step"), outcome=getattr(found, "step_outcome", "none"),
                           another_voice=voices or voice_mismatch)
        try:
            owner_turn = store.update("turns", owner_turn["id"], {"saving": False, "guardian": screened.decisions,
                                                                  "held_back": screened.held_back,
                                                                  **({"voice_mismatch": True} if voice_mismatch else {})})
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
        gap = step.removeprefix("gap:") if step and step.startswith("gap:") else None
        gaps.after_reply(owner_id, last_said, text=ai_turn["text"], turn_id=ai_turn["id"], now=ai_turn["at"],
                         asked_gap=gap, outcome=getattr(found, "step_outcome", None), step=step)
        return owner_turn, expand(ai_turn)
