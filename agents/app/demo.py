"""Private demo copies (PR-1): every signed-in Google account gets its own copy of the demo.

A copy is the account's own Vikram, with everything that belongs to him (conversation, cards,
vault, cases, recordings). The copy comes from the verified sign-in, never from the browser,
and every route checks that the data it touches belongs to the caller's copy. Anything else
is "not found", so a judge can't even tell that other copies exist (PR-1.6).

Without sign-in (local development and tests) there is one person, "local", whose copy keeps
the persona's own ID.
"""

from __future__ import annotations

import json
import pathlib
import time
from contextvars import ContextVar

from fastapi import HTTPException, Request

from . import media
from .governance import core as governance
from .store import new_id, store

PERSONA = pathlib.Path(__file__).resolve().parents[1] / "demo_data" / "persona.json"
LOCAL = "local"
# Collections whose documents belong to one owner (by owner_id); reset clears them all.
OWNED = ("moments", "inventory", "people", "vault_items", "cases", "legal_documents", "turns",
         "access_requests", "handoffs", "tasks", "loop_cache")
TOUCH_SECONDS = 3600  # how often "last used" is written, so not every request writes
# Routes that answer the same for any owner, so they never reveal whether one exists. They
# check the copy themselves and act only on the caller's own.
NEUTRAL_ROUTES = {"request_access"}

_copy: ContextVar[str | None] = ContextVar("demo_copy", default=None)


def _persona() -> dict:
    return json.loads(PERSONA.read_text())


def owner_id_for(copy_id: str) -> str:
    return _persona()["owner"]["id"] if copy_id == governance.user_key(LOCAL) else f"owner_{copy_id}"


def current() -> str:
    copy_id = _copy.get()
    if copy_id is None:
        raise HTTPException(401, "Please sign in.")
    return copy_id


def owner_id() -> str:
    """The owner in the caller's own copy."""
    return owner_id_for(current())


def begin(uid: str) -> str:
    """Called at sign-in for every request and live call: note whose copy this is. It reads and
    writes nothing, so the admin portal's requests never create a copy."""
    copy_id = governance.user_key(uid)
    _copy.set(copy_id)
    return copy_id


def ensure() -> str:
    """Find or create the caller's copy (the app's first call, the live call and reset)."""
    copy_id = current()
    now = time.time()
    doc = store.get("demo_copies", copy_id)
    if not doc:
        store.put("demo_copies", {"id": copy_id, "user_key": copy_id, "owner_id": owner_id_for(copy_id),
                                  "created_at": now, "last_used_at": now, "allowance_used": 0.0,
                                  "status": "active"})
    elif now - doc.get("last_used_at", 0) > TOUCH_SECONDS:
        store.update("demo_copies", copy_id, {"last_used_at": now})
    if not store.get("owners", owner_id_for(copy_id)):
        reset()  # a new copy, or one whose data was cleared
    return copy_id


def reset() -> dict:
    """Restore the caller's own copy to the starting point. Never touches another copy (PR-1.5)."""
    oid = owner_id()
    for collection in OWNED:
        for doc in store.list(collection, owner_id=oid):
            store.delete(collection, doc["id"])
    media.delete_all(oid)
    data = _persona()
    owner = {**data["owner"], "id": oid, "readiness_score": None}
    store.put("owners", owner)
    for m in data["moments"]:
        store.put("moments", {**m, "id": new_id("m"), "owner_id": oid, "status": "approved"})
    for item in data["inventory"]:
        store.put("inventory", {**item, "id": new_id("inv"), "owner_id": oid, "status": "approved"})
    return owner


def guard(request: Request) -> None:
    """Router dependency: a route with an owner in its path only works for the caller's own copy."""
    requested = request.path_params.get("owner_id")
    route = request.scope.get("route")
    if route is not None and route.name in NEUTRAL_ROUTES:
        return
    if requested is not None and requested != owner_id():
        raise HTTPException(404, "owner not found")


def mine(collection: str, doc_id: str) -> dict | None:
    """A document by ID, only if it belongs to the caller's copy."""
    doc = store.get(collection, doc_id)
    return doc if doc and doc.get("owner_id") == owner_id() else None
