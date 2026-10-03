"""Saving AI suggestions as proposals the owner keeps or discards.

- A repeat of something already there is merged, not shown twice.
- A follow-up detail (same moment title, or same institution and last 4 digits)
  updates the pending card, or, if the original was already kept, becomes an
  "update" card the owner confirms.
- People the owner describes ("Ajay is my nephew") become family cards.

Cards are drafts in Firestore. Keeping one commits it to the owner's kept-knowledge bundle
(knowledge.py, PR-7) and removes the draft, so kept items are always read from the bundle.
"""

from __future__ import annotations

import re
from typing import Any

from . import gaps, knowledge
from .store import new_id, store


def _norm(text: str | None) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _similar(a: str | None, b: str | None, threshold: float = 0.8) -> bool:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= threshold


def _covers(new: str | None, old: str | None, threshold: float = 0.6) -> bool:
    """True if most of the old text's words are still in the new text (a rewrite, not a replacement)."""
    old_words = set(_norm(old).split())
    return bool(old_words) and len(old_words & set(_norm(new).split())) / len(old_words) >= threshold


def _combine(old: str, new: str) -> str:
    """A follow-up must add to what was saved, never wipe it."""
    return new if _covers(new, old) else f"{old.rstrip('. ')}. {new}"


def _digits(hint: str | None) -> str:
    return re.sub(r"\D", "", hint or "")[-4:]


def _same_item(a: dict, b: dict) -> bool:
    return (
        bool(_digits(a.get("account_hint")))
        and _digits(a.get("account_hint")) == _digits(b.get("account_hint"))
        and _norm(a.get("institution")) == _norm(b.get("institution"))
    )


_ID_PREFIX = {"moments": "m", "inventory": "inv", "people": "p"}  # m_/inv_ are cited as sources


def _pending(collection: str, owner_id: str, fields: dict, **extra: Any) -> dict:
    return store.put(collection, {"id": new_id(_ID_PREFIX[collection]), "owner_id": owner_id, "status": "pending",
                                  **extra, **fields})


def _all(collection: str, owner_id: str) -> list[dict]:
    """Kept items (from the bundle) and drafts (from Firestore) together."""
    return knowledge.items(owner_id, collection) + store.list(collection, owner_id=owner_id)


def known(owner_id: str) -> dict:
    """What is already saved or waiting, given to the model so it doesn't repeat itself."""
    return {
        "family": knowledge.family(owner_id) + [
            f"{p['name']} ({p['relationship']}), waiting for confirmation"
            for p in store.list("people", owner_id=owner_id, status="pending")],
        "moments": [{"title": m["title"], "summary": m["summary"]} for m in _all("moments", owner_id)],
        "inventory": [f"{i['institution']} {i['product']} {i['account_hint']} (nominee: "
                      f"{i.get('nominee_or_beneficiary') or 'not recorded'})" for i in _all("inventory", owner_id)],
        # PR-7.10: what's missing, found by fixed rules and ranked by harm; Saathi may raise one.
        "open_gaps": gaps.top(owner_id),
    }


def save_moment(owner_id: str, moment: dict, source: dict[str, Any]) -> dict | None:
    """Returns the card to show, or None if it adds nothing new."""
    for existing in _all("moments", owner_id):
        if _similar(existing["quote"], moment["quote"]) or _similar(existing["summary"], moment["summary"], 0.85):
            if existing["status"] == "pending":
                return store.update("moments", existing["id"], {**moment, "quote": existing["quote"]})
            return None  # already kept
        if _norm(existing["title"]) == _norm(moment["title"]):
            quote = existing["quote"] if _norm(moment["quote"]) in _norm(existing["quote"]) \
                else f"{existing['quote']} … {moment['quote']}"
            merged = {**moment, "quote": quote, "summary": _combine(existing["summary"], moment["summary"]),
                      "people": sorted(set(existing.get("people", [])) | set(moment.get("people", []))),
                      "places": sorted(set(existing.get("places", [])) | set(moment.get("places", []))),
                      "year": moment.get("year") or existing.get("year")}
            if existing["status"] == "pending":
                return store.update("moments", existing["id"], merged)
            return _pending("moments", owner_id, merged, updates=existing["id"], **source)
    return _pending("moments", owner_id, moment, **source)


def _match(owner_id: str, item: dict) -> list[dict]:
    """Saved items this one is about: same institution and last 4 digits; or, if the model left
    the digits out (e.g. "my wife is the SBI nominee"), the only saved item at that institution."""
    saved = _all("inventory", owner_id)
    same = [e for e in saved if _same_item(e, item)]
    if same or _digits(item.get("account_hint")):
        return same
    at = [e for e in saved if _norm(e.get("institution")) == _norm(item.get("institution"))]
    accounts = {e.get("id") for e in at if e["status"] != "pending"} | {e.get("updates") for e in at if e.get("updates")}
    return at if len(accounts - {None}) <= 1 and at else []


def save_inventory(owner_id: str, item: dict, source: dict[str, Any]) -> dict:
    """Merge into a matching pending proposal, or create a new one."""
    for existing in _match(owner_id, item):
        if not _digits(item.get("account_hint")):
            item = {**item, "account_hint": existing.get("account_hint")}
        if existing["status"] != "pending":
            return _pending("inventory", owner_id, item, updates=existing["id"], **source)
        merged = {**existing, **{k: v for k, v in item.items() if v not in (None, "", [])}}
        merged["missing_fields"] = [
            f for f in item.get("missing_fields", [])
            if not ("nominee" in f.lower() and merged.get("nominee_or_beneficiary"))
        ]
        return store.update("inventory", existing["id"], merged)
    return _pending("inventory", owner_id, item, **source)


def save_person(owner_id: str, person: dict) -> dict | None:
    name = _norm(person["name"])
    if any(_norm(f).startswith(name) for f in knowledge.family(owner_id)):
        return None
    for existing in store.list("people", owner_id=owner_id):
        if _norm(existing["name"]) == name:
            return store.update("people", existing["id"], person) if existing["status"] == "pending" else None
    return _pending("people", owner_id, person)


def approve(collection: str, doc: dict) -> dict:
    """The owner tapped Keep: commit the card to the kept-knowledge bundle, then remove the draft.
    An update card becomes the new version, and the version it updates is marked deprecated."""
    # Follow "superseded by": a second update card for the same item builds on the latest
    # version, never on one already replaced, so there's only ever one current version.
    previous = knowledge.current(doc["owner_id"], doc["updates"]) if doc.get("updates") else None
    if previous:  # the new version keeps what was known and adds what the card says
        fresh = {k: v for k, v in doc.items() if v not in (None, "", [])}
        doc = {**{k: v for k, v in previous.items() if k not in ("concept", "okf_status")}, **fresh}
    kept = knowledge.commit(collection, doc, doc["owner_id"], replaces=previous["id"] if previous else None)
    store.delete(collection, doc["id"])
    return kept
