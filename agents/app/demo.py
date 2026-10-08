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
import threading
import time
from contextvars import ContextVar

from fastapi import HTTPException, Request

from . import knowledge, media
from .governance import core as governance
from .store import new_id, store

PERSONA = pathlib.Path(__file__).resolve().parents[1] / "demo_data" / "persona.json"
LOCAL = "local"
# Collections whose documents belong to one owner (by owner_id); reset clears them all.
OWNED = ("moments", "inventory", "people", "vault_items", "cases", "legal_documents", "turns",
         "access_requests", "handoffs", "tasks", "loop_cache", "gap_skips", "gap_asks")
# Version of how a copy keeps knowledge. 1: kept items live in the owner's OKF bundle (PR-7);
# copies from before are started again on their next sign-in, purging kept items from Firestore.
KNOWLEDGE_VERSION = 1
TOUCH_SECONDS = 3600  # how often "last used" is written, so not every request writes
# Routes that answer the same for any owner, so they never reveal whether one exists. They
# check the copy themselves and act only on the caller's own.
NEUTRAL_ROUTES = {"request_access"}

# The people in every copy (PR-1.3). Each acts as themselves, so rules such as "two different
# reviewers" hold even though one judge plays them all (PR-1.4).
PEOPLE = {
    "vikram": {"name": "Vikram", "role": "owner"},
    "priya": {"name": "Priya", "role": "family"},
    "anjali": {"name": "Anjali", "role": "reviewer"},
    "rahul": {"name": "Rahul", "role": "reviewer"},
    "admin": {"name": "Platform admin", "role": "platform_admin"},
}
DEFAULT_PERSON = "vikram"
ACTING_HEADER = "x-acting-as"
WHO_DOES = {"owner": "Vikram", "family": "Priya", "reviewer": "a reviewer (Anjali or Rahul)"}

_copy: ContextVar[str | None] = ContextVar("demo_copy", default=None)
_person: ContextVar[str] = ContextVar("demo_person", default=DEFAULT_PERSON)


def _persona() -> dict:
    return json.loads(PERSONA.read_text())


def owner_id_for(copy_id: str) -> str:
    return _persona()["owner"]["id"] if copy_id == governance.user_key(LOCAL) else f"owner_{copy_id}"


def current() -> str:
    copy_id = _copy.get()
    if copy_id is None:
        raise HTTPException(401, "Please sign in.")
    return copy_id


def current_or_none() -> str | None:
    """The caller's copy, or None outside a signed-in request."""
    return _copy.get()


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
    if _needs_start(copy_id, doc):
        with _start_lock(copy_id):  # two first requests at once must not both start the copy
            if _needs_start(copy_id, store.get("demo_copies", copy_id)):
                # A new copy, one whose data was cleared, or one from before kept knowledge moved to
                # the OKF bundle (its kept items were in Firestore): start it again from the persona.
                reset(copy_id)
                store.update("demo_copies", copy_id, {"knowledge": KNOWLEDGE_VERSION})
    return copy_id


def _needs_start(copy_id: str, doc: dict | None) -> bool:
    return not store.get("owners", owner_id_for(copy_id)) or (doc or {}).get("knowledge") != KNOWLEDGE_VERSION


_start_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _start_lock(copy_id: str) -> threading.Lock:
    with _locks_guard:
        return _start_locks.setdefault(copy_id, threading.Lock())


def reset(copy_id: str | None = None) -> dict:
    """Restore a copy to the starting point: the caller's own (PR-1.5), or one a platform admin
    chose in the portal (PR-12.6). Never touches any other copy."""
    oid = owner_id_for(copy_id) if copy_id else owner_id()
    for collection in OWNED:
        for doc in store.list(collection, owner_id=oid):
            store.delete(collection, doc["id"])
    media.delete_all(oid)
    for doc in store.list("agent_steps", user_key=copy_id or current()):  # its trace for the agents panel
        store.delete("agent_steps", doc["id"])
    data = _persona()
    family = data["owner"].get("family", [])
    owner = {**{k: v for k, v in data["owner"].items() if k != "family"}, "id": oid, "readiness_score": None}
    store.put("owners", owner)
    # The persona's starting knowledge goes into the bundle, labelled as the demo's own (not the
    # owner's confirmation): Priya, and any stories or accounts the persona starts with.
    seeded = {"generated_by": "process:demo-persona", "verified": False}
    for entry in family:
        name, _, relationship = entry.partition(" (")
        knowledge.commit("people", {"id": new_id("p"), "name": name, "relationship": relationship.rstrip(")")},
                         oid, **seeded)
    for m in data["moments"]:
        knowledge.commit("moments", {**m, "id": new_id("m")}, oid, **seeded)
    for item in data["inventory"]:
        knowledge.commit("inventory", {**item, "id": new_id("inv")}, oid, **seeded)
    return {**owner, "family": knowledge.family(oid)}


def act_as(person: str | None) -> str:
    """Set who is acting within the caller's copy. Unknown people are refused."""
    person = (person or DEFAULT_PERSON).strip().lower()
    if person not in PEOPLE:
        raise HTTPException(400, "unknown person")
    _person.set(person)
    return person


def person() -> dict:
    """The person acting now, within the caller's copy: {"id", "name", "role"}."""
    pid = _person.get()
    return {"id": pid, **PEOPLE[pid]}


def only(*roles: str):
    """Route dependency: only these people may take this action (PR-1.4)."""
    def check() -> None:
        if PEOPLE[_person.get()]["role"] not in roles:
            who = " or ".join(WHO_DOES[r] for r in roles)
            raise HTTPException(403, f"Only {who} can do this. Switch to them first.")
    return check


async def guard(request: Request) -> None:
    """Router dependency: who is acting (from the switcher), a fresh per-request cache of kept
    knowledge, and a route with an owner in its path
    only works for the caller's own copy. Async on purpose: it runs in the request's own context,
    so the acting person it sets reaches the route (a sync dependency runs in a worker thread,
    whose changes would be lost)."""
    act_as(request.headers.get(ACTING_HEADER))
    knowledge.start_request()
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


NICKNAME_MAX = 30


def clean_nickname(value: str | None) -> str | None:
    """One line of plain text, at most 30 characters; empty means none."""
    text = " ".join("".join(ch for ch in (value or "") if ch.isprintable()).split())[:NICKNAME_MAX]
    return text or None


def set_nickname(value: str | None) -> str | None:
    """The optional name a visitor gives their own copy. Only platform admins see it, in the
    portal's demo list; it's never logged. Asked once: skipping also counts as answered."""
    nickname = clean_nickname(value)
    store.update("demo_copies", ensure(), {"nickname": nickname, "nickname_asked": True})
    return nickname


def copy_record() -> dict:
    return store.get("demo_copies", current()) or {}
