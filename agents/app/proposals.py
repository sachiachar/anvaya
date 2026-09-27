"""Saving AI suggestions as proposals the owner keeps or discards.

- A repeat of something already there is merged, not shown twice.
- A follow-up detail (same moment title, or same institution and last 4 digits)
  updates the pending card, or, if the original was already kept, becomes an
  "update" card the owner confirms.
- People the owner describes ("Ajay is my nephew") become family cards.
"""

from __future__ import annotations

import re
from typing import Any

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


def known(owner_id: str) -> dict:
    """What is already saved or waiting, given to the model so it doesn't repeat itself."""
    owner = store.get("owners", owner_id) or {}
    return {
        "family": owner.get("family", []) + [
            f"{p['name']} ({p['relationship']}), waiting for confirmation"
            for p in store.list("people", owner_id=owner_id, status="pending")],
        "moments": [{"title": m["title"], "summary": m["summary"]} for m in store.list("moments", owner_id=owner_id)],
        "inventory": [f"{i['institution']} {i['product']} {i['account_hint']}"
                      for i in store.list("inventory", owner_id=owner_id)],
    }


def save_moment(owner_id: str, moment: dict, source: dict[str, Any]) -> dict | None:
    """Returns the card to show, or None if it adds nothing new."""
    for existing in store.list("moments", owner_id=owner_id):
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


def save_inventory(owner_id: str, item: dict, source: dict[str, Any]) -> dict:
    """Merge into a matching pending proposal, or create a new one."""
    for existing in store.list("inventory", owner_id=owner_id):
        if not _same_item(existing, item):
            continue
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
    owner = store.get("owners", owner_id) or {}
    name = _norm(person["name"])
    if any(_norm(f).startswith(name) for f in owner.get("family", [])):
        return None
    for existing in store.list("people", owner_id=owner_id):
        if _norm(existing["name"]) == name:
            return store.update("people", existing["id"], person) if existing["status"] == "pending" else None
    return _pending("people", owner_id, person)


def approve(collection: str, doc: dict) -> dict:
    """The owner tapped Keep."""
    if collection == "people":
        owner = store.get("owners", doc["owner_id"])
        store.update("owners", owner["id"], {"family": [*owner.get("family", []),
                                                        f"{doc['name']} ({doc['relationship']})"]})
        return store.update("people", doc["id"], {"status": "approved"})
    if doc.get("updates") and (target := store.get(collection, doc["updates"])):
        fields = {k: v for k, v in doc.items() if k not in ("id", "owner_id", "status", "updates")}
        store.delete(collection, doc["id"])
        return store.update(collection, target["id"], {**fields, "status": "approved"})
    return store.update(collection, doc["id"], {"status": "approved"})
