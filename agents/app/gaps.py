"""Gaps and conflicts drive the conversation (PR-7.10).

Fixed rules, not the model, find what's missing in the owner's kept knowledge, and rank it by
how much it would hurt the family. The conversation planner takes the gaps that are due as steps
(planner.py), and Saathi raises them gently, when the moment fits; Gemini only words the question.
Code remembers which gap was asked and how it was answered, so a question comes back only as a
reminder, after a wait (see `due`). "Skip" marks a gap as intentionally unknown, and it's never
raised again.

    nominee      an account or policy with no nominee or beneficiary
    conflict     a wish gives an account to someone other than its nominee
    heir         a property no wish or story says who should receive
    relationship a person whose relationship to the owner isn't known
    papers       property or insurance with no record of where its papers are kept
    will         nothing kept says whether there's a will (even "there's no will" counts)
    loans        nothing kept says whether there are loans or debts
    first_call   nothing kept says who the family should call first
    digital      nothing kept about email or online accounts
    confirm      something kept with doubt ("I think it's Meena"), two weeks on
    recheck      a nominee, papers or the will, once a year, in case it changed
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from . import knowledge
from .store import store

NOMINEE_EXPECTED = {"insurance": 3, "bank_account": 2, "investment": 2, "retirement": 2}
UNKNOWN = {"", "unknown", "not sure", "none", "n/a", "n a", "na", "nil", "not applicable"}
KINDS = ("nominee", "conflict", "heir", "relationship", "papers", "will", "loans", "first_call", "digital",
         "confirm", "recheck")  # as listed above; the web app's Gap type has the same list
DAY = 86400
PAPERS_EXPECTED = {"property", "insurance"}  # where their papers are kept, or the family can't claim them
CONFIRM_AFTER_DAYS = 14  # something kept with doubt ("I think…") comes up to check after this
RECHECK_EVERY_DAYS = knowledge.STALE_AFTER_DAYS  # nominees, papers and the will: re-confirmed once a year (PR-7.6)
# Words that show something is already recorded, even "there's no loan". Matched on _words text.
WILL_WORDS = re.compile(r" (my|a|the|no|his|her) will | testament | vasiyat | wasiyat | वसीयत ")
LOAN_WORDS = re.compile(r" (loan|loans|debt|debts|emi|emis|mortgage|karz|karza|qarz|कर्ज|कर्ज़|लोन) ")
# Not "phone" or "mobile" alone: "phone Meena first" is about who to call, not an online account.
DIGITAL_WORDS = re.compile(r" (email|e mail|gmail|password manager|online|upi|net banking|mobile banking|digital|"
                           r"whatsapp|facebook|social media) ")
FIRST_CALL_WORDS = re.compile(r" (call|contact|inform|tell|phone) (\w+ ){0,4}first | first (\w+ ){0,3}(call|contact|inform) ")
COMMON = (  # kind, harm, what's missing
    ("will", 3, "Whether there's a will, and where it's kept, isn't recorded."),
    ("loans", 2, "Whether there are any loans or debts isn't recorded."),
    ("first_call", 2, "Who the family should call first isn't recorded."),
    ("digital", 1, "Email and online accounts aren't recorded."),
)
COMMON_KINDS = {kind for kind, _, _ in COMMON}
COMMON_SIGNS = {"will": (WILL_WORDS, set()), "loans": (LOAN_WORDS, {"loan"}), "first_call": (FIRST_CALL_WORDS, set()),
                "digital": (DIGITAL_WORDS, {"digital_account", "subscription"})}
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


def _words(text: str | None) -> str:
    """Like _plain, but keeps Hindi (Devanagari) words, for finding what's been talked about."""
    return " ".join(re.sub(r"[^\w\u0900-\u097f']+", " ", (text or "").lower()).split())


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


def _kept_at(owner_id: str) -> dict[str, float]:
    """When each kept item was confirmed, by id."""
    out = {}
    for cid, entry in knowledge.catalog(owner_id)["concepts"].items():
        if at := entry.get("kept_at"):
            out[cid] = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    return out


def _mentions(kind: str, moments: list[dict], accounts: list[dict]) -> bool:
    """Whether these items record a common gap: an item of its kind, or words about it."""
    words, categories = COMMON_SIGNS[kind]
    if categories & {a.get("category") for a in accounts}:
        return True
    said = " ".join(_words(f"{m.get('title')} {m.get('summary')} {m.get('quote')}") for m in moments) + " " + \
        " ".join(_words(f"{a.get('institution')} {a.get('product')}") for a in accounts)
    return bool(words.search(f" {said} "))


def _changes(item: dict) -> bool:
    """Facts that change over the years: a nominee, where the papers are, the will."""
    return bool((item.get("nominee_or_beneficiary") or "").strip() or (item.get("papers_at") or "").strip()
                or WILL_WORDS.search(f" {_words(item.get('title'))} {_words(item.get('summary'))} "))


def _doubt(item: dict) -> str:
    parts = [f"{label}: {item[f]}" for f, label in (("nominee_or_beneficiary", "nominee"), ("papers_at", "papers"))
             if (item.get(f) or "").strip()]
    return ", ".join(parts) or (item.get("summary") or "")[:80]


def _label(item: dict) -> str:
    return " ".join(x for x in (item.get("institution"), item.get("product"), item.get("account_hint")) if x)


def find(owner_id: str, *, include_skipped: bool = False, now: float | None = None) -> list[dict]:
    """Open gaps, most harmful first. Each has a stable key, its harm, and a plain sentence."""
    now = time.time() if now is None else now
    accounts = knowledge.items(owner_id, "inventory")
    moments = knowledge.items(owner_id, "moments")
    people = knowledge.items(owner_id, "people")
    kept_at = _kept_at(owner_id)
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
        if category in PAPERS_EXPECTED and _plain(a.get("papers_at")) in UNKNOWN:
            gap(f"papers:{_id(a)}", 2, a["id"], f"Where the papers for {label} are kept isn't recorded.", "papers")
    for p in people:
        if _plain(p.get("relationship")) in UNKNOWN:
            gap(f"relationship:{_id(p)}", 1, p["id"], f"How {p.get('name')} is related isn't known.", "relationship")

    # Making sure: something kept with doubt, after a while; and things that change, once a year.
    for item in [*accounts, *moments]:
        age = now - kept_at.get(item["id"], now)
        name = _label(item) or f"\"{item.get('title')}\""
        if item.get("unsure") and age >= CONFIRM_AFTER_DAYS * DAY:
            # The kept version's id is in the key, so a newer version kept with doubt is checked again.
            gap(f"confirm:{_id(item)}:{item['id']}", 2, item["id"],
                f"{name} was kept with some doubt ({_doubt(item)}); worth checking it's right.", "confirm")
        elif _changes(item) and age >= RECHECK_EVERY_DAYS * DAY:
            cycle = int(age // (RECHECK_EVERY_DAYS * DAY))  # a new key each year, so a "still the same" lasts a year
            gap(f"recheck:{_id(item)}:{cycle}", 1, item["id"],
                f"{name} was last confirmed over a year ago ({_doubt(item)}); is it still the same?", "recheck")

    # What families most often can't find. Recorded once anything kept mentions it, even "there's none".
    for kind, harm, text in COMMON:
        if not _mentions(kind, moments, accounts):
            gap(f"{kind}:{kind}", harm, "", text, kind)

    # A check the owner said is right is closed, for this version (confirm) or this year (recheck).
    answered = {a["key"] for a in store.list("gap_asks", owner_id=owner_id)
                if (a.get("events") or [{}])[-1].get("outcome") == "answered"}
    found = [g for g in found if not (g["kind"] in ("confirm", "recheck") and g["key"] in answered)]
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
    pending = {c: store.list(c, owner_id=owner_id, status="pending") for c in ("inventory", "people", "moments")}
    waiting = {_id(d) for c in ("inventory", "people") for d in pending[c]}
    chosen: list[dict] = []
    reminded = False
    for g in find(owner_id, now=now):
        if waiting & set(g["key"].split(":")[1:]):
            continue
        if g["kind"] in COMMON_KINDS and _mentions(g["kind"], pending["moments"], pending["inventory"]):
            continue  # a waiting card answers it
        state = {"key": g["key"], "kind": g["kind"], "text": g["text"], "harm": HARM_WORDS.get(g["harm"], "low"),
                 "reminder": 0}
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
                asked_gap: str | None, outcome: str | None, now: float | None = None, step: str | None = None) -> None:
    """Record what Saathi just said: how the owner answered the gap it asked last time, which
    gap (if any) it asks about now, and the message itself, so the next turn can carry on.
    A key that isn't an open gap is ignored, so the model can't invent one. Only keys, times and
    outcomes are kept, never the owner's words."""
    now = time.time() if now is None else now
    previous = (recent(last_said, now) or {}).get("gap")
    if previous and outcome in OUTCOMES:
        doc = store.get("gap_asks", f"{owner_id}:{previous}")
        if doc and doc.get("events"):
            # Which turn gave the answer, so discarding that conversation takes the answer back too.
            doc["events"][-1].update(outcome=outcome, answered_in=turn_id)
            store.put("gap_asks", doc)
    asked = asked_gap if asked_gap and any(g["key"] == asked_gap for g in find(owner_id)) else None
    if asked:
        doc = store.get("gap_asks", f"{owner_id}:{asked}") or {
            "id": f"{owner_id}:{asked}", "owner_id": owner_id, "key": asked, "events": []}
        doc["events"].append({"turn_id": turn_id, "at": now, "outcome": None})
        store.put("gap_asks", doc)
    current = (store.get("owners", owner_id) or {}).get("last_said") or {}
    if current.get("at", 0) <= now:  # exchanges of a call can finish out of order: keep the newest
        # The plan step Saathi carried out (PR-2.12.3), so the next answer is judged against it.
        store.update("owners", owner_id, {"last_said": {"text": text, "turn_id": turn_id, "at": now, "gap": asked,
                                                        "step": step}})


def forget_turns(owner_id: str, turn_ids: set[str]) -> None:
    """A discarded conversation: its questions no longer count as asked, and Saathi doesn't
    carry on from its last message."""
    for doc in store.list("gap_asks", owner_id=owner_id):
        events = doc.get("events") or []
        kept = [e for e in events if e.get("turn_id") not in turn_ids]
        changed = len(kept) != len(events)
        for e in kept:
            if e.get("answered_in") in turn_ids:  # asked in a kept turn, answered in the discarded one
                e.update(outcome=None, answered_in=None)
                changed = True
        if not kept:
            store.delete("gap_asks", doc["id"])
        elif changed:
            store.put("gap_asks", {**doc, "events": kept})
    owner = store.get("owners", owner_id) or {}
    if (owner.get("last_said") or {}).get("turn_id") in turn_ids:
        store.update("owners", owner_id, {"last_said": None})


def skip(owner_id: str, key: str) -> dict:
    """The owner chose not to answer: never raise this gap again."""
    if not any(g["key"] == key for g in find(owner_id, include_skipped=True)):
        raise KeyError(key)
    return store.put("gap_skips", {"id": f"{owner_id}:{key}", "owner_id": owner_id, "key": key, "at": time.time()})
