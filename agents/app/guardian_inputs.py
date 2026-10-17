"""What the guardian is fed about the owner's conversation (guardian-led conversations, PR-2.12.1).

The guardian is the central control for the owner's conversation: every new piece of information
the owner gives goes to it before anything is made of it. It answers allow, deflect or block, and
only allowed information becomes a card for the owner to Keep. Its inputs are:

  - the fixed rules in code (step_guard.py): passing on a secret, paying because someone else asked,
    changing something with an institution because someone called, being rushed by someone else;
  - the fraud module: tactics a reviewer approved in the fraud guides, which say which steps,
    channels and warning flags they exploit;
  - words aimed at Saathi itself, such as "ignore your instructions".

What the owner said is read here and never stored with the decision: the guardian keeps only the
ids involved (the turn, the card), the rules that matched and its decision (rule 4). The guardian
module itself never imports this one, so it stays independent (PR-2.7.5).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import knowledge, step_guard
from .governance import core, guardian
from .store import store

# How a piece of information reads as a step, so the fixed rules and the tactics can judge it.
KIND_TYPES = {"inventory": "record_with_institution", "wish": "legal_document", "person": "tell_family"}
# Words that try to steer Saathi rather than tell it something about the owner's life.
_TO_SAATHI = re.compile(
    r"\b(ignore|disregard|forget|override) (all |any |your |the )?(previous |earlier |above |prior )?"
    r"(instructions?|rules?|prompts?|guidelines?)\b|\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak\b|"
    r"\byou are (now|no longer)\b|\bpretend (to be|you are)\b", re.IGNORECASE)
CARD_FIELDS = {"moments": ("title", "summary", "quote"),
               "inventory": ("institution", "product", "account_hint", "holder_name", "nominee_or_beneficiary"),
               "people": ("name", "relationship")}


def statement_facts(kind: str, text: str) -> dict:
    """The facts the guardian's detector reads for one piece of information. Never stored."""
    step_type = KIND_TYPES.get(kind, "talk_more")
    flags, rule, shares_secret = step_guard.statement_signals(text, step_type)
    # Someone else started it (a caller, a message): judged as if an "official" asked for it.
    channel = "institution_official" if "started_by_other" in flags else "owner_self"
    facts: dict = {"flags": sorted(flags)}
    if _TO_SAATHI.search(text):
        facts["to_saathi"] = True
    if shares_secret:
        facts["secret_shared"] = True
    if rule:
        facts["rule"] = rule
    try:
        tactics = step_guard.approved_tactics()
    except step_guard.TacticsUnavailable:
        facts["tactics_unavailable"] = True
        tactics = []
    # A tactic counts only when the owner's own words show one of its warning flags, so an
    # ordinary account or wish is never held back just because a tactic names its kind of step.
    matched = [key for key, ex in tactics
               if ex.flags and flags & set(ex.flags) and ex.matches(step_type, channel, flags)]
    if matched:
        facts["tactics"] = matched
    return facts


@dataclass
class Screened:
    """The cards the guardian allowed, with the decision behind each (for the audit trail), and
    the ids of every decision made for this turn."""
    moments: list = field(default_factory=list)
    inventory: list = field(default_factory=list)
    people: list = field(default_factory=list)
    allowed_by: dict[int, str] = field(default_factory=dict)  # id(card) -> guardian action id
    decisions: list[str] = field(default_factory=list)
    held_back: int = 0
    # For the planner (PR-2.12.2): (action id, response, safe route) for each decision.
    outcomes: list[tuple[str, str, str | None]] = field(default_factory=list)


def safe_route(facts: dict) -> str | None:
    """Which safe-route step a deflected statement calls for: a secret belongs in the vault; a
    payment, change or tactic someone else started means calling the institution directly. Words
    aimed at Saathi get none: they're blocked, and the conversation simply goes on."""
    if facts.get("to_saathi"):
        return None
    if facts.get("secret_shared"):
        return "secret"
    return "someone_else" if facts.get("rule") or facts.get("tactics") else None


def _kind(collection: str, card) -> str:
    return card.kind if collection == "moments" else {"inventory": "inventory", "people": "person"}[collection]


def screen(*, owner_words: str, moments, inventory, people, turn_id: str, agent: str) -> Screened:
    """Ask the guardian about each new piece of information in one exchange, before any card is made.
    Each card is judged on its own words and what the owner said around it. With no card at all, what
    the owner said is still judged once, so nothing they say passes unseen."""
    out = Screened()
    cards = [("moments", c) for c in moments] + [("inventory", c) for c in inventory] + [("people", c) for c in people]
    for n, (collection, card) in enumerate(cards):
        words = " ".join(str(getattr(card, f, None) or "") for f in CARD_FIELDS[collection])
        facts = statement_facts(_kind(collection, card), f"{words} {owner_words}")
        decision = guardian.decide("owner_statement", agent, facts,
                                   refs={"turn": turn_id, "card": f"{collection}:{n}"}, once=f"gev_st_{turn_id}_{n}")
        out.decisions.append(decision.action_id)
        out.outcomes.append((decision.action_id, decision.response, safe_route(facts)))
        if decision.goes_ahead:
            getattr(out, collection).append(card)
            out.allowed_by[id(card)] = decision.action_id
        else:
            out.held_back += 1
    if not cards and owner_words.strip():
        facts = statement_facts("utterance", owner_words)
        decision = guardian.decide("owner_statement", agent, facts, refs={"turn": turn_id}, once=f"gev_st_{turn_id}")
        out.decisions.append(decision.action_id)
        out.outcomes.append((decision.action_id, decision.response, safe_route(facts)))
        out.held_back += 0 if decision.goes_ahead else 1
    return out


# ---- The family's questions (PR-2.12.4) ------------------------------------------------------
#
# The family asking about the owner after the handover: getting what the owner kept must stay easy,
# so the guardian stops only a vault secret and words aimed at the assistant. Everything else is
# answered; a risky pattern adds one helpful sentence (fixed words), or a quiet flag for our team
# that the family never sees. Full numbers in an answer are masked, never refused.

_APPROVAL = re.compile(r"\b(confirm|approve|is it (ok|okay|fine|alright|right)|should i|can i|am i allowed|allowed to|"
                       r"authori[sz]e)\b", re.IGNORECASE)
_CHANGE = re.compile(r"\b(transfer|move|change|withdraw|close|sell|pay|cash|break)\b", re.IGNORECASE)
_DISPUTE = re.compile(r"\b(who should get|who gets|who deserves|fair share|bigger share|don'?t tell|do not tell|"
                      r"hide (it|this|that) from|without telling|keep (it|this) from|dispute|fighting over)\b", re.IGNORECASE)
_BANK_CHANGE = re.compile(r"\b(change|new|different|update|another)\b.{0,40}\b(bank account|account number|ifsc|"
                          r"bank details|upi( id)?)\b", re.IGNORECASE)
BURST_PER_HOUR = 30  # more family questions than this in an hour gets a quiet look from our team

NOTES = {
    "someone_else": "One thing to know: banks and insurers don't ask for fees or codes on a call. It's safest to "
                    "phone them on the number in {first}'s own papers.",
    "approval": "I can only tell you what {first} kept; I can't approve changes. The bank or insurer decides, with "
                "the right papers.",
    "dispute": "For decisions between family members, the will and a registered lawyer are the right guide. I share "
               "only what {first} kept.",
}


def family_question_facts(text: str) -> dict:
    """What the guardian's family detector reads for one question. Never stored."""
    flags, rule, _ = step_guard.statement_signals(text, "record_with_institution")
    facts: dict = {"asks_secret": step_guard.asks_for_secret(text)}
    if _TO_SAATHI.search(text):
        facts["to_assistant"] = True
    notes = []
    try:
        tactics = step_guard.approved_tactics()
    except step_guard.TacticsUnavailable:
        tactics = []
    channel = "institution_official" if "started_by_other" in flags else "owner_self"
    if rule or any(ex.flags and flags & set(ex.flags) and ex.matches("record_with_institution", channel, flags)
                   for _, ex in tactics):
        notes.append("someone_else")
    if _APPROVAL.search(text) and _CHANGE.search(text):
        notes.append("approval")
    if _DISPUTE.search(text):
        notes.append("dispute")
    facts["notes"] = notes
    if _BANK_CHANGE.search(text):
        facts["bank_change"] = True
    hour_ago = time.time() - 3600
    asked = [a for a in store.list("guardian_actions", action="family_question", user_key=core.current_user())
             if a["at"] > hour_ago]
    if len(asked) >= BURST_PER_HOUR:
        facts["burst"] = True
    return facts


def family_notes(facts: dict, owner_name: str) -> list[str]:
    first = (owner_name or "the owner").split()[0]
    return [NOTES[n].format(first=first) for n in facts.get("notes", [])]


def family_answer(answer: str) -> tuple[str, dict]:
    """The assistant's answer before it is shown: full numbers masked to their last 4 characters (as
    the stored turn already is), and a check that it never helps pass on a secret or pay someone who
    asked. Returns the answer to show and the facts for the guardian (never stored)."""
    masked = knowledge.mask(answer)
    return masked, {"masked": masked != answer, "problem": step_guard.reply_problem(masked)}


# ---- Another voice (PR-2.13.2) -----------------------------------------------------------------


def needs_owner_confirmation(collection: str, doc: dict) -> bool:
    """With someone else in the room, these cards wait for the owner's confirmation: a wish, a money
    matter (practical), an account or policy, or a person. Stories and advice stay keepable."""
    return collection in ("inventory", "people") or (collection == "moments" and doc.get("kind") in ("wish", "practical"))


def another_voice(owner_id: str, turn_id: str, agent: str, card_docs: list[tuple[str, dict]], *,
                  heard_by: list[str] | None = None) -> str:
    """Another voice was heard in the owner's side of an exchange: the guardian records it for the
    team, and each card from that exchange is marked, so the owner sees it before Keep. A wish,
    money or people card is also held (another_voice_hold): at Keep, the guardian's voice rule
    waits for the owner to confirm on the card that it's what they want. Nothing about the voice
    is kept: no audio, no voiceprint. `heard_by` lists every agent that heard it (Saathi live, the
    extractor in the audio). Returns the guardian action id."""
    refs = {"turn": turn_id, "voice": "another", "heard_by": ",".join(heard_by or [agent])}
    decision = guardian.decide("owner_statement", agent, {"another_voice": True}, refs=refs,
                               once=f"gev_voice_{turn_id}")
    for collection, doc in card_docs:
        if store.get(collection, doc["id"]):
            mark = {"another_voice": True, **({"another_voice_hold": True}
                                              if needs_owner_confirmation(collection, doc) else {})}
            store.update(collection, doc["id"], mark)
            doc.update(mark)
    return decision.action_id


def voice_mismatch(owner_id: str, turn_id: str, agent: str, card_docs: list[tuple[str, dict]]) -> str:
    """The voice check: the start of the owner's side of an exchange didn't sound like their
    voiceprint (voiceprint.py). The guardian records it for the team, and marks each card from that
    exchange. At Keep, the guardian's voice rule (guardian.unconfirmed_voice) holds a marked card
    until the owner confirms on it that it was them. Nothing about the voice is kept. Returns the
    guardian action id."""
    decision = guardian.decide("owner_statement", agent, {"voice_mismatch": True},
                               refs={"turn": turn_id, "voice": "mismatch"}, once=f"gev_voicecheck_{turn_id}")
    for collection, doc in card_docs:
        if store.get(collection, doc["id"]):
            store.update(collection, doc["id"], {"voice_mismatch": True})
            doc["voice_mismatch"] = True
    return decision.action_id
