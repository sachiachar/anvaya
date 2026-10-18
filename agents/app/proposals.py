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
import threading
from typing import Any

from . import knowledge
from .schemas import is_wish
from .store import new_id, store

_saving_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def saving(owner_id: str) -> threading.Lock:
    """Held while a Talk turn or a call exchange saves the owner's cards. Saving runs in worker threads,
    off the event loop, and without this two exchanges saved at once could each make the same card.
    One lock per owner, so one owner's save never waits for another's."""
    with _locks_guard:
        return _saving_locks.setdefault(owner_id, threading.Lock())


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


_ID_PREFIX = {"moments": "m", "inventory": "inv", "people": "p", "documents": "doc"}  # m_/inv_ are cited as sources


def _pending(collection: str, owner_id: str, fields: dict, **extra: Any) -> dict:
    return store.put(collection, {"id": new_id(_ID_PREFIX[collection]), "owner_id": owner_id, "status": "pending",
                                  **extra, **fields})


def _all(collection: str, owner_id: str) -> list[dict]:
    """Drafts (from Firestore), then kept items (from the bundle). Drafts first: a second detail about a
    kept item goes into the update card already waiting for Keep, not into a second update card."""
    return store.list(collection, owner_id=owner_id) + knowledge.items(owner_id, collection)


def known(owner_id: str) -> dict:
    """What is already saved or waiting, given to the model so it doesn't repeat itself. No gaps:
    they feed only the conversation planner (PR-2.12.2), never Saathi."""
    return {
        "family": knowledge.family(owner_id) + [
            f"{p['name']} ({p['relationship']}), waiting for confirmation"
            for p in store.list("people", owner_id=owner_id, status="pending")],
        "moments": [{"title": m["title"], "summary": m["summary"],
                     **({"waiting_for_keep": True} if m.get("status") == "pending" else {})}
                    for m in _all("moments", owner_id)],
        "inventory": [f"{i['institution']} {i['product']} {i['account_hint']} (nominee: "
                      f"{i.get('nominee_or_beneficiary') or 'not recorded'})" for i in _all("inventory", owner_id)],
    }


def save_moment(owner_id: str, moment: dict, source: dict[str, Any]) -> dict | None:
    """Returns the card to show, or None if it adds nothing new."""
    said_kind = moment.get("kind")
    if is_wish(moment.get("statements")):
        moment = {**moment, "kind": "wish"}  # something the owner wants is a wish, decided in code (PR-13.1)
    saved = _all("moments", owner_id)
    for existing in saved:  # a repeat first, so something already kept is never added again
        if _similar(existing["quote"], moment["quote"]) or _similar(existing["summary"], moment["summary"], 0.85):
            if existing["status"] == "pending":
                statements = _statements(existing, moment)
                return store.update("moments", existing["id"], {**moment, "quote": existing["quote"],
                                                                "statements": statements,
                                                                "kind": _kind(existing, moment, statements)})
            return None  # already kept
    for existing in saved:
        if _norm(existing["title"]) == _norm(moment["title"]):
            quote = existing["quote"] if _norm(moment["quote"]) in _norm(existing["quote"]) \
                else f"{existing['quote']} … {moment['quote']}"
            statements = _statements(existing, moment)
            merged = {**moment, "quote": quote, "summary": _combine(existing["summary"], moment["summary"]),
                      "people": sorted(set(existing.get("people", [])) | set(moment.get("people", []))),
                      "places": sorted(set(existing.get("places", [])) | set(moment.get("places", []))),
                      "year": moment.get("year") or existing.get("year"),
                      "statements": statements, "kind": _kind(existing, moment, statements)}
            if existing["status"] == "pending":
                return store.update("moments", existing["id"], merged)
            return _pending("moments", owner_id, merged, updates=existing["id"], **source)
    like = lookalike(owner_id, moment, also=said_kind)
    extra = {"looks_like": {"id": like["id"], "title": like["title"]}} if like else {}
    return _pending("moments", owner_id, moment, **extra, **source)


def _kind(existing: dict, new: dict, statements: list[dict]) -> str:
    """A follow-up never turns a wish back into a story: the merged statements decide."""
    return "wish" if is_wish(statements) or existing.get("kind") == "wish" else new.get("kind", "story")


def _statements(old: dict, new: dict) -> list[dict]:
    """A follow-up adds statements; the same statement said again is kept once."""
    out, seen = [], set()
    for s in [*(old.get("statements") or []), *(new.get("statements") or [])]:
        key = tuple(_norm(s.get(f)) for f in ("subject", "predicate", "object", "recipient", "words"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _people_of(m: dict) -> set[str]:
    return {re.sub(r"^(?:my|his|her) ", "", _norm(p)) for p in m.get("people") or [] if _norm(p)}


def lookalike(owner_id: str, moment: dict, also: str | None = None) -> dict | None:
    """A kept story or wish this new card may be a reworded update of: the same kind, and the same
    people or a close title or summary. The owner decides on the card ("Replace it" or "Keep
    both"); nothing is merged silently. `also`: the kind the model gave, when code made the card a
    wish, so a wish once kept as a story is still found."""
    best, best_score = None, 0.0
    kinds = dict.fromkeys(k for k in (moment.get("kind"), also) if k)
    for kept in [m for k in kinds for m in knowledge.items(owner_id, "moments", kind=k)]:
        title_words, kept_words = set(_norm(moment.get("title")).split()), set(_norm(kept.get("title")).split())
        title = len(title_words & kept_words) / len(title_words | kept_words) if title_words | kept_words else 0
        summary_a, summary_b = set(_norm(moment.get("summary")).split()), set(_norm(kept.get("summary")).split())
        summary = len(summary_a & summary_b) / len(summary_a | summary_b) if summary_a | summary_b else 0
        shared = _people_of(moment) & _people_of(kept)
        score = max(title, summary) + (0.3 if shared else 0)
        if (title >= 0.5 or summary >= 0.5 or (shared and max(title, summary) >= 0.25)) and score > best_score:
            best, best_score = kept, score
    return best


def _match(owner_id: str, item: dict) -> list[dict]:
    """Saved items this one is about: same institution and last 4 digits; or, if the model left
    the digits out (e.g. "my wife is the SBI nominee"), the only account of that kind at that
    institution, counting kept accounts and new ones waiting for Keep."""
    saved = _all("inventory", owner_id)
    same = [e for e in saved if _same_item(e, item)]
    if same:
        return same
    if _digits(item.get("account_hint")):
        # The number arrives after the account was first mentioned without one ("my LIC policy",
        # then "it ends 1234"): it fills in that card, if it's the only numberless one of that kind
        # at that institution. Two numberless policies at one insurer stay apart.
        blank = [e for e in saved if not _digits(e.get("account_hint"))
                 and _norm(e.get("institution")) == _norm(item.get("institution"))
                 and (not item.get("category") or not e.get("category") or item.get("category") == e.get("category"))]
        return blank if len({e.get("updates") or e["id"] for e in blank}) == 1 else []
    at = [e for e in saved if _norm(e.get("institution")) == _norm(item.get("institution"))
          and (not item.get("category") or item.get("category") == e.get("category"))
          and (not item.get("product") or _norm(item.get("product")) == _norm(e.get("product")))]
    # One account, however many cards: a kept item and the update cards that point at it.
    accounts = {e.get("updates") or e["id"] for e in at}
    return at if len(accounts) == 1 else []


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
            and not (("account" in f.lower() or "number" in f.lower()) and _digits(merged.get("account_hint")))
        ]
        return store.update("inventory", existing["id"], merged)
    return _pending("inventory", owner_id, item, **source)


def same_name(a: str | None, b: str | None) -> bool:
    """The same person by name, ignoring case and punctuation ("Priya" and "Priya Rao" too)."""
    na, nb = _norm(a), _norm(b)
    return bool(na and nb) and (na == nb or na.split()[0] == nb.split()[0])


def _placeholder(p: dict) -> bool:
    """Someone saved by relationship only, before their name was known: "Wife" (wife)."""
    return _norm(p.get("name")) == _norm(p.get("relationship"))


def save_person(owner_id: str, person: dict) -> dict | None:
    """A new person, or None if they're already saved. Once the name of someone saved by
    relationship only is known ("Wife" becomes "Anjana"), that person is renamed, not duplicated."""
    name, relationship = _norm(person["name"]), _norm(person.get("relationship"))
    saved = knowledge.items(owner_id, "people") + store.list("people", owner_id=owner_id)
    same_role = [p for p in saved if relationship and _norm(p.get("relationship")) == relationship]
    if _placeholder(person) and same_role:
        return None  # "my wife" when the wife is already saved, by name or not
    stand_ins = [p for p in same_role if _placeholder(p)]
    if len(stand_ins) == 1 and len(same_role) == 1:
        old = stand_ins[0]
        if old.get("status") == "pending":
            return store.update("people", old["id"], person)
        return _pending("people", owner_id, person, updates=old["id"])
    if any(_norm(f).startswith(name) for f in knowledge.family(owner_id)):
        return None
    for existing in store.list("people", owner_id=owner_id):
        if _norm(existing["name"]) == name:
            return store.update("people", existing["id"], person) if existing["status"] == "pending" else None
    return _pending("people", owner_id, person)


def save_screened(owner_id: str, screened, source: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    """Save the cards the guardian allowed (guardian_inputs.screen), each linked to the decision
    that allowed it, so a card can be traced back to its turn and its guardian decision."""
    def linked(collection: str, card, doc: dict | None) -> dict | None:
        if doc and (action := screened.allowed_by.get(id(card))) and store.get(collection, doc["id"]):
            doc = store.update(collection, doc["id"], {"guardian_action": action})
        return doc

    moments = [d for m in screened.moments if (d := linked("moments", m, save_moment(owner_id, m.model_dump(), source)))]
    inventory = [linked("inventory", i, save_inventory(owner_id, i.model_dump(), source)) for i in screened.inventory]
    people = [d for p in screened.people if (d := linked("people", p, save_person(owner_id, p.model_dump())))]
    return moments, inventory, people


def save_document(owner_id: str, *, path: str, mime: str, file_name: str | None, comment: str | None,
                  found: list[str], cards: list[tuple[str, dict]]) -> dict:
    """A card for the shared photo or document itself, so the owner can keep it: their comment says
    what it is; what was found in it is listed. The cards made from it point to it (document_id), so
    once kept they link both ways in the bundle."""
    comment = (comment or "").strip()
    title = (comment.split(".")[0][:60].strip() if comment else "") or (found[0] if found else "") \
        or (file_name or "Shared document")
    description = " ".join(filter(None, [comment, f"What Saathi found in it: {'; '.join(found)}." if found else ""])) \
        or "A photo or document you shared."
    doc = _pending("documents", owner_id, {"title": title, "description": description, "comment": comment,
                                           "file_name": file_name, "mime": mime, "document": path, "found": found})
    for collection, card in cards:
        if store.get(collection, card["id"]):
            store.update(collection, card["id"], {"document_id": doc["id"]})
            card["document_id"] = doc["id"]
    return doc


def approve(collection: str, doc: dict) -> dict:
    """The owner tapped Keep: commit the card to the kept-knowledge bundle, then remove the draft.
    An update card becomes the new version, and the version it updates is marked deprecated."""
    # Follow "superseded by": a second update card for the same item builds on the latest
    # version, never on one already replaced, so there's only ever one current version.
    previous = knowledge.current(doc["owner_id"], doc["updates"]) if doc.get("updates") else None
    if doc.get("updates") and not previous:
        store.delete(collection, doc["id"])
        raise knowledge.Removed(doc["updates"])  # it updates something the owner removed
    if previous:  # the new version keeps what was known and adds what the card says
        fresh = {k: v for k, v in doc.items() if v not in (None, "", [])}
        doc = {**{k: v for k, v in previous.items() if k not in ("concept", "okf_status")}, **fresh}
    kept = knowledge.commit(collection, doc, doc["owner_id"], replaces=previous["id"] if previous else None)
    store.delete(collection, doc["id"])
    return kept
