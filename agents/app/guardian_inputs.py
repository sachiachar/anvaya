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
from dataclasses import dataclass, field

from . import step_guard
from .governance import guardian

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
