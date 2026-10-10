"""HTTP API for the Anvaya app. Every route is under /api so Firebase Hosting
can proxy it to Cloud Run on the same origin as the web app.

Autonomy level of each action is returned as `autonomy` so the UI can show it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from google.auth.exceptions import DefaultCredentialsError
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field, ValidationError

from . import (auth, demo, escrow, fraud_guides, fraud_pipeline, gaps, grounding, guardian_inputs, knowledge,
               knowledge_map, live, loops, media, model_access, planner, proposals, release, scheduler, speech, step_guard,
               tasks, tone)
from .config import settings
from . import executor_agent
from .executor_agent import UnknownSession, ask
from .governance import core as governance_core
from .governance import guardian
from .governance import keys as model_keys
from .governance import portal
from .store import new_id, store

# Keys people add themselves never reach a log line, from any library (PR-10.10).
model_keys.install_log_redaction()

# A broken registry.yaml or policies.yaml stops the API here, rather than loosening a rule.
log = logging.getLogger(__name__)
governance_core.check_files()
guardian.check_policy()  # a broken guardian.yaml stops start-up too
planner.check_plan()  # and so does a broken conversation_plan.yaml (PR-2.12.2)
app = FastAPI(title="Anvaya agents", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store(request: Request, call_next):
    """Firebase Hosting's CDN keeps answers that don't say otherwise (a 404 for 10 minutes), so a
    poll could keep getting "no vault recipient yet" after the person accepted. Every answer here
    is about someone's own, changing data: nothing is kept."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


# Every HTTP route needs a signed-in, allowed user (when AUTH_REQUIRED=true), and works only
# on that user's own demo copy (PR-1.6): an owner in the path must be theirs, and documents
# looked up by ID are checked with demo.mine. The live-call WebSocket checks its own token
# and owner (see live_call); /healthz stays open.
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user), Depends(demo.guard)])


# ---- Gemini failures: readable errors instead of a bare 500 --------------------


@app.exception_handler(DefaultCredentialsError)
async def no_credentials(_: Request, exc: DefaultCredentialsError) -> JSONResponse:
    return JSONResponse(status_code=503, content={
        "detail": "Gemini is not configured: no Google Cloud credentials. Run "
                  "'gcloud auth application-default login', or set GOOGLE_API_KEY in agents/.env."})


@app.exception_handler(genai_errors.APIError)
async def gemini_error(_: Request, exc: genai_errors.APIError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"Gemini error {exc.code}: {model_keys.redact(exc.message)}"})


@app.exception_handler(governance_core.Refused)
async def refused_by_gateway(_: Request, exc: governance_core.Refused) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
async def bad_model_output(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=502, content={
        "detail": "Gemini returned output that did not match the expected format. Try again."})

ASSIST, PROPOSE, ACT = "assist", "propose", "act_within_limits"
# The agents behind each item the app shows; its autonomy label comes from their policy (PR-15.7).
CARDS, STEPS, ASSISTANT = "capture.extractor", "readiness.planner", "handover.assistant"
# A person's own action, not an agent's, when it asks the guardian (PR-2.7): the owner changing the
# vault recipient.
OWNER_ACTS = "person:owner"
# Fields of a card that aren't what it says, left out of the guardian's keep check: ids, sources, labels.
NOT_WORDS = ("id", "owner_id", "status", "kind", "category", "recording", "document", "updates", "collection")


def owner_or_404(owner_id: str) -> dict:
    owner = store.get("owners", owner_id)
    if not owner:
        raise HTTPException(404, "owner not found")
    return {**owner, "family": knowledge.family(owner_id)}  # family comes from kept Person concepts


def open_case(owner_id: str) -> dict | None:
    cases = [c for c in store.list("cases", owner_id=owner_id) if c["state"] != "cancelled"]
    if not cases:
        return None
    case = _refresh(max(cases, key=lambda c: c["history"][0]["at"]))
    store.put("cases", case)
    return case


def _refresh(case: dict) -> dict:
    """release.refresh, with the guardian asked once when the waiting period ends (PR-2.7.4). If a
    document check found warning signs or a reviewer overrode a "not valid", it holds the release
    for a risk reviewer: "reverse" on the Guardian page releases it, "confirm" stops it."""
    due = case["state"] == "waiting" and case.get("wait_until") is not None and time.time() >= case["wait_until"]
    if due and not case.get("release_hold") and not case.get("release_cleared"):
        signs = sum(1 for check in ("certificate_check", "authority_check")
                    for s in (case.get(check) or {}).get("signs") or [] if s.get("result") == "found")
        override = any("OVERRIDE of AI" in h["event"] for h in case["history"])
        # Stable ids: two requests at the same moment (Priya's app and the reviewers' list) make one
        # event and one hold for the case, never two that could be decided differently.
        event_id, hold_id = f"gev_release_{case['id']}", f"ghd_release_{case['id']}"
        decision = guardian.decide("release", CHECKER, {"signs_found": signs, "override": override},
                                   refs={"case": case["id"]}, case_key=case["id"], once=event_id,
                                   record_id=f"gac_release_{case['id']}")
        if decision.goes_ahead:
            case["release_cleared"] = True
        else:
            guardian.hold(decision, "release", case["owner_id"], {"case_id": case["id"]}, hold_id=hold_id, event_id=event_id)
            case["release_hold"] = hold_id
            case["history"].append({"at": time.time(), "event": "release held for a check by a risk reviewer"})
    return release.refresh(case)


def _finish_release(payload: dict) -> None:
    case = store.get("cases", payload["case_id"])
    if case and case["state"] == "waiting":
        case.update(release_hold=None, release_cleared=True)
        case["history"].append({"at": time.time(), "event": "a risk reviewer cleared the release"})
        store.put("cases", release.refresh(case))


def _cancel_release(payload: dict) -> None:
    case = store.get("cases", payload["case_id"])
    if case and case["state"] == "waiting":
        case.update(state="cancelled", release_hold=None)
        case["history"].append({"at": time.time(), "event": "release stopped after a check by a risk reviewer"})
        store.put("cases", case)


def require_handover(owner_id: str) -> None:
    if not release.is_released(open_case(owner_id)):
        raise HTTPException(403, "available only after the death is confirmed and checks are complete")


# ---- Demo setup -------------------------------------------------------------


@api.post("/demo/seed")
def seed_demo() -> dict:
    """Reset the caller's own demo copy to the synthetic persona (PR-1.5). Never touches another
    copy. Never use real personal data in demos."""
    demo.ensure()
    return {"owner": demo.reset()}


@api.get("/owners/{owner_id}")
def get_owner(owner_id: str) -> dict:
    return owner_or_404(owner_id)


# ---- Capture loop -------------------------------------------------------------


@api.post("/owners/{owner_id}/capture/audio", dependencies=[Depends(demo.only("owner"))])
async def capture_audio(owner_id: str, audio: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    data = await audio.read()
    mime = (audio.content_type or "audio/webm").split(";")[0]
    recording = media.save(owner_id, f"{new_id('rec')}.{mime.split('/')[-1]}", data, mime)
    result = await loops.capture_audio(data, mime, owner)
    result = grounding.ground(result, owner_words=result.transcript)  # cards say only what was said
    proposals = []
    for moment in result.moments:
        doc = {"id": new_id("m"), "owner_id": owner_id, "status": "pending",
               "recording": recording, "language": result.language, **moment.model_dump()}
        proposals.append(store.put("moments", doc))
    return {"autonomy": ACT, "language": result.language, "transcript": result.transcript,
            "proposals": proposals}


# ---- Conversation: one interface for voice, text, photos and documents -------


def _attachment_kind(mime: str) -> str:
    if mime.startswith("audio/"):
        return "voice"
    if mime.startswith("image/"):
        return "photo"
    return "document"


def _expand(turn: dict) -> dict:
    """Replace proposal ids with the items and their current status (discarded ones drop out).
    The path of a file the owner shared stays on the server (it's only for Discard). A turn marked
    "saving" for too long lost its save, so it no longer counts as saving."""
    turn = {k: v for k, v in turn.items() if k != "media"}
    if turn.get("saving") and time.time() - turn.get("at", 0) > live.SAVING_EXPIRES_SECONDS:
        turn["saving"] = False
    ids = turn.get("proposals")
    if not ids:
        return turn
    # A draft is in Firestore; once kept it's in the owner's bundle, under the same id.
    label = governance_core.autonomy(CARDS)
    items = [{**doc, "collection": c, "autonomy": label} for c in ("people", "moments", "inventory")
             for doc in (store.get(c, i) or knowledge.get(turn["owner_id"], i) for i in ids.get(c, [])) if doc]
    return {**turn, "proposals": items, "discarded": sum(len(v) for v in ids.values()) - len(items)}


@api.get("/owners/{owner_id}/conversation")
def get_conversation(owner_id: str) -> dict:
    turns = [_expand(t) for t in sorted(store.list("turns", owner_id=owner_id), key=lambda t: t["at"])]
    # An item mentioned several times (e.g. a policy, then its nominee) shows once: on its latest turn.
    seen: set[str] = set()
    for turn in reversed(turns):
        if isinstance(turn.get("proposals"), list):
            turn["proposals"] = [p for p in turn["proposals"] if not (p["id"] in seen or seen.add(p["id"]))]
    return {"turns": turns}


@api.post("/owners/{owner_id}/conversation", dependencies=[Depends(demo.only("owner"))])
async def converse(owner_id: str, text: str | None = Form(None), file: UploadFile | None = File(None)) -> dict:
    """One turn: the owner talks, types, or shares a photo or document. The AI replies,
    and anything worth keeping comes back as proposals the owner can keep or discard.
    Store and media calls run in worker threads, so one slow read doesn't hold up everyone else."""
    owner = await asyncio.to_thread(owner_or_404, owner_id)
    if not (text and text.strip()) and not file:
        raise HTTPException(400, "say something, type something, or attach a file")

    upload, attachment, source, path = None, None, {}, None
    if file:
        data = await file.read()
        mime = (file.content_type or "application/octet-stream").split(";")[0]
        kind = _attachment_kind(mime)
        path = await asyncio.to_thread(media.save, owner_id, f"{new_id(kind)}.{mime.split('/')[-1]}", data, mime)
        upload = (data, mime)
        attachment = {"kind": kind, "name": file.filename, "mime": mime}
        source = {"recording": path} if kind == "voice" else {"document": path}

    # Guardian-led (PR-2.12.3): read the turn, let the guardian see each new piece of information and
    # the plan move on, and only then let Saathi reply, from the plan's current step and its tone.
    # Saathi carries on from what's kept and its own last message, never from older conversation.
    last_said = gaps.recent(owner.get("last_said"))
    known = await asyncio.to_thread(proposals.known, owner_id)
    result = await loops.understand(owner, last_said, text, upload, known)
    # Cards say only what was said: the owner's words (typed, or heard in a voice note), checked in code.
    voice = bool(attachment and attachment["kind"] == "voice")
    result = grounding.ground(result, owner_words=" ".join(filter(None, [text, result.heard if voice else ""])),
                              saathi_words=(last_said or {}).get("text") or "", known=known,
                              from_document=bool(attachment) and not voice)
    taken = await asyncio.to_thread(_take_in, owner_id, text, result, attachment, path, source, last_said)
    reply = await loops.respond(owner, taken["step"], last_said, (text or "").strip() or result.heard, known)
    return await asyncio.to_thread(_answer, owner_id, taken, result, reply, last_said)


def _take_in(owner_id: str, text: str | None, result, attachment: dict | None, path: str | None, source: dict,
             last_said: dict | None) -> dict:
    """The owner's side of a turn: the guardian sees each new piece of information first, only what it
    allows becomes a card, and the plan takes in the guardian's decisions, the owner's mood and how
    they answered the step Saathi carried out last. Returns the turn, its cards and the step to carry out."""
    with proposals.saving(owner_id):  # cards from a call exchange may be saving at the same moment
        store.update("owners", owner_id, {"language": result.owner_language})
        now = time.time()
        owner_turn_id = new_id("turn")
        screened = guardian_inputs.screen(owner_words=(text or "").strip() or result.heard, moments=result.moments,
                                          inventory=result.inventory, people=result.people, turn_id=owner_turn_id,
                                          agent="saathi.chat")
        moments, inventory, people = proposals.save_screened(owner_id, screened, source)
        plan = planner.after_turn(owner_id, owner_turn_id, screened.outcomes, mood=result.owner_mood,
                                  answered_step=(last_said or {}).get("step"), outcome=result.step_outcome)
        owner_turn = store.put("turns", {"id": owner_turn_id, "owner_id": owner_id, "at": now, "role": "owner",
                                         "text": (text or "").strip() or result.heard, "heard": result.heard,
                                         "attachment": attachment, **({"media": path} if path else {}),
                                         "guardian": screened.decisions, "held_back": screened.held_back})
    return {"owner_turn": owner_turn, "at": now, "step": planner.view(plan)["current"],
            "proposals": {"moments": [m["id"] for m in moments], "inventory": [i["id"] for i in inventory],
                          "people": [p["id"] for p in people]}}


def _answer(owner_id: str, taken: dict, result, reply, last_said: dict | None) -> dict:
    """Saathi's side of a turn: its reply, the step it carried out, and the gap that step asked about
    (so a gap comes back only as a reminder)."""
    step = taken["step"] or {}
    ai_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": max(time.time(), taken["at"] + 0.001),
                                  "role": "assistant", "text": reply.reply, "language": reply.reply_language,
                                  "step": step.get("id"), "proposals": taken["proposals"]})
    gap = step["id"].removeprefix("gap:") if step.get("source") == "gap" and step.get("opens") == "saathi" else None
    gaps.after_reply(owner_id, last_said, text=reply.reply, turn_id=ai_turn["id"], asked_gap=gap,
                     outcome=result.step_outcome, step=step.get("id"))
    return {"autonomy": ACT, "owner_turn": _expand(taken["owner_turn"]), "assistant_turn": _expand(ai_turn)}


@app.websocket("/api/owners/{owner_id}/live")
async def live_call(ws: WebSocket, owner_id: str, token: str | None = None, acting_as: str | None = None) -> None:
    """Two-way voice call with the companion (Gemini Live API). See live.py for the protocol.
    Browsers can't send headers on a WebSocket, so the sign-in token comes as ?token=."""
    if settings.auth_required:
        try:
            user = await asyncio.to_thread(auth.verify, token)
        except HTTPException as e:
            await ws.close(code=4401 if e.status_code == 401 else 4403, reason=e.detail[:120])
            return
        governance_core.set_current_user(governance_core.user_key(user.uid))
        demo.begin(user.uid)
    else:
        governance_core.set_current_user(governance_core.user_key(demo.LOCAL))
        demo.begin(demo.LOCAL)
    governance_core.set_channel("live_call")  # the guardian records what this call does as a live call
    await asyncio.to_thread(demo.ensure)  # no catalog cache here: a call lasts minutes, and kept knowledge can change meanwhile
    if (acting_as or demo.DEFAULT_PERSON).strip().lower() != "vikram":  # only the owner talks with Saathi
        await ws.close(code=4403, reason="Only Vikram can call Saathi.")
        return
    owner = await asyncio.to_thread(store.get, "owners", owner_id)
    if not owner or owner_id != demo.owner_id():  # only the caller's own copy
        await ws.close(code=4404)
        return
    family = await asyncio.to_thread(knowledge.family, owner_id)
    await live.call(ws, {**owner, "family": family}, _expand)  # family from kept people


def _speech_name(text: str) -> str:
    """Where a spoken reply is cached: the same words in the same voice share one file."""
    return f"speech/{hashlib.sha256(f'{settings.tts_voice}:{text}'.encode()).hexdigest()[:32]}.wav"


class Speech(BaseModel):
    turn_id: str


@api.post("/speech")
async def speech_audio(body: Speech) -> Response:
    """Spoken version of one of Saathi's replies, as a WAV file (PR-10.3).

    Only a reply Saathi actually gave can be spoken, never arbitrary text. The audio is
    saved and reused for the same words, so replaying a reply, or a reply that repeats
    earlier words, costs nothing.
    """
    turn = demo.mine("turns", body.turn_id)
    if not turn or turn.get("role") != "assistant" or not (turn.get("text") or "").strip():
        raise HTTPException(404, "reply not found")
    name = _speech_name(turn["text"])
    if cached := media.load(turn["owner_id"], name):
        return Response(content=cached, media_type="audio/wav")
    try:
        audio = await speech.speak(turn["text"])
        media.save(turn["owner_id"], name, audio, "audio/wav")
        return Response(content=audio, media_type="audio/wav")
    except speech.VoiceUnavailable as e:
        raise HTTPException(503, str(e)) from e


@api.post("/owners/{owner_id}/documents", dependencies=[Depends(demo.only("owner"))])
async def add_document(owner_id: str, image: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    original = media.save(owner_id, f"{new_id('doc')}.{mime.split('/')[-1]}", data, mime)
    item = await loops.extract_document(data, mime, owner)
    doc = {"id": new_id("inv"), "owner_id": owner_id, "status": "pending",
           "document": original, **item.model_dump()}
    return {"autonomy": ACT, "proposal": store.put("inventory", doc)}


@api.post("/owners/{owner_id}/wishes", dependencies=[Depends(demo.only("owner"))])
def add_wish(owner_id: str, text: str = Form(...)) -> dict:
    """A wish the owner types is the owner's own action, so it is approved at once."""
    owner_or_404(owner_id)
    doc = {"id": new_id("m"), "kind": "wish",
           "title": text[:60], "summary": text, "quote": text, "people": [], "places": [], "year": None}
    return knowledge.commit("moments", doc, owner_id, generated_by=f"human:owner-{owner_id}")


# ---- Curation loop (Propose: the owner decides) -------------------------------


@api.get("/owners/{owner_id}/review")
def review_queue(owner_id: str) -> dict:
    label = governance_core.autonomy(CARDS)
    return {
        "moments": [{**d, "autonomy": label} for d in store.list("moments", owner_id=owner_id, status="pending")],
        "inventory": [{**d, "autonomy": label} for d in store.list("inventory", owner_id=owner_id, status="pending")],
    }


class Decision(BaseModel):
    collection: Literal["moments", "inventory", "people"]
    decision: Literal["approve", "reject"]
    edits: dict | None = None
    # "Replace it" on a card that looks like a kept item: keep it as that item's new version.
    replace: bool = False


@api.post("/proposals/{doc_id}/decision", dependencies=[Depends(demo.only("owner"))])
def decide(doc_id: str, body: Decision) -> dict:
    doc = demo.mine(body.collection, doc_id)
    if not doc or doc["status"] != "pending":
        raise HTTPException(404, "no pending proposal with that id")
    if body.decision == "reject":
        store.delete(body.collection, doc_id)
        return {"id": doc_id, "status": "rejected"}
    # Edits change what a card says, never what it is or where it came from.
    safe_edits = {k: v for k, v in (body.edits or {}).items()
                  if k not in ("id", "owner_id", "status", "updates", "looks_like", "recording", "document")}
    replaces = None
    if body.replace:  # checked before any edit is saved, so a refused Replace changes nothing
        target = (doc.get("looks_like") or {}).get("id")
        kept = knowledge.current(doc["owner_id"], target) if target else None
        kind = safe_edits.get("kind", doc.get("kind"))
        if body.collection != "moments" or not kept or kept.get("kind") != kind:
            raise HTTPException(409, "There's nothing kept of the same kind for this card to replace.")
        replaces = kept["id"]
    # Keeping asks the guardian first (PR-2.7.4): a card that passes on a secret or pays because
    # someone asked is never kept. It reads the card as it would be kept, edits included.
    # Each field on its own, so a "never" in one field can't clear an instruction in another.
    card = {**doc, **safe_edits}
    problem = next((p for k, v in card.items() if k not in NOT_WORDS and isinstance(v, str)
                    and (p := step_guard.reply_problem(v))), None)
    decision = guardian.decide("keep_card", CARDS, {"problem": problem}, refs={"card": doc_id})
    if not decision.goes_ahead:
        raise HTTPException(409, "This can't be kept: it passes on a secret or a payment someone asked for. "
                                 "Passwords and PINs belong in your vault.")
    if safe_edits:
        doc = store.update(body.collection, doc_id, safe_edits)
    if replaces:
        doc = store.update(body.collection, doc_id, {"updates": replaces})
    try:
        kept = proposals.approve(body.collection, doc)
    except knowledge.Removed as e:
        raise HTTPException(409, "You removed the item this card updates, so it can't be kept.") from e
    # The audit chain (PR-2.12.5): the card is gone now, so the Keep decision remembers the decision
    # that allowed the card and the item it became.
    store.update("guardian_actions", decision.action_id, {"refs": {"card": doc_id, "kept": kept["id"],
                 **({"statement": doc["guardian_action"]} if doc.get("guardian_action") else {})}})
    planner.refresh(doc["owner_id"], {"kind": "keep", "ref": doc_id})  # a kept item can finish a step
    return kept


@api.get("/owners/{owner_id}/archive")
def archive(owner_id: str) -> dict:
    return {
        "moments": knowledge.items(owner_id, "moments"),
        "inventory": knowledge.items(owner_id, "inventory"),
        "legal_documents": store.list("legal_documents", owner_id=owner_id),
    }


@api.get("/owners/{owner_id}/kept", dependencies=[Depends(demo.only("owner"))])
def list_kept(owner_id: str) -> dict:
    """Everything the owner has kept, current versions only, so they can remove a mistake."""
    owner_or_404(owner_id)
    cat = knowledge.catalog(owner_id)
    return {"items": sorted(({"id": cid, "collection": e["collection"], "type": e["graph"]["type"],
                              "title": e["title"], "description": e["description"], "kept_at": e.get("kept_at")}
                             for cid, e in cat["concepts"].items() if e["graph"]["status"] == "stable"),
                            key=lambda i: (i["collection"], i["title"].lower()))}


@api.delete("/owners/{owner_id}/kept/{item_id}", dependencies=[Depends(demo.only("owner"))])
def remove_kept(owner_id: str, item_id: str) -> dict:
    """The owner removes something wrongly recorded: it and every earlier version are deleted from
    the bundle, so it never reaches the family. Only the owner, and not while a release case is
    open: then the bundle is what the reviewers and the family rely on."""
    owner = owner_or_404(owner_id)
    item = knowledge.current(owner_id, item_id)
    if not item:
        raise HTTPException(404, "Nothing kept with that id.")
    if open_case(owner_id):
        raise HTTPException(409, "Nothing can be removed while a handover is being reviewed.")
    if item.get("concept", "").startswith("people/") and owner.get("vault_recipient_name") and \
            proposals.same_name(item.get("name"), owner["vault_recipient_name"]):
        raise HTTPException(409, "This person receives your vault. Choose someone else first.")
    return {"removed": knowledge.remove(owner_id, item["id"]), **list_kept(owner_id)}


@api.get("/owners/{owner_id}/knowledge-map", dependencies=[Depends(demo.only("owner"))])
def get_knowledge_map(owner_id: str) -> dict:
    """The owner's kept knowledge as a graph, for the demo's knowledge map (read-only)."""
    owner = owner_or_404(owner_id)
    return knowledge_map.build(owner_id, owner.get("name") or "Owner")


@api.get("/owners/{owner_id}/gaps", dependencies=[Depends(demo.only("owner"))])
def get_gaps(owner_id: str) -> dict:
    """What's missing, found by fixed rules (PR-7.10), most harmful first. Saathi may ask about
    the top two; the owner can skip any of them."""
    owner_or_404(owner_id)
    return {"gaps": gaps.find(owner_id)}


@api.post("/owners/{owner_id}/gaps/{key}/skip", dependencies=[Depends(demo.only("owner"))])
def skip_gap(owner_id: str, key: str) -> dict:
    """The owner chose not to answer this one: Saathi never raises it again."""
    owner_or_404(owner_id)
    try:
        gaps.skip(owner_id, key)
    except KeyError as e:
        raise HTTPException(404, "gap not found") from e
    return {"gaps": gaps.find(owner_id)}


# ---- Readiness and planning loops ---------------------------------------------


# Caching (PR-10.3): these loops call Gemini only when their inputs have changed since the
# last run; otherwise the saved result comes back at no cost.


def _fingerprint(inputs) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def _cached(loop: str, owner_id: str, inputs) -> dict | None:
    doc = store.get("loop_cache", f"{loop}:{owner_id}")
    return doc["result"] if doc and doc["fingerprint"] == _fingerprint(inputs) else None


def _remember(loop: str, owner_id: str, inputs, result: dict) -> None:
    store.put("loop_cache", {"id": f"{loop}:{owner_id}", "owner_id": owner_id, "loop": loop,
                             "fingerprint": _fingerprint(inputs), "result": result, "at": time.time()})


def _readiness_inputs(owner_id: str) -> tuple[dict, list]:
    owner = owner_or_404(owner_id)
    recipient = owner.get("vault_recipient") or {}
    args = [
        {k: v for k, v in owner.items() if k != "readiness_score"},
        knowledge.items(owner_id, "inventory"),
        knowledge.items(owner_id, "moments"),
        [v["ai_label"] for v in store.list("vault_items", owner_id=owner_id) if v.get("ai_label")],
        [d["document_type"] for d in store.list("legal_documents", owner_id=owner_id)],
        store.list("tasks", owner_id=owner_id),
        {"items": len(store.list("vault_items", owner_id=owner_id)), "recipient": recipient.get("status"),
         "sealed": bool(owner.get("vault_escrow"))},
    ]
    return owner, args


@api.post("/owners/{owner_id}/readiness", dependencies=[Depends(demo.only("owner"))])
async def readiness(owner_id: str) -> dict:
    """Readiness loop: score, plus next steps that stay on the owner's list until done."""
    owner, args = _readiness_inputs(owner_id)
    if (cached := _cached("readiness", owner_id, args)) is not None:
        return {**cached, "tasks": _labelled_steps(owner_id), "cached": True}
    result = await loops.assess_readiness(owner, *args[1:])
    # Off the event loop: each step is checked and written down by the guardian (PR-2.7).
    await asyncio.to_thread(tasks.refresh, owner_id, [g.model_dump() for g in result.gaps])
    await asyncio.to_thread(planner.refresh, owner_id, {"kind": "readiness"})  # suggestions feed only the plan
    store.update("owners", owner_id, {"readiness_score": result.score})
    # Running readiness updates the next steps, which are one of its own inputs, so remember
    # the inputs as they are now; an unchanged owner then gets the saved result next time.
    _remember("readiness", owner_id, _readiness_inputs(owner_id)[1], {"autonomy": ACT, "score": result.score})
    return {"autonomy": ACT, "score": result.score, "tasks": _labelled_steps(owner_id)}


def _labelled_steps(owner_id: str) -> list[dict]:
    """The owner's open next steps, each with its agent's autonomy label (PR-15.7)."""
    label = governance_core.autonomy(STEPS)
    return [{**t, "autonomy": label} for t in tasks.open_tasks(owner_id)]


@api.get("/owners/{owner_id}/plan", dependencies=[Depends(demo.only("owner"))])
def get_plan(owner_id: str) -> dict:
    """The conversation plan (PR-2.12.2): the current step, who starts it, and the next ones. The
    only source of what Saathi talks about; worked out on a cold start or when it's over an hour old."""
    owner_or_404(owner_id)
    return planner.view(planner.current(owner_id))


@api.get("/owners/{owner_id}/tasks")
def list_tasks(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    return {"score": owner.get("readiness_score"), "tasks": _labelled_steps(owner_id)}


class TaskUpdate(BaseModel):
    status: Literal["done", "dismissed"]


@api.post("/tasks/{task_id}", dependencies=[Depends(demo.only("owner"))])
def update_task(task_id: str, body: TaskUpdate) -> dict:
    if not demo.mine("tasks", task_id):
        raise HTTPException(404, "task not found")
    return store.update("tasks", task_id, {"status": body.status, "closed_at": time.time()})


class Prompt(BaseModel):
    text: str
    step_id: str | None = None  # the plan step this opens, from the Talk screen (PR-2.12.3)


@api.post("/owners/{owner_id}/conversation/prompt", dependencies=[Depends(demo.only("owner"))])
def companion_asks(owner_id: str, body: Prompt) -> dict:
    """Start a topic from a next step: the companion's question appears in the chat,
    and the next typed message or live call carries on from it. The text comes from the app, so it
    gets the same checks as a next step (PR-13.3) and as anything Saathi says (no death words)."""
    owner_or_404(owner_id)
    if step_guard.reply_problem(body.text) or tone.about_their_death(body.text):
        raise HTTPException(422, "Saathi can't start with that.")
    turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": time.time(),
                               "role": "assistant", "text": body.text, "language": None})
    # When it opens the plan's current step (PR-2.12.3), the next answer is judged against that step.
    step = planner.view(planner.current(owner_id))["current"] if body.step_id else None
    step_id = step["id"] if step and step["id"] == body.step_id else None
    gap = step_id.removeprefix("gap:") if step_id and step_id.startswith("gap:") else None
    store.update("owners", owner_id, {"last_said": {"text": body.text, "turn_id": turn["id"], "at": turn["at"],
                                                    "gap": gap, "step": step_id}})
    return turn


class TurnIds(BaseModel):
    turn_ids: list[str] = Field(min_length=1, max_length=400)


def _my_turns(owner_id: str, turn_ids: list[str]) -> list[dict]:
    """The given turns, only those in the caller's own conversation, oldest first."""
    found = (demo.mine("turns", tid) for tid in dict.fromkeys(turn_ids))
    return sorted((t for t in found if t and t["owner_id"] == owner_id), key=lambda t: t["at"])


@api.post("/owners/{owner_id}/conversation/summary", dependencies=[Depends(demo.only("owner"))])
async def summarize_conversation(owner_id: str, body: TurnIds) -> dict:
    """A few sentences that sum up a conversation that just ended. Shown, never stored or logged;
    numbers are masked in code, whatever the model wrote."""
    owner = owner_or_404(owner_id)
    turns = _my_turns(owner_id, body.turn_ids)
    if not turns:
        raise HTTPException(404, "There's nothing in this conversation to sum up.")
    result = await loops.summarize_conversation(owner, turns)
    return {"autonomy": "assist", "summary": knowledge.mask(result.summary.strip())}


@api.post("/owners/{owner_id}/conversation/forget", dependencies=[Depends(demo.only("owner"))])
def forget_conversation(owner_id: str, body: TurnIds) -> dict:
    """The owner discards a conversation: its turns are deleted, with every card from it still
    waiting for Keep. Anything already kept stays: removing that is a separate, deliberate step
    (DELETE /kept). Saathi no longer carries on from what was deleted."""
    owner_or_404(owner_id)
    turns = _my_turns(owner_id, body.turn_ids)
    gone = {t["id"] for t in turns}
    # A waiting card mentioned again in another conversation keeps the same id: it stays for that one.
    elsewhere = {doc_id for t in store.list("turns", owner_id=owner_id) if t["id"] not in gone
                 for ids in (t.get("proposals") or {}).values() for doc_id in ids}
    items, files = 0, {t["media"] for t in turns if t.get("media")}
    for turn in turns:
        for collection, ids in (turn.get("proposals") or {}).items():
            if collection not in ("moments", "inventory", "people"):
                continue
            for doc_id in ids:
                doc = demo.mine(collection, doc_id) if doc_id not in elsewhere else None
                if doc and doc.get("status") == "pending":
                    files |= {doc[k] for k in ("recording", "document") if isinstance(doc.get(k), str)}
                    store.delete(collection, doc_id)
                    items += 1
        store.delete("turns", turn["id"])
    gaps.forget_turns(owner_id, gone)  # its questions no longer count as asked
    # The shared photos and documents, and Saathi's cached spoken replies, go too, unless
    # something still uses them: a kept item, another waiting card, or a reply with the same words.
    in_use = json.dumps([store.list(c, owner_id=owner_id) for c in ("moments", "inventory", "turns")]
                        + [knowledge.items(owner_id, c) for c in ("moments", "inventory")], ensure_ascii=False, default=str)
    for path in files:
        if json.dumps(path)[1:-1] not in in_use:
            media.delete_path(owner_id, path)
    spoken = {t["text"] for t in store.list("turns", owner_id=owner_id) if t.get("role") == "assistant"}
    for turn in turns:
        if turn.get("role") == "assistant" and turn.get("text") and turn["text"] not in spoken:
            media.delete(owner_id, _speech_name(turn["text"]))
    return {"deleted_turns": len(turns), "deleted_items": items}


@api.post("/owners/{owner_id}/legal-needs", dependencies=[Depends(demo.only("owner"))])
async def legal_needs(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    args = [knowledge.items(owner_id, "moments", kind="wish"),
            knowledge.items(owner_id, "inventory"),
            bool(store.list("vault_items", owner_id=owner_id))]
    if (cached := _cached("legal_needs", owner_id, args)) is not None:
        return {**cached, "cached": True}
    result = {"autonomy": PROPOSE, **(await loops.find_legal_needs(owner, *args)).model_dump()}
    _remember("legal_needs", owner_id, args, result)
    return result


class Handoff(BaseModel):
    partner: str
    owner_consent: bool
    needs: list[dict]


@api.post("/owners/{owner_id}/legal-handoff", dependencies=[Depends(demo.only("owner"))])
def legal_handoff(owner_id: str, body: Handoff) -> dict:
    """Send a pre-filled summary to a legal partner. Requires the owner's explicit consent.

    The summary never includes vault contents. In this MVP the partner is mocked;
    the payload is what a real partner integration would receive.
    """
    if not body.owner_consent:
        raise HTTPException(400, "the owner must consent before anything is shared with a legal partner")
    owner = owner_or_404(owner_id)
    summary = {
        "id": new_id("handoff"),
        "owner_id": owner_id,
        "partner": body.partner,
        "sent_at": time.time(),
        "client": {k: owner.get(k) for k in ("name", "age", "country", "family")},
        "requests": body.needs,
        "assets_overview": [
            {k: i.get(k) for k in ("category", "institution", "product", "nominee_or_beneficiary")}
            for i in knowledge.items(owner_id, "inventory")
        ],
    }
    store.put("handoffs", summary)
    return {"autonomy": PROPOSE, "status": "sent to partner (mock)", "summary": summary}


@api.post("/owners/{owner_id}/legal-documents", dependencies=[Depends(demo.only("owner"))])
def record_legal_document(owner_id: str, document_type: str = Form(...), location: str = Form("")) -> dict:
    owner_or_404(owner_id)
    return store.put("legal_documents", {"id": new_id("legal"), "owner_id": owner_id,
                                         "document_type": document_type, "location": location})


# ---- Secure vault -------------------------------------------------------------
# The server stores only ciphertext and a key blob it cannot open (see escrow.py).


class KeyBackup(BaseModel):
    """The recipient's private key, encrypted on their device with a key derived
    from their recovery code. Useless to us without that code."""

    kdf: Literal["PBKDF2-SHA256"]
    iterations: int
    salt: str
    iv: str
    ciphertext: str


class Recipient(BaseModel):
    name: str
    relationship: str
    public_key_jwk: dict
    key_backup: KeyBackup


class Invite(BaseModel):
    name: str
    relationship: str


@api.post("/owners/{owner_id}/vault/invite", dependencies=[Depends(demo.only("owner"))])
def invite_recipient(owner_id: str, body: Invite) -> dict:
    """The owner chooses who receives the vault. Choosing someone new replaces the old choice. A
    change asks the guardian first (PR-2.7.4): a second change within a day waits for a risk
    reviewer, and the owner sees that in the app."""
    owner = owner_or_404(owner_id)
    if open_case(owner_id):
        raise HTTPException(409, "The vault recipient can't be changed while a release case is open.")
    if guardian.waiting(owner_id, "recipient_change"):  # one at a time, so the order is never in doubt
        raise HTTPException(409, guardian.HELD_MESSAGES["recipient_change"]["held"])
    previous = owner.get("vault_invite") or {}
    if previous and previous.get("name") != body.name:  # a change, not the first choice
        last_change = owner.get("vault_recipient_changed_at") or 0
        decision = guardian.decide("recipient_change", OWNER_ACTS,
                                   {"changed_recently": time.time() - last_change < 86400,
                                    "replaces_sealed": bool(owner.get("vault_escrow"))})
        if not decision.goes_ahead:
            # What it changes from, so a reviewer's later "reverse" never undoes a newer choice.
            guardian.hold(decision, "recipient_change", owner_id,
                          {"owner_id": owner_id, "from": previous.get("name"), "invite": body.model_dump()})
            return {"autonomy": PROPOSE, "held": True, "message": guardian.HELD_MESSAGES["recipient_change"]["held"]}
    _apply_invite(owner, body.model_dump())
    return {"autonomy": PROPOSE, "invited": body.name}


def _apply_invite(owner: dict, invite: dict) -> None:
    previous, current = owner.get("vault_invite") or {}, owner.get("vault_recipient") or {}
    changes: dict = {"vault_invite": {**invite, "at": time.time()}}
    if previous and previous.get("name") != invite["name"]:
        changes["vault_recipient_changed_at"] = time.time()
    if current and current.get("name") != invite["name"]:
        changes.update(vault_recipient=None, vault_recipient_name=None, vault_escrow=None)
    store.update("owners", owner["id"], changes)


def _finish_recipient_change(payload: dict) -> None:
    """A reviewer reversed the hold: make the change, but only if the owner's choice is still the
    one it changes from, and no release case has opened since. Read plainly, so no release decision
    starts from the reviewer's request."""
    owner = store.get("owners", payload["owner_id"])
    if not owner or (owner.get("vault_invite") or {}).get("name") != payload.get("from"):
        return
    if any(c["state"] != "cancelled" for c in store.list("cases", owner_id=owner["id"])):
        return
    _apply_invite(owner, payload["invite"])


@api.get("/owners/{owner_id}/vault/invite")
def get_invite(owner_id: str) -> dict:
    invite = owner_or_404(owner_id).get("vault_invite")
    if not invite:
        raise HTTPException(404, "the owner hasn't chosen a vault recipient yet")
    return invite


@api.post("/owners/{owner_id}/vault/recipient", dependencies=[Depends(demo.only("family"))])
def set_recipient(owner_id: str, body: Recipient) -> dict:
    """The invited person accepts and registers their key. A new key (e.g. recovery code lost)
    means the owner's device must re-seal the vault, so only allowed while no case is open."""
    owner = owner_or_404(owner_id)
    invite = owner.get("vault_invite")
    if not invite:
        raise HTTPException(409, "Only the person the owner has chosen can become the vault recipient.")
    # Only the invited person registers, as themselves: the person acting must be the one invited.
    def first_name(name: str) -> str:
        return (name.split() or [""])[0].casefold()

    if not first_name(invite["name"]) or first_name(invite["name"]) != first_name(demo.person()["name"]):
        raise HTTPException(409, f"Vikram chose {invite['name']}, not you, to receive his vault.")
    if owner.get("vault_recipient") and open_case(owner_id):
        raise HTTPException(409, "The vault can no longer be re-sealed. Restore the existing key with the recovery code.")
    # The key still waits for the owner to confirm the matching code before the vault is sealed for it.
    recipient = {**body.model_dump(), "name": invite["name"], "relationship": invite["relationship"],
                 "status": "pending_owner"}
    store.update("owners", owner_id, {"vault_recipient_name": invite["name"], "vault_recipient": recipient,
                                      "vault_escrow": None})
    return {"autonomy": PROPOSE, "recipient": invite["name"], "status": "pending_owner"}


@api.get("/owners/{owner_id}/vault/recipient")
def get_recipient(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    recipient = owner.get("vault_recipient")
    if not recipient:
        raise HTTPException(404, "no vault recipient yet")
    return {**recipient, "vault_sealed": bool(owner.get("vault_escrow"))}


class RecipientDecision(BaseModel):
    decision: Literal["approve", "decline"]


@api.post("/owners/{owner_id}/vault/recipient/decision", dependencies=[Depends(demo.only("owner"))])
def decide_recipient(owner_id: str, body: RecipientDecision) -> dict:
    """Only the owner can accept someone as vault recipient."""
    owner = owner_or_404(owner_id)
    recipient = owner.get("vault_recipient")
    if not recipient or recipient.get("status") != "pending_owner":
        raise HTTPException(404, "no recipient waiting for approval")
    if body.decision == "decline":
        store.update("owners", owner_id, {"vault_recipient": None, "vault_recipient_name": None})
        return {"status": "declined"}
    store.update("owners", owner_id, {"vault_recipient": {**recipient, "status": "approved"}})
    return {"status": "approved"}


class Escrow(BaseModel):
    recipient_wrapped_key: str  # base64; the data key wrapped with the recipient's public key


@api.post("/owners/{owner_id}/vault/escrow", dependencies=[Depends(demo.only("owner"))])
def set_escrow(owner_id: str, body: Escrow) -> dict:
    owner = owner_or_404(owner_id)
    if (owner.get("vault_recipient") or {}).get("status") != "approved":
        raise HTTPException(409, "Approve the vault recipient before sealing the vault for them.")
    store.update("owners", owner_id, {"vault_escrow": escrow.wrap(body.recipient_wrapped_key)})
    return {"stored": True, "kms": bool(settings.kms_key_name)}


class VaultItem(BaseModel):
    category: str
    iv: str
    ciphertext: str
    ai_label: str | None = None  # optional, owner-approved label the AI may see


@api.post("/owners/{owner_id}/vault/items", dependencies=[Depends(demo.only("owner"))])
def add_vault_item(owner_id: str, body: VaultItem) -> dict:
    owner_or_404(owner_id)
    return store.put("vault_items", {"id": new_id("vault"), "owner_id": owner_id, **body.model_dump()})


@api.get("/owners/{owner_id}/vault/items")
def list_vault_items(owner_id: str) -> dict:
    return {"items": store.list("vault_items", owner_id=owner_id)}


# ---- Activation and vault release (Assist: a person decides) ------------------


@api.post("/owners/{owner_id}/death-report", dependencies=[Depends(demo.only("family"))])
async def report_death(owner_id: str, certificate: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    if open_case(owner_id):
        raise HTTPException(409, "a case is already open")
    data = await certificate.read()
    expected = "death certificate"
    key = fraud_guides.key_for(expected, owner.get("country") or "India")
    guide = fraud_guides.current(key) if key else None
    check = await loops.check_document(data, certificate.content_type or "image/jpeg",
                                       expected, owner["name"], guide)
    case = release.new_case(new_id("case"), owner_id, loops.review_summary(check, guide))
    case["reported_by"] = demo.person()["name"]
    case = store.put("cases", case)
    _record_signs(case, case["certificate_check"], "certificate")  # once the case is saved
    return {"autonomy": ASSIST, "case": case}


# Document checks and reviewer confirmations ask the guardian (PR-2.7): it records each one, and
# warning signs or an override become an event. Only the case id and the fraud guide's sign ids go
# in; never what a document says, a reviewer's name or their written reason.
CHECKER = loops.CHECKER


def _record_signs(case: dict, check: dict, what: str) -> None:
    found = [s["id"] for s in check.get("signs") or [] if s.get("result") == "found"]
    _after_the_fact("certificate_check", {"signs_found": found, "what": what}, case)


def _record_override(case: dict, check_field: str, what: str, override_reason: str | None) -> None:
    """Called once a confirmation is saved. release._check_override let it through against the
    AI's "not valid" only with a written reason."""
    override = bool(override_reason) and (case.get(check_field) or {}).get("verdict") == "not_valid"
    _after_the_fact("review_confirmation", {"override": override, "what": what}, case)


def _after_the_fact(action: str, facts: dict, case: dict) -> None:
    """These ask the guardian after the case is saved, so a failed record must not turn a saved
    report or confirmation into an error the person would retry. It's logged instead, without text."""
    try:
        guardian.decide(action, CHECKER, facts, refs={"case": case["id"]}, case_key=case["id"])
    except Exception:
        log.exception("guardian record failed: %s for %s", action, case["id"])


@api.post("/owners/{owner_id}/release/authority", dependencies=[Depends(demo.only("family"))])
async def submit_authority(owner_id: str, proof: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    case = open_case(owner_id)
    if not case:
        raise HTTPException(404, "no open case")
    data = await proof.read()
    check = await loops.check_document(
        data, proof.content_type or "image/jpeg",
        "proof of legal authority (e.g. letters testamentary, probate or succession certificate)",
        owner.get("vault_recipient_name") or "the named vault recipient",
    )
    try:
        release.attach_authority_check(case, loops.review_summary(check))
    except release.ReleaseError as e:
        raise HTTPException(409, str(e)) from e
    return {"autonomy": ASSIST, "case": store.put("cases", case)}


@api.get("/admin/cases")
def list_cases() -> dict:
    cases = [_refresh(c) for c in store.list("cases", owner_id=demo.owner_id())]
    for c in cases:
        store.put("cases", c)
    return {"cases": cases}


class Review(BaseModel):
    reviewer: str | None = None  # ignored: the reviewer is the person acting (PR-1.4)
    override_reason: str | None = None


def _reviewer() -> str:
    """The reviewer is whoever is acting, from the switcher, never a typed name, so the
    two-person rule compares real identities."""
    return demo.person()["name"]


@api.post("/admin/cases/{case_id}/confirm-death", dependencies=[Depends(demo.only("reviewer"))])
def confirm_death(case_id: str, body: Review) -> dict:
    out = _transition(case_id, lambda c: release.confirm_death(c, _reviewer(), body.override_reason))
    _record_override(out["case"], "certificate_check", "death certificate", body.override_reason)
    return out


@api.post("/admin/cases/{case_id}/confirm-authority", dependencies=[Depends(demo.only("reviewer"))])
def confirm_authority(case_id: str, body: Review) -> dict:
    out = _transition(case_id, lambda c: release.confirm_authority(c, _reviewer(), body.override_reason))
    _record_override(out["case"], "authority_check", "proof of legal authority", body.override_reason)
    return out


@api.post("/owners/{owner_id}/alive", dependencies=[Depends(demo.only("owner"))])
def owner_alive(owner_id: str) -> dict:
    """The owner's device answers the alert: stop everything."""
    case = open_case(owner_id)
    if not case:
        return {"status": "no open case"}
    out = _transition(case["id"], release.owner_responded)
    if case.get("release_hold"):  # nothing left to release: the owner no longer sees it as waiting
        guardian.close(case["release_hold"])
    return out


def _transition(case_id: str, step) -> dict:
    case = demo.mine("cases", case_id)
    if not case:
        raise HTTPException(404, "case not found")
    try:
        case = step(case)
    except release.ReleaseError as e:
        raise HTTPException(409, str(e)) from e
    return {"case": store.put("cases", case)}


@api.get("/owners/{owner_id}/release")
def release_status(owner_id: str) -> dict:
    return {"case": open_case(owner_id)}


@api.get("/owners/{owner_id}/guardian/held")
def guardian_held(owner_id: str) -> dict:
    """The owner's in-app notice (PR-2.7): actions the guardian is holding for a risk reviewer,
    and ones a reviewer stopped this week. Only the action and a fixed message, nothing else."""
    owner_or_404(owner_id)
    return {"held": guardian.held_for(owner_id)}


@api.get("/owners/{owner_id}/vault/release-bundle", dependencies=[Depends(demo.only("family"))])
def release_bundle(owner_id: str) -> dict:
    """Only after release: the recipient-wrapped data key plus the encrypted items.

    The recipient's device unwraps and decrypts locally; this server never can.
    """
    require_handover(owner_id)
    owner = owner_or_404(owner_id)
    if not owner.get("vault_escrow"):
        raise HTTPException(404, "the owner never set up a vault key")
    return {
        "recipient_wrapped_key": escrow.unwrap(owner["vault_escrow"]),
        "items": store.list("vault_items", owner_id=owner_id),
    }


# ---- Handover and settlement loops ----------------------------------------------


class Question(BaseModel):
    question: str
    session_id: str | None = None


@api.post("/owners/{owner_id}/ask", dependencies=[Depends(demo.only("family"))])
async def ask_question(owner_id: str, body: Question) -> dict:
    require_handover(owner_id)
    owner = owner_or_404(owner_id)
    # The question asks the guardian first (PR-2.7.4, PR-2.12.4). Getting what the owner kept stays
    # easy: only a vault secret or words aimed at the assistant are stopped, without any model call.
    # A risky pattern adds a helpful note to the answer, or a quiet look for our team.
    facts = guardian_inputs.family_question_facts(body.question)
    decision = guardian.decide("family_question", ASSISTANT, facts)
    first = (owner.get("name") or "the owner").split()[0]
    if not decision.goes_ahead:
        answer = (f"I can't help with passwords, PINs or codes. Anything {first} kept in the vault is "
                  f"released separately to the person {first} named, and I can't see it." if facts["asks_secret"]
                  else f"I can only answer questions about what {first} kept.")
        # Recorded like any other turn (PR-15.8), so a review sees the attempt.
        session_id = await asyncio.to_thread(executor_agent.record_blocked, owner_id, f"family:{demo.current()}",
                                             body.question, answer, body.session_id)
        return {"autonomy": ACT, "blocked": True, "sources": [], "tools_used": [], "session_id": session_id,
                "answer": answer}
    try:
        # The asker is the family within the caller's own copy, so sessions never cross copies.
        result = await ask(owner, f"family:{demo.current()}", body.question, body.session_id)
    except UnknownSession:
        raise HTTPException(404, "conversation not found") from None
    # The answer asks the guardian too, before it's shown: full numbers masked, and replaced only if
    # it would help pass on a secret or pay someone who asked. Notes follow the answer.
    answer, answer_facts = guardian_inputs.family_answer(result["answer"])
    checked = guardian.decide("family_answer", ASSISTANT, answer_facts, refs={"question": decision.action_id})
    if not checked.goes_ahead:
        answer, result["sources"] = f"I can't help with that safely. Please ask about what {first} kept.", []
    notes = guardian_inputs.family_notes(facts, owner.get("name") or "") if checked.goes_ahead else []
    return {"autonomy": ACT, **result, "answer": answer, "notes": notes}


@api.post("/owners/{owner_id}/settlement", dependencies=[Depends(demo.only("family"))])
async def settlement(owner_id: str) -> dict:
    require_handover(owner_id)
    owner = owner_or_404(owner_id)
    args = [knowledge.items(owner_id, "inventory"),
            knowledge.items(owner_id, "moments")]
    if (cached := _cached("settlement", owner_id, args)) is not None:
        return {**cached, "cached": True}
    result = {"autonomy": PROPOSE, **(await loops.plan_settlement(owner, *args)).model_dump()}
    _remember("settlement", owner_id, args, result)
    return result


# ---- Access requests: family members the owner never listed ----------------------
# While the owner is alive, only the owner can let someone in. After a death, our
# team checks proof of relationship. Either way, access covers the archive and the
# handover assistant; the vault still needs the legal-authority release.

NEUTRAL_REPLY = "Thank you. If this person uses Anvaya, your request has been passed on and you'll hear back."


class AccessRequest(BaseModel):
    name: str
    relationship: str
    contact: str
    note: str = ""


@api.post("/owners/{owner_id}/access-requests", dependencies=[Depends(demo.only("family"))])
def request_access(owner_id: str, body: AccessRequest) -> dict:
    # Same reply either way, so a stranger learns nothing about who uses the service. In the demo
    # a request is kept only for the caller's own copy (PR-1.6).
    owner = store.get("owners", owner_id) if owner_id == demo.owner_id() else None
    if owner:
        after_death = open_case(owner_id) is not None
        store.put("access_requests", {
            "id": new_id("req"), "owner_id": owner_id, "at": time.time(), **body.model_dump(),
            "status": "pending_review" if after_death else "pending_owner",
        })
    return {"message": NEUTRAL_REPLY}


@api.get("/owners/{owner_id}/access-requests")
def owner_access_requests(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    return {"requests": store.list("access_requests", owner_id=owner_id, status="pending_owner"),
            "contacts": owner.get("contacts", [])}


@api.get("/admin/access-requests")
def team_access_requests() -> dict:
    return {"requests": store.list("access_requests", owner_id=demo.owner_id(), status="pending_review")}


class AccessDecision(BaseModel):
    decision: Literal["approve", "decline"]
    decided_by: Literal["owner", "team"]


@api.post("/access-requests/{request_id}/decision")
def decide_access(request_id: str, body: AccessDecision) -> dict:
    if demo.person()["role"] != {"owner": "owner", "team": "reviewer"}[body.decided_by]:
        raise HTTPException(403, "Only Vikram decides while he's here; after that, a reviewer decides.")
    req = demo.mine("access_requests", request_id)
    expected = {"owner": "pending_owner", "team": "pending_review"}[body.decided_by]
    if not req or req["status"] != expected:
        raise HTTPException(404, "no request waiting for this decision")
    if body.decision == "decline":
        return store.update("access_requests", request_id, {"status": "declined"})
    owner = owner_or_404(req["owner_id"])
    contact = {k: req[k] for k in ("name", "relationship", "contact")} | {
        "access": "archive_and_handover", "approved_by": body.decided_by}
    store.update("owners", owner["id"], {"contacts": [*owner.get("contacts", []), contact]})
    return store.update("access_requests", request_id, {"status": "approved"})


@app.get("/api/healthz")
def healthz() -> dict:
    return {"ok": True, "store": settings.store, "kms": bool(settings.kms_key_name), "auth": settings.auth_required}


@app.post("/internal/fraud/cycle")
async def fraud_cycle(request: Request) -> dict:
    """The hourly fraud cycle (Cloud Scheduler, scheduler.py). Outside /api: no person signs in here;
    only the scheduler's own service account, by its Google-signed ID token. The runs happen inside
    this request, so Cloud Run keeps the CPU on until they finish."""
    await asyncio.to_thread(scheduler.verify, request.headers.get("authorization", ""), auth._request)
    return {"runs": await fraud_pipeline.cycle()}


@api.get("/agents/steps")
def my_agent_steps() -> dict:
    """The agents panel (PR-15.7): the signed-in person's own recent AI steps, a simple trace with no
    text. The copy comes from the verified sign-in, never from the request."""
    return governance_core.agent_steps(governance_core.current_user())


@api.get("/allowance")
def my_allowance() -> dict:
    """Today's use against the signed-in person's daily allowance (PR-10.4), so the app can
    warn near the limit."""
    user = governance_core.current_user()
    return {"pools": governance_core.allowance_status(), "warn_at": governance_core.WARN_AT,
            "requests": governance_core.my_requests(user) if user else [],
            "limits": governance_core.REQUEST_LIMITS}


class MoreRequest(BaseModel):
    kind: Literal["daily_allowance", "voice_minutes", "spoken_replies"]
    amount: int
    hours: int = 24
    reason: str = Field(min_length=3, max_length=300)


@api.post("/allowance/request")
def ask_for_more(body: MoreRequest) -> dict:
    """Ask for more of today's allowance (PR-10.6). A platform admin, or in a demo the copy's own
    demo admin, decides in the admin portal; approval raises the allowance until it expires."""
    user = governance_core.current_user()
    if not user:
        raise HTTPException(401, "Please sign in.")
    try:
        req = governance_core.request_more(user, body.kind, body.amount, body.hours, body.reason,
                                           requested_by=demo.person()["name"], copy_id=demo.current())
    except governance_core.RequestRefused as e:
        raise HTTPException(409, str(e)) from e
    return {"request": {k: req[k] for k in ("id", "at", "kind", "amount", "hours", "status")}}


@api.get("/me")
def me(request: Request) -> dict:
    """Who is signed in, and the owner in their own demo copy (the app calls this right after
    sign-in, which also creates the copy on first use)."""
    user = getattr(request.state, "user", None)
    demo.ensure()
    copy = demo.copy_record()
    return {"email": user.email if user else None, "name": user.name if user else None,
            "auth": settings.auth_required, "owner_id": demo.owner_id(),
            "people": [{"id": pid, **p} for pid, p in demo.PEOPLE.items()], "acting_as": demo.person()["id"],
            "nickname": copy.get("nickname"), "ask_nickname": not copy.get("nickname_asked")}


class Nickname(BaseModel):
    nickname: str | None = None  # None or empty: skip


@api.post("/me/nickname")
def set_nickname(body: Nickname) -> dict:
    """The optional name for the caller's demo copy, so the Anvaya team can tell copies apart.
    Shown only to platform admins; never logged."""
    return {"nickname": demo.set_nickname(body.nickname)}


api.include_router(portal.router)
api.include_router(model_access.router)
app.include_router(api)

# How a held action finishes when a risk reviewer reverses its hold, and what "confirm" does (PR-2.7.6).
guardian.on_review("release", _finish_release, _cancel_release)
guardian.on_review("recipient_change", _finish_recipient_change, lambda payload: None)  # confirmed: not made
