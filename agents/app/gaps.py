"""Gaps and conflicts drive the conversation (PR-7.10).

Fixed rules, not the model, find what's missing in the owner's kept knowledge, and rank it by
how much it would hurt the family. Saathi gets the top two that are due and raises at most one,
gently, when the moment fits; Gemini only words the question. Code remembers which gap was asked and how
it was answered, so a question comes back only as a reminder, after a wait (see `due`). "Skip"
marks a gap as intentionally unknown, and it's never raised again.

    nominee      an account or policy with no nominee or beneficiary
    conflict     a wish gives an account to someone other than its nominee
    heir         a property no wish or story says who should receive
    relationship a person whose relationship to the owner isn't known
"""

from __future__ import annotations

import re
import time

from . import knowledge
from .store import store

NOMINEE_EXPECTED = {"insurance": 3, "bank_account": 2, "investment": 2, "retirement": 2}
UNKNOWN = {"", "unknown", "not sure", "none", "n/a"}
DAY = 86400
CONVERSATION_GAP = 30 * 60  # Saathi's last message counts as "this conversation" for this long
# Asking again is a reminder, after a wait in days that grows each time; the more a gap would hurt
# the family, the sooner it comes back and the more reminders it gets. The length of each tuple is
# how many reminders follow the first question.
REMINDERS = {3: (3, 7, 14, 14), 2: (7, 14, 30), 1: (14, 30)}
SLOWER = {"not_now": 2, "declined": 3}  # how much longer to wait after these answers
OUTCOMES = ("answered", "unsure", "not_now", "declined")
HARM_WORDS = {3: "high", 2: "medium", 1: "low"}
# A bare relationship ("my wife") can't be compared with a name unless a kept person has it.
RELATION_WORDS = {"wife", "husband", "spouse", "son", "daughter", "mother", "father", "brother", "sister",
                  "niece", "nephew", "grandson", "granddaughter", "daughter-in-law", "son-in-law", "amma", "appa"}


def _plain(text: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9']+", " ", (text or "").lower()).split())


def _who(name: str | None) -> str:
    """'my wife' -> 'wife'; 'Ajay Kumar' -> 'ajay kumar'."""
    return re.sub(r"^(?:my|his|her)\s+", "", _plain(name))


def _same_person(a: str, b: str, relations: dict[str, str]) -> bool:
    """False only when both clearly name different people. A relationship word that no kept
    person has ("my wife", with no wife kept) can't be compared, so it's never a conflict."""
    a, b = _who(a), _who(b)
    if not a or not b:
        return True  # unknown on either side is a nominee gap, not a conflict
    a, b = relations.get(a, a), relations.get(b, b)  # "wife" -> the kept wife's name
    if a in RELATION_WORDS or b in RELATION_WORDS:
        return True
    return a == b or a.split(" ")[0] == b.split(" ")[0]


def _id(item: dict) -> str:
    """What a gap is about, stable across versions: a new version of an account (an update card)
    gets a new id, but a skip must still hold."""
    if "category" in item:
        return f"{_plain(item.get('institution'))}|{re.sub(r'[^0-9]', '', item.get('account_hint') or '')[-4:]}" \
               f"|{_plain(item.get('product'))}"
    return _plain(item.get("name") or item.get("title"))


def _label(item: dict) -> str:
    return " ".join(x for x in (item.get("institution"), item.get("product"), item.get("account_hint")) if x)


def find(owner_id: str, *, include_skipped: bool = False) -> list[dict]:
    """Open gaps, most harmful first. Each has a stable key, its harm, and a plain sentence."""
    accounts = knowledge.items(owner_id, "inventory")
    moments = knowledge.items(owner_id, "moments")
    people = knowledge.items(owner_id, "people")
    relations = {_plain(p.get("relationship")): _plain(p.get("name")) for p in people if p.get("relationship")}
    wishes = [m for m in moments if m.get("kind") == "wish"]
    found: list[dict] = []

    def gap(key: str, harm: int, about: str, text: str, kind: str) -> None:
        found.append({"key": key, "kind": kind, "harm": harm, "about": about, "text": text})

    for a in accounts:
        label = _label(a)
        category = a.get("category")
        nominee = (a.get("nominee_or_beneficiary") or "").strip()
        if category in NOMINEE_EXPECTED and _plain(nominee) in UNKNOWN:
            gap(f"nominee:{_id(a)}", NOMINEE_EXPECTED[category], a["id"],
                f"{label} has no nominee recorded.", "nominee")
        inst = _plain(a.get("institution"))
        for w in wishes:
            text = _plain(f"{w.get('title')} {w.get('summary')}")
            if not inst or f" {inst} " not in f" {text} " or _plain(nominee) in UNKNOWN:
                continue
            for person in w.get("people") or []:
                if not _same_person(person, nominee, relations):
                    gap(f"conflict:{_id(a)}:{_id(w)}", 3, a["id"],
                        f"The wish \"{w.get('title')}\" gives the {label} to {person}, "
                        f"but its nominee is {nominee}.", "conflict")
                    break
        if category == "property":
            named = any(inst and f" {inst} " in f" {_plain(m.get('title'))} {_plain(m.get('summary'))} "
                        for m in moments)
            if not named:
                gap(f"heir:{_id(a)}", 2, a["id"], f"Nobody is named to receive {label}.", "heir")
    for p in people:
        if _plain(p.get("relationship")) in UNKNOWN:
            gap(f"relationship:{_id(p)}", 1, p["id"], f"How {p.get('name')} is related isn't known.", "relationship")

    if not include_skipped:
        skipped = {s["key"] for s in store.list("gap_skips", owner_id=owner_id)}
        found = [g for g in found if g["key"] not in skipped]
    return sorted(found, key=lambda g: -g["harm"])


def due(owner_id: str, n: int = 2, now: float | None = None) -> list[dict]:
    """What Saathi may raise now: the n most harmful open gaps that are due, each with its state.

    A gap never asked is due. One asked before comes back only as a reminder: after a wait that
    depends on its harm and grows each time (longer after "not now"), and only while it has
    reminders left; after that it stays on the owner's list of pages to write, but Saathi stops
    raising it. Never twice in one conversation, at most one reminder among the n, and not while
    a waiting card already answers it (the answer waits for Keep)."""
    now = time.time() if now is None else now
    asks = {a["key"]: a.get("events") or [] for a in store.list("gap_asks", owner_id=owner_id)}
    waiting = {_id(d) for c in ("inventory", "people") for d in store.list(c, owner_id=owner_id, status="pending")}
    chosen: list[dict] = []
    reminded = False
    for g in find(owner_id):
        if waiting & set(g["key"].split(":")[1:]):
            continue
        state = {"key": g["key"], "text": g["text"], "harm": HARM_WORDS.get(g["harm"], "low"), "reminder": 0}
        if events := asks.get(g["key"]):
            since = now - events[-1]["at"]
            wait = _wait(g["harm"], events)
            if reminded or since < CONVERSATION_GAP or wait is None or since < wait:
                continue
            reminded = True
            state.update(reminder=len(events), last_asked=_ago(since),
                         last_answer=(events[-1].get("outcome") or "no answer").replace("_", " "))
        chosen.append(state)
        if len(chosen) == n:
            break
    return chosen


def _wait(harm: int, events: list[dict]) -> float | None:
    """Seconds before the next reminder, or None when none are left."""
    waits = REMINDERS.get(harm, REMINDERS[1])
    if len(events) > len(waits):
        return None
    return waits[len(events) - 1] * DAY * SLOWER.get(events[-1].get("outcome") or "", 1)


def _ago(seconds: float) -> str:
    days = round(seconds / DAY)
    return "today" if days < 1 else "yesterday" if days == 1 else f"{days} days ago"


def recent(last_said: dict | None, now: float | None = None) -> dict | None:
    """Saathi's last message, only while it's still the same conversation."""
    now = time.time() if now is None else now
    return last_said if last_said and now - last_said.get("at", 0) < CONVERSATION_GAP else None


def after_reply(owner_id: str, last_said: dict | None, *, text: str, turn_id: str,
                asked_gap: str | None, outcome: str | None, now: float | None = None) -> None:
    """Record what Saathi just said: how the owner answered the gap it asked last time, which
    gap (if any) it asks about now, and the message itself, so the next turn can carry on.
    A key that isn't an open gap is ignored, so the model can't invent one. Only keys, times and
    outcomes are kept, never the owner's words."""
    now = time.time() if now is None else now
    previous = (recent(last_said, now) or {}).get("gap")
    if previous and outcome in OUTCOMES:
        doc = store.get("gap_asks", f"{owner_id}:{previous}")
        if doc and doc.get("events"):
            doc["events"][-1]["outcome"] = outcome
            store.put("gap_asks", doc)
    asked = asked_gap if asked_gap and any(g["key"] == asked_gap for g in find(owner_id)) else None
    if asked:
        doc = store.get("gap_asks", f"{owner_id}:{asked}") or {
            "id": f"{owner_id}:{asked}", "owner_id": owner_id, "key": asked, "events": []}
        doc["events"].append({"turn_id": turn_id, "at": now, "outcome": None})
        store.put("gap_asks", doc)
    store.update("owners", owner_id, {"last_said": {"text": text, "turn_id": turn_id, "at": now, "gap": asked}})


def forget_turns(owner_id: str, turn_ids: set[str]) -> None:
    """A discarded conversation: its questions no longer count as asked, and Saathi doesn't
    carry on from its last message."""
    for doc in store.list("gap_asks", owner_id=owner_id):
        kept = [e for e in doc.get("events") or [] if e.get("turn_id") not in turn_ids]
        if not kept:
            store.delete("gap_asks", doc["id"])
        elif len(kept) != len(doc.get("events") or []):
            store.put("gap_asks", {**doc, "events": kept})
    owner = store.get("owners", owner_id) or {}
    if (owner.get("last_said") or {}).get("turn_id") in turn_ids:
        store.update("owners", owner_id, {"last_said": None})


def last_gap(owner_id: str, last_said: dict | None) -> dict | None:
    """The gap Saathi's last message asked about, if it's still open, for judging the answer."""
    key = (recent(last_said) or {}).get("gap")
    return next(({"key": g["key"], "text": g["text"]} for g in find(owner_id) if g["key"] == key), None)


def skip(owner_id: str, key: str) -> dict:
    """The owner chose not to answer: never raise this gap again."""
    if not any(g["key"] == key for g in find(owner_id, include_skipped=True)):
        raise KeyError(key)
    return store.put("gap_skips", {"id": f"{owner_id}:{key}", "owner_id": owner_id, "key": key, "at": time.time()})
