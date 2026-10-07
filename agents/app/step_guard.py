"""Never pursue a next step that could be part of a fraud (PR-13.3, docs/proposals/listening.md §6).

Every next step is typed from closed lists in code (step_types.py): what it does, the channel it
goes through, and flags. Flags are worked out in code from the step's words as well as taken from
the step, so the check never depends on a model remembering to set them. Before a step reaches
the owner, Saathi or the suggested topic, `check` decides, in order:

1. a step of an unknown type, channel or flag is not pursued: when unsure, the answer is no;
2. a step that involves a secret (an OTP, a password or a PIN) is never pursued. The one exception
   is adding it to the owner's own vault in the app, which is encrypted on their device, and only
   if the step doesn't also tell them to share, give or send it to anyone;
3. a step that matches an approved fraud tactic is not pursued. A tactic lists the step types,
   channels and flags it exploits (`fraud_guides.Exploits`); only tactics the certificate check may
   use (observed, derived, validated) count.

If the approved tactics can't be read, nothing is pursued: it fails closed.

A step that isn't pursued is dropped, and a count is recorded with no words and no names: the
rule or tactic, the copy's hashed key and the time. The portal shows the count.
"""

from __future__ import annotations

import logging
import re
import time

from . import demo, fraud_guides
from .governance import core as governance
from .step_types import CHANNELS, FLAGS, STEP_TYPES
from .store import new_id, store

log = logging.getLogger(__name__)

# Words for each flag, in English, Hinglish and Hindi. "PIN code" is a postal code in India.
_SECRET = re.compile(r"\b(otps?|one[- ]time (passwords?|codes?)|passwords?|passcodes?|pins?(?![- ]?codes?)|cvvs?|"
                     r"upi pins?|mpins?)\b|पासवर्ड|ओटीपी|पिन(?! ?कोड)", re.IGNORECASE)
_PAYMENT = re.compile(r"\b(pay|pays|paid|paying|payment|fees?|charges?|deposit|commission|bribe|"
                      r"(send|transfer|move) (the )?(money|amount|funds|cash))\b|शुल्क|फीस|पैसे भेज", re.IGNORECASE)
_STARTED_BY_OTHER = re.compile(r"\b(who called|caller|called you|someone (called|asked|messaged)|asked you to|"
                               r"on the call|sms from|whatsapp(ed)? you|link in the message)\b", re.IGNORECASE)
_DEADLINE = re.compile(r"\b(today|immediately|urgent(ly)?|right now|within \d+ (hours?|days?)|last date|"
                       r"before it expires|or (it|the account) (will be )?(blocked|frozen))\b|तुरंत|आज ही", re.IGNORECASE)
_SHARE = re.compile(r"\b(share|give|send|tell|forward|read out|hand over|show|batao|bata do|de do)\b"
                    r"|बताएं|बताओ|बता दें|भेजें|दे दें", re.IGNORECASE)

# Today's next steps (the readiness planner's actions) as typed steps, until PR-13.4 types them itself.
ACTION_TYPES = {"talk": ("talk_more", "anvaya_app"), "family": ("tell_family", "family_named_by_owner"),
                "vault": ("add_to_vault", "anvaya_app"), "legal": ("legal_document", "registered_professional"),
                "institution": ("record_with_institution", "institution_official")}


class TacticsUnavailable(Exception):
    """The approved tactics couldn't be read; nothing may be pursued."""


def _words(step: dict) -> str:
    return " ".join(str(step.get(f) or "") for f in ("title", "text", "why_it_matters", "question_for_owner")) + \
        " " + " ".join(str(s) for s in step.get("steps") or [])


def flags_of(step: dict) -> set[str]:
    """The step's own flags plus those its words show."""
    words = _words(step)
    found = {f for f, pattern in (("secret", _SECRET), ("payment", _PAYMENT), ("started_by_other", _STARTED_BY_OTHER),
                                  ("deadline", _DEADLINE)) if pattern.search(words)}
    return set(step.get("flags") or []) | found


def approved_tactics() -> list[tuple[str, fraud_guides.Exploits]]:
    """Approved tactics that say which steps they exploit, from every current guide. Raises
    TacticsUnavailable if the guides can't be read."""
    try:
        guides = fraud_guides.current_all()
    except Exception as e:
        log.warning("fraud guides unavailable; no next step is pursued until they can be read")
        raise TacticsUnavailable from e
    return [(f"{g.key}:{t.id}", t.exploits) for g in guides for t in g.tactics
            if t.label in fraud_guides.CHECKABLE and t.exploits and not t.exploits.empty()]


def check(step: dict, tactics: list[tuple[str, fraud_guides.Exploits]]) -> str | None:
    """Why this step must not be pursued, or None if it may be."""
    if step.get("type") not in STEP_TYPES or step.get("channel") not in CHANNELS:
        return "unknown"
    if not set(step.get("flags") or []) <= set(FLAGS):
        return "unknown"
    flags = flags_of(step)
    own_vault = step["type"] == "add_to_vault" and step["channel"] == "anvaya_app" and not _SHARE.search(_words(step))
    if "secret" in flags and not own_vault:
        return "secret"
    for key, exploits in tactics:
        if exploits.matches(step["type"], step["channel"], flags):
            return f"tactic:{key}"
    return None


def typed(task: dict) -> dict:
    """A readiness next step with its type and channel, from its action."""
    kind, channel = ACTION_TYPES.get(task.get("action", ""), (None, None))
    return {"type": kind, "channel": channel, "flags": [], **task}  # a step that has its own type keeps it


def allowed(steps: list[dict]) -> list[dict]:
    """The steps that may reach the owner, without counting the others (for reads)."""
    try:
        tactics = approved_tactics()
    except TacticsUnavailable:
        return []
    return [s for s in steps if check(typed(s), tactics) is None]


def pursue(owner_id: str, steps: list[dict]) -> list[dict]:
    """The steps that may reach the owner. The others are dropped and counted. Raises
    TacticsUnavailable if the guides can't be read, so a caller never mistakes it for "no steps"."""
    tactics = approved_tactics()
    kept = []
    for step in steps:
        reason = check(typed(step), tactics)
        if reason is None:
            kept.append(step)
        else:
            # The hashed key of whoever's copy this is (what the portal shows), never a name.
            key = demo.current_or_none() or governance.user_key(owner_id)
            store.put("steps_not_pursued", {"id": new_id("snp"), "at": time.time(), "rule": reason, "user_key": key})
    return kept
