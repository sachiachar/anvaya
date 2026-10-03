"""HTTP API for the Anvaya app. Every route is under /api so Firebase Hosting
can proxy it to Cloud Run on the same origin as the web app.

Autonomy level of each action is returned as `autonomy` so the UI can show it.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import time
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from google.auth.exceptions import DefaultCredentialsError
from google.genai import errors as genai_errors
from pydantic import BaseModel, ValidationError

from . import auth, escrow, live, loops, media, proposals, release, speech, tasks
from .config import settings
from .executor_agent import ask
from .governance import core as governance_core
from .governance import portal
from .store import new_id, store

app = FastAPI(title="Anvaya agents", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Every HTTP route needs a signed-in, allowed user (when AUTH_REQUIRED=true).
# The live-call WebSocket checks its own token (see live_call); /healthz stays open.
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


# ---- Gemini failures: readable errors instead of a bare 500 --------------------


@app.exception_handler(DefaultCredentialsError)
async def no_credentials(_: Request, exc: DefaultCredentialsError) -> JSONResponse:
    return JSONResponse(status_code=503, content={
        "detail": "Gemini is not configured: no Google Cloud credentials. Run "
                  "'gcloud auth application-default login', or set GOOGLE_API_KEY in agents/.env."})


@app.exception_handler(genai_errors.APIError)
async def gemini_error(_: Request, exc: genai_errors.APIError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"Gemini error {exc.code}: {exc.message}"})


@app.exception_handler(governance_core.Refused)
async def refused_by_gateway(_: Request, exc: governance_core.Refused) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
async def bad_model_output(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=502, content={
        "detail": "Gemini returned output that did not match the expected format. Try again."})

ASSIST, PROPOSE, ACT = "assist", "propose", "act_within_limits"


def owner_or_404(owner_id: str) -> dict:
    owner = store.get("owners", owner_id)
    if not owner:
        raise HTTPException(404, "owner not found")
    return owner


def open_case(owner_id: str) -> dict | None:
    cases = [c for c in store.list("cases", owner_id=owner_id) if c["state"] != "cancelled"]
    if not cases:
        return None
    case = release.refresh(max(cases, key=lambda c: c["history"][0]["at"]))
    store.put("cases", case)
    return case


def require_handover(owner_id: str) -> None:
    if not release.is_released(open_case(owner_id)):
        raise HTTPException(403, "available only after the death is confirmed and checks are complete")


# ---- Demo setup -------------------------------------------------------------


@api.post("/demo/seed")
def seed_demo() -> dict:
    """Load the synthetic demo persona. Never use real personal data in demos."""
    path = pathlib.Path(__file__).resolve().parents[1] / "demo_data" / "persona.json"
    data = json.loads(path.read_text())
    owner = data["owner"]
    for collection in ("moments", "inventory", "people", "vault_items", "cases", "legal_documents", "turns",
                       "access_requests", "handoffs", "tasks", "loop_cache"):
        for doc in store.list(collection, owner_id=owner["id"]):
            store.delete(collection, doc["id"])
    store.put("owners", {**owner, "readiness_score": None})
    for m in data["moments"]:
        store.put("moments", {**m, "owner_id": owner["id"], "status": "approved"})
    for item in data["inventory"]:
        store.put("inventory", {**item, "owner_id": owner["id"], "status": "approved"})
    return {"owner": owner}


@api.get("/owners/{owner_id}")
def get_owner(owner_id: str) -> dict:
    return owner_or_404(owner_id)


# ---- Capture loop -------------------------------------------------------------


@api.post("/owners/{owner_id}/capture/audio")
async def capture_audio(owner_id: str, audio: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    data = await audio.read()
    mime = (audio.content_type or "audio/webm").split(";")[0]
    recording = media.save(owner_id, f"{new_id('rec')}.{mime.split('/')[-1]}", data, mime)
    result = await loops.capture_audio(data, mime, owner)
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
    """Replace proposal ids with the items and their current status (discarded ones drop out)."""
    ids = turn.get("proposals")
    if not ids:
        return turn
    items = [{**doc, "collection": c} for c in ("people", "moments", "inventory")
             for doc in (store.get(c, i) for i in ids.get(c, [])) if doc]
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


@api.post("/owners/{owner_id}/conversation")
async def converse(owner_id: str, text: str | None = Form(None), file: UploadFile | None = File(None)) -> dict:
    """One turn: the owner talks, types, or shares a photo or document. The AI replies,
    and anything worth keeping comes back as proposals the owner can keep or discard."""
    owner = owner_or_404(owner_id)
    if not (text and text.strip()) and not file:
        raise HTTPException(400, "say something, type something, or attach a file")

    upload, attachment, source = None, None, {}
    if file:
        data = await file.read()
        mime = (file.content_type or "application/octet-stream").split(";")[0]
        kind = _attachment_kind(mime)
        path = media.save(owner_id, f"{new_id(kind)}.{mime.split('/')[-1]}", data, mime)
        upload = (data, mime)
        attachment = {"kind": kind, "name": file.filename, "mime": mime}
        source = {"recording": path} if kind == "voice" else {"document": path}

    history = sorted(store.list("turns", owner_id=owner_id), key=lambda t: t["at"])
    result = await loops.converse(owner, history, text, upload, proposals.known(owner_id))
    store.update("owners", owner_id, {"language": result.owner_language})

    now = time.time()
    moments = [d for m in result.moments if (d := proposals.save_moment(owner_id, m.model_dump(), source))]
    inventory = [proposals.save_inventory(owner_id, i.model_dump(), source) for i in result.inventory]
    people = [d for p in result.people if (d := proposals.save_person(owner_id, p.model_dump()))]
    owner_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now, "role": "owner",
                                     "text": (text or "").strip() or result.heard, "heard": result.heard,
                                     "attachment": attachment})
    ai_turn = store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": now + 0.001,
                                  "role": "assistant", "text": result.reply, "language": result.reply_language,
                                  "proposals": {"moments": [m["id"] for m in moments],
                                                "inventory": [i["id"] for i in inventory],
                                                "people": [p["id"] for p in people]}})
    return {"autonomy": ACT, "owner_turn": owner_turn, "assistant_turn": _expand(ai_turn)}


@app.websocket("/api/owners/{owner_id}/live")
async def live_call(ws: WebSocket, owner_id: str, token: str | None = None) -> None:
    """Two-way voice call with the companion (Gemini Live API). See live.py for the protocol.
    Browsers can't send headers on a WebSocket, so the sign-in token comes as ?token=."""
    if settings.auth_required:
        try:
            auth.verify(token)
        except HTTPException as e:
            await ws.close(code=4401 if e.status_code == 401 else 4403, reason=e.detail[:120])
            return
    owner = store.get("owners", owner_id)
    if not owner:
        await ws.close(code=4404)
        return
    await live.call(ws, owner, _expand)


class Speech(BaseModel):
    turn_id: str


@api.post("/speech")
async def speech_audio(body: Speech) -> Response:
    """Spoken version of one of Saathi's replies, as a WAV file (PR-10.3).

    Only a reply Saathi actually gave can be spoken, never arbitrary text. The audio is
    saved and reused for the same words, so replaying a reply, or a reply that repeats
    earlier words, costs nothing.
    """
    turn = store.get("turns", body.turn_id)
    if not turn or turn.get("role") != "assistant" or not (turn.get("text") or "").strip():
        raise HTTPException(404, "reply not found")
    key = hashlib.sha256(f"{settings.tts_voice}:{turn['text']}".encode()).hexdigest()[:32]
    name = f"speech/{key}.wav"
    if cached := media.load(turn["owner_id"], name):
        return Response(content=cached, media_type="audio/wav")
    try:
        audio = await speech.speak(turn["text"])
        media.save(turn["owner_id"], name, audio, "audio/wav")
        return Response(content=audio, media_type="audio/wav")
    except speech.VoiceUnavailable as e:
        raise HTTPException(503, str(e)) from e


@api.post("/owners/{owner_id}/documents")
async def add_document(owner_id: str, image: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    original = media.save(owner_id, f"{new_id('doc')}.{mime.split('/')[-1]}", data, mime)
    item = await loops.extract_document(data, mime, owner)
    doc = {"id": new_id("inv"), "owner_id": owner_id, "status": "pending",
           "document": original, **item.model_dump()}
    return {"autonomy": ACT, "proposal": store.put("inventory", doc)}


@api.post("/owners/{owner_id}/wishes")
def add_wish(owner_id: str, text: str = Form(...)) -> dict:
    """A wish the owner types is the owner's own action, so it is approved at once."""
    owner_or_404(owner_id)
    doc = {"id": new_id("m"), "owner_id": owner_id, "status": "approved", "kind": "wish",
           "title": text[:60], "summary": text, "quote": text, "people": [], "places": [], "year": None}
    return store.put("moments", doc)


# ---- Curation loop (Propose: the owner decides) -------------------------------


@api.get("/owners/{owner_id}/review")
def review_queue(owner_id: str) -> dict:
    return {
        "moments": store.list("moments", owner_id=owner_id, status="pending"),
        "inventory": store.list("inventory", owner_id=owner_id, status="pending"),
    }


class Decision(BaseModel):
    collection: Literal["moments", "inventory", "people"]
    decision: Literal["approve", "reject"]
    edits: dict | None = None


@api.post("/proposals/{doc_id}/decision")
def decide(doc_id: str, body: Decision) -> dict:
    doc = store.get(body.collection, doc_id)
    if not doc or doc["status"] != "pending":
        raise HTTPException(404, "no pending proposal with that id")
    if body.decision == "reject":
        store.delete(body.collection, doc_id)
        return {"id": doc_id, "status": "rejected"}
    safe_edits = {k: v for k, v in (body.edits or {}).items() if k not in ("id", "owner_id", "status", "updates")}
    if safe_edits:
        doc = store.update(body.collection, doc_id, safe_edits)
    return proposals.approve(body.collection, doc)


@api.get("/owners/{owner_id}/archive")
def archive(owner_id: str) -> dict:
    return {
        "moments": store.list("moments", owner_id=owner_id, status="approved"),
        "inventory": store.list("inventory", owner_id=owner_id, status="approved"),
        "legal_documents": store.list("legal_documents", owner_id=owner_id),
    }


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
        store.list("inventory", owner_id=owner_id, status="approved"),
        store.list("moments", owner_id=owner_id, status="approved"),
        [v["ai_label"] for v in store.list("vault_items", owner_id=owner_id) if v.get("ai_label")],
        [d["document_type"] for d in store.list("legal_documents", owner_id=owner_id)],
        store.list("tasks", owner_id=owner_id),
        {"items": len(store.list("vault_items", owner_id=owner_id)), "recipient": recipient.get("status"),
         "sealed": bool(owner.get("vault_escrow"))},
    ]
    return owner, args


@api.post("/owners/{owner_id}/readiness")
async def readiness(owner_id: str) -> dict:
    """Readiness loop: score, plus next steps that stay on the owner's list until done."""
    owner, args = _readiness_inputs(owner_id)
    if (cached := _cached("readiness", owner_id, args)) is not None:
        return {**cached, "tasks": tasks.open_tasks(owner_id), "cached": True}
    result = await loops.assess_readiness(owner, *args[1:])
    tasks.refresh(owner_id, [g.model_dump() for g in result.gaps])
    store.update("owners", owner_id, {"readiness_score": result.score})
    # Running readiness updates the next steps, which are one of its own inputs, so remember
    # the inputs as they are now; an unchanged owner then gets the saved result next time.
    _remember("readiness", owner_id, _readiness_inputs(owner_id)[1], {"autonomy": ACT, "score": result.score})
    return {"autonomy": ACT, "score": result.score, "tasks": tasks.open_tasks(owner_id)}


@api.get("/owners/{owner_id}/tasks")
def list_tasks(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    return {"score": owner.get("readiness_score"), "tasks": tasks.open_tasks(owner_id)}


class TaskUpdate(BaseModel):
    status: Literal["done", "dismissed"]


@api.post("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate) -> dict:
    if not store.get("tasks", task_id):
        raise HTTPException(404, "task not found")
    return store.update("tasks", task_id, {"status": body.status, "closed_at": time.time()})


class Prompt(BaseModel):
    text: str


@api.post("/owners/{owner_id}/conversation/prompt")
def companion_asks(owner_id: str, body: Prompt) -> dict:
    """Start a topic from a next step: the companion's question appears in the chat,
    and the next typed message or live call carries on from it."""
    owner_or_404(owner_id)
    return store.put("turns", {"id": new_id("turn"), "owner_id": owner_id, "at": time.time(),
                               "role": "assistant", "text": body.text, "language": None})


@api.post("/owners/{owner_id}/legal-needs")
async def legal_needs(owner_id: str) -> dict:
    owner = owner_or_404(owner_id)
    args = [store.list("moments", owner_id=owner_id, status="approved", kind="wish"),
            store.list("inventory", owner_id=owner_id, status="approved"),
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


@api.post("/owners/{owner_id}/legal-handoff")
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
            for i in store.list("inventory", owner_id=owner_id, status="approved")
        ],
    }
    store.put("handoffs", summary)
    return {"autonomy": PROPOSE, "status": "sent to partner (mock)", "summary": summary}


@api.post("/owners/{owner_id}/legal-documents")
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


@api.post("/owners/{owner_id}/vault/invite")
def invite_recipient(owner_id: str, body: Invite) -> dict:
    """The owner chooses who receives the vault. Choosing someone new replaces the old choice."""
    owner = owner_or_404(owner_id)
    if open_case(owner_id):
        raise HTTPException(409, "The vault recipient can't be changed while a release case is open.")
    current = owner.get("vault_recipient") or {}
    changes: dict = {"vault_invite": {**body.model_dump(), "at": time.time()}}
    if current and current.get("name") != body.name:
        changes.update(vault_recipient=None, vault_recipient_name=None, vault_escrow=None)
    store.update("owners", owner_id, changes)
    return {"autonomy": PROPOSE, "invited": body.name}


@api.get("/owners/{owner_id}/vault/invite")
def get_invite(owner_id: str) -> dict:
    invite = owner_or_404(owner_id).get("vault_invite")
    if not invite:
        raise HTTPException(404, "the owner hasn't chosen a vault recipient yet")
    return invite


@api.post("/owners/{owner_id}/vault/recipient")
def set_recipient(owner_id: str, body: Recipient) -> dict:
    """The invited person accepts and registers their key. A new key (e.g. recovery code lost)
    means the owner's device must re-seal the vault, so only allowed while no case is open."""
    owner = owner_or_404(owner_id)
    invite = owner.get("vault_invite")
    if not invite:
        raise HTTPException(409, "Only the person the owner has chosen can become the vault recipient.")
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


@api.post("/owners/{owner_id}/vault/recipient/decision")
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


@api.post("/owners/{owner_id}/vault/escrow")
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


@api.post("/owners/{owner_id}/vault/items")
def add_vault_item(owner_id: str, body: VaultItem) -> dict:
    owner_or_404(owner_id)
    return store.put("vault_items", {"id": new_id("vault"), "owner_id": owner_id, **body.model_dump()})


@api.get("/owners/{owner_id}/vault/items")
def list_vault_items(owner_id: str) -> dict:
    return {"items": store.list("vault_items", owner_id=owner_id)}


# ---- Activation and vault release (Assist: a person decides) ------------------


@api.post("/owners/{owner_id}/death-report")
async def report_death(owner_id: str, certificate: UploadFile = File(...)) -> dict:
    owner = owner_or_404(owner_id)
    if open_case(owner_id):
        raise HTTPException(409, "a case is already open")
    data = await certificate.read()
    check = await loops.check_document(data, certificate.content_type or "image/jpeg",
                                       "death certificate", owner["name"])
    case = release.new_case(new_id("case"), owner_id, loops.review_summary(check))
    return {"autonomy": ASSIST, "case": store.put("cases", case)}


@api.post("/owners/{owner_id}/release/authority")
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
    cases = [release.refresh(c) for c in store.list("cases")]
    for c in cases:
        store.put("cases", c)
    return {"cases": cases}


class Review(BaseModel):
    reviewer: str
    override_reason: str | None = None


@api.post("/admin/cases/{case_id}/confirm-death")
def confirm_death(case_id: str, body: Review) -> dict:
    return _transition(case_id, lambda c: release.confirm_death(c, body.reviewer, body.override_reason))


@api.post("/admin/cases/{case_id}/confirm-authority")
def confirm_authority(case_id: str, body: Review) -> dict:
    return _transition(case_id, lambda c: release.confirm_authority(c, body.reviewer, body.override_reason))


@api.post("/owners/{owner_id}/alive")
def owner_alive(owner_id: str) -> dict:
    """The owner's device answers the alert: stop everything."""
    case = open_case(owner_id)
    if not case:
        return {"status": "no open case"}
    return _transition(case["id"], release.owner_responded)


def _transition(case_id: str, step) -> dict:
    case = store.get("cases", case_id)
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


@api.get("/owners/{owner_id}/vault/release-bundle")
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


@api.post("/owners/{owner_id}/ask")
async def ask_question(owner_id: str, body: Question) -> dict:
    require_handover(owner_id)
    owner = owner_or_404(owner_id)
    return {"autonomy": ACT, **await ask(owner, "family", body.question, body.session_id)}


@api.post("/owners/{owner_id}/settlement")
async def settlement(owner_id: str) -> dict:
    require_handover(owner_id)
    owner = owner_or_404(owner_id)
    args = [store.list("inventory", owner_id=owner_id, status="approved"),
            store.list("moments", owner_id=owner_id, status="approved")]
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


@api.post("/owners/{owner_id}/access-requests")
def request_access(owner_id: str, body: AccessRequest) -> dict:
    owner = store.get("owners", owner_id)
    if owner:  # same reply either way, so a stranger learns nothing about who uses the service
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
    return {"requests": store.list("access_requests", status="pending_review")}


class AccessDecision(BaseModel):
    decision: Literal["approve", "decline"]
    decided_by: Literal["owner", "team"]


@api.post("/access-requests/{request_id}/decision")
def decide_access(request_id: str, body: AccessDecision) -> dict:
    req = store.get("access_requests", request_id)
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


@api.get("/me")
def me(request: Request) -> dict:
    """Who is signed in (the app calls this to check the allowlist right after sign-in)."""
    user = getattr(request.state, "user", None)
    return {"email": user.email if user else None, "name": user.name if user else None,
            "auth": settings.auth_required}


api.include_router(portal.router)
app.include_router(api)
