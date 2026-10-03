"""Gaps and conflicts drive the conversation (PR-7.10).

Fixed rules, not the model, find what's missing in the owner's kept knowledge, and rank it by
how much it would hurt the family. Saathi gets the top two and raises at most one, gently,
when the moment fits; Gemini only words the question. "Skip" marks a gap as intentionally
unknown, and it's never raised again.

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


def top(owner_id: str, n: int = 2) -> list[str]:
    """What Saathi may ask about: the n most harmful open gaps, as plain sentences."""
    return [g["text"] for g in find(owner_id)[:n]]


def skip(owner_id: str, key: str) -> dict:
    """The owner chose not to answer: never raise this gap again."""
    if not any(g["key"] == key for g in find(owner_id, include_skipped=True)):
        raise KeyError(key)
    return store.put("gap_skips", {"id": f"{owner_id}:{key}", "owner_id": owner_id, "key": key, "at": time.time()})
