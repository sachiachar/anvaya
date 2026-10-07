"""Never pursue a next step that could be part of a fraud (PR-13.3, docs/proposals/listening.md §6).

Every next step is typed from closed lists in code: what it does, the channel it goes through, and
flags. Before a step reaches the owner, Saathi or the suggested topic, `check` decides, in order:

1. a step of an unknown type or channel is not pursued: when unsure, the answer is no;
2. a step that involves a secret (an OTP, a password or a PIN) is never pursued, whether the model
   flagged it or its words mention one. The one exception is adding it to the owner's own vault in
   the app, which is encrypted on their device and is where secrets belong;
3. a step that matches an approved fraud tactic is not pursued. A tactic lists the step types,
   channels and flags it exploits (`fraud_guides.Exploits`); only tactics the certificate check may
   use (observed, derived, validated) count.

A step that isn't pursued is dropped, and a count is recorded with no words and no names: the
rule or tactic, the owner's hashed key and the time. The portal shows the count.
"""

from __future__ import annotations

import re
import time

from . import demo, fraud_guides
from .governance import core as governance
from .store import new_id, store

STEP_TYPES = ("tell_family", "record_with_institution", "legal_document", "keep_papers", "add_to_vault",
              "gather_information", "talk_more", "plan_with_family")
CHANNELS = ("institution_official", "registered_professional", "family_named_by_owner", "owner_self", "anvaya_app")
FLAGS = ("payment", "secret", "started_by_other", "deadline")

# Words for a secret, in English, Hinglish and Hindi. A step that mentions one is never pursued.
# "PIN code" is a postal code in India, not a secret.
_SECRET = re.compile(r"\b(otp|one[- ]time (password|code)|password|passcode|pin(?! ?code)|cvv|upi pin|mpin)\b"
                     r"|पासवर्ड|ओटीपी|पिन", re.IGNORECASE)

# Today's next steps (the readiness planner's actions) as typed steps, until PR-13.4 types them itself.
ACTION_TYPES = {"talk": ("talk_more", "anvaya_app"), "family": ("tell_family", "family_named_by_owner"),
                "vault": ("add_to_vault", "anvaya_app"), "legal": ("legal_document", "registered_professional"),
                "institution": ("record_with_institution", "institution_official")}


def _words(step: dict) -> str:
    return " ".join(str(step.get(f) or "") for f in ("title", "text", "why_it_matters", "question_for_owner")) + \
        " " + " ".join(str(s) for s in step.get("steps") or [])


def approved_tactics() -> list[tuple[str, fraud_guides.Exploits]]:
    """Approved tactics that say which steps they exploit, from every current guide."""
    out = []
    for key in fraud_guides.keys():
        guide = fraud_guides.current(key)
        for tactic in guide.tactics if guide else []:
            if tactic.label in fraud_guides.CHECKABLE and tactic.exploits and not tactic.exploits.empty():
                out.append((f"{key}:{tactic.id}", tactic.exploits))
    return out


def check(step: dict, tactics: list[tuple[str, fraud_guides.Exploits]] | None = None) -> str | None:
    """Why this step must not be pursued, or None if it may be."""
    if step.get("type") not in STEP_TYPES or step.get("channel") not in CHANNELS:
        return "unknown"
    flags = set(step.get("flags") or [])
    if not flags <= set(FLAGS):
        return "unknown"
    own_vault = step["type"] == "add_to_vault" and step["channel"] == "anvaya_app"
    if not own_vault and ("secret" in flags or _SECRET.search(_words(step))):
        return "secret"
    for key, exploits in approved_tactics() if tactics is None else tactics:
        if exploits.matches(step["type"], step["channel"], flags):
            return f"tactic:{key}"
    return None


def typed(task: dict) -> dict:
    """A readiness next step with its type and channel, from its action."""
    kind, channel = ACTION_TYPES.get(task.get("action", ""), (None, None))
    return {"type": kind, "channel": channel, "flags": [], **task}  # a step that has its own type keeps it


def pursue(owner_id: str, steps: list[dict]) -> list[dict]:
    """The steps that may reach the owner. The others are dropped and counted."""
    tactics = approved_tactics()
    allowed = []
    for step in steps:
        reason = check(step, tactics)
        if reason is None:
            allowed.append(step)
        else:
            # The hashed key of whoever's copy this is (what the portal shows), never a name.
            key = demo.current_or_none() or governance.user_key(owner_id)
            store.put("steps_not_pursued", {"id": new_id("snp"), "at": time.time(), "rule": reason, "user_key": key})
    return allowed
