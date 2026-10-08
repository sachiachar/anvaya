"""The guardian core (PR-2.7, issue #12): one guard that important actions pass through.

When an action asks the guardian (`decide`), it:

1. writes the action down (`guardian_actions`): who, as a hashed key; what; the ids of the items
   involved; the agent's autonomy; where it came from (the app or a live call). Never any text,
   names or secrets;
2. runs the detectors registered for that action. A detector is a small check that returns a risk
   level and a reason (fixed words from code), or nothing. Fixed rules and, later, Gemini signals
   use the same format (PR-2.7.2);
3. turns the highest risk into one response, using the policy file `agents/guardian.yaml`
   (PR-2.7.3). A rule fixed in code can set a floor, so the file can make a response stricter but
   never let through what the code forbids;
4. records an intervention for a risk reviewer when the response isn't "continue" (the portal's
   Guardian page), and returns the decision.

Part 1 (this module) records and decides. Part 2 makes the four actions people care about ask
first and obey it: keeping a card, changing the vault recipient, the release, and the family's
questions.

Independent (PR-2.7.5): the guardian reads only what it's given and its own records. It never
imports or reads the vault, the escrow or any media; a test checks this.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from ..store import new_id, store
from . import core

POLICY_PATH = pathlib.Path(__file__).resolve().parents[2] / "guardian.yaml"
RISKS = ("none", "low", "medium", "high")
RESPONSES = ("continue", "add_check", "reduce_autonomy", "pause", "block")
ACTIONS = ("next_step", "model_call", "review_confirmation", "certificate_check")
CHANNELS = ("app", "live_call", "background")
KEPT_DAYS = 30  # each action record's expire_at; a Firestore TTL policy removes it (deploy.yml)

Risk = Literal["none", "low", "medium", "high"]
Response = Literal["continue", "add_check", "reduce_autonomy", "pause", "block"]


# ---- The policy file ------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Levels(_Strict):
    none: Response | None = None
    low: Response | None = None
    medium: Response | None = None
    high: Response | None = None


class Policy(_Strict):
    version: Literal[1]
    default: Levels
    actions: dict[str, Levels]

    @model_validator(mode="after")
    def _complete(self) -> Policy:
        if missing := [r for r in RISKS if getattr(self.default, r) is None]:
            raise ValueError(f"guardian.yaml: the default has no response for {', '.join(missing)}")
        if unknown := sorted(set(self.actions) - set(ACTIONS)):
            raise ValueError(f"guardian.yaml: unknown actions {', '.join(unknown)}")
        if missing := [a for a in ACTIONS if a not in self.actions]:
            raise ValueError(f"guardian.yaml: no entry for {', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def policy() -> Policy:
    """The policy file, checked against its schema; a problem stops start-up (check_policy)."""
    return Policy.model_validate(yaml.safe_load(POLICY_PATH.read_text()))


def check_policy() -> None:
    policy()


def response_for(action: str, risk: str) -> str:
    return getattr(policy().actions[action], risk) or getattr(policy().default, risk)


def stricter(a: str, b: str) -> str:
    return max(a, b, key=RESPONSES.index)


# ---- Detectors ------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """What a detector found: a risk level, a reason in fixed words, and, for a rule fixed in
    code, the mildest response allowed (the floor)."""
    detector: str
    risk: Risk
    reason: str
    floor: Response = "continue"


Detector = Callable[[dict], Finding | None]


def next_step_rule(facts: dict) -> Finding | None:
    """The step guard's verdict (step_guard.check): a step that could be part of a fraud."""
    rule = facts.get("rule")
    if rule:
        return Finding("step_guard", "high", f"A next step was not pursued: rule {rule}", floor="block")
    return None


def daily_limit(facts: dict) -> Finding | None:
    """A call our key won't make: a person's daily allowance, or the service's daily ceiling."""
    if facts.get("limit") == "allowance":
        return Finding("daily_limit", "high", f"Daily allowance reached: {facts['where']}", floor="pause")
    if facts.get("limit") == "ceiling":
        return Finding("daily_limit", "high",
                       f"Daily service ceiling reached: {facts['where']}, {facts['line']} paused", floor="pause")
    return None


def review_override(facts: dict) -> Finding | None:
    """A reviewer confirmed although the AI's check said "not valid" (with a written reason)."""
    if facts.get("override"):
        # Always a reviewer's look at least: the policy file can raise this, never silence it.
        return Finding("review_override", "medium", f"A reviewer went against the AI's 'not valid' on the {facts['what']}",
                       floor="add_check")
    return None


MAX_SIGN_IDS = 5  # a guide can grow; the reason stays short


def warning_signs(facts: dict) -> Finding | None:
    """Warning signs from the fraud reference guide that a document check found (sign ids only)."""
    found = facts.get("signs_found") or []
    if not found:
        return None
    ids = ", ".join(found[:MAX_SIGN_IDS]) + (f" and {len(found) - MAX_SIGN_IDS} more" if len(found) > MAX_SIGN_IDS else "")
    return Finding("warning_signs", "medium",
                   f"The {facts['what']} check found {len(found)} warning sign{'s' if len(found) > 1 else ''}: {ids}",
                   floor="add_check")  # always a reviewer's look at least


# Which detectors watch which action. A new detector, fixed or a Gemini signal, is added here.
DETECTORS: dict[str, list[Detector]] = {
    "next_step": [next_step_rule],
    "model_call": [daily_limit],
    "review_confirmation": [review_override],
    "certificate_check": [warning_signs],
}


# ---- Deciding -------------------------------------------------------------------------


@dataclass
class Decision:
    response: str
    risk: str
    findings: list[Finding] = field(default_factory=list)
    action_id: str = ""

    @property
    def goes_ahead(self) -> bool:
        return self.response in ("continue", "add_check", "reduce_autonomy")


def decide(action: str, agent: str, facts: dict | None = None, *, refs: dict[str, str] | None = None,
           case_key: str | None = None, user_key: str | None = None, event_key: str | None = None,
           once: str | None = None, record_id: str | None = None) -> Decision:
    """Ask the guardian about one action. `facts` are what the detectors read; they are never
    stored. `refs` are the ids of the items involved, which are stored. `user_key` is whose action
    it is (the caller's by default); `event_key` whose intervention it is, if different (e.g.
    core.SERVICE for the daily ceiling). `once` gives the intervention a stable id, so a repeat
    (the same refusal, the same step) adds no new one. `record_id` does the same for the action
    record: a repeat replaces it (with the latest time) instead of adding another. Where the
    action came from (the app or a live call) is the request's (core.current_channel)."""
    channel = core.current_channel()
    if action not in ACTIONS or channel not in CHANNELS:
        raise ValueError(f"unknown guardian action {action!r} or channel {channel!r}")
    facts, user = facts or {}, user_key or core.current_user()
    findings = [f for d in DETECTORS[action] if (f := d(facts)) is not None]
    risk = max((f.risk for f in findings), key=RISKS.index, default="none")
    response = response_for(action, risk)
    for f in findings:
        response = stricter(response, f.floor)
    now = time.time()
    record = store.put("guardian_actions", {
        "id": record_id or new_id("gac"), "at": now, "day": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"),
        "expire_at": datetime.fromtimestamp(now + KEPT_DAYS * 86400, timezone.utc),
        "user_key": user, "action": action, "agent": agent, "autonomy": core.autonomy(agent), "channel": channel,
        "refs": refs or {}, "risk": risk, "response": response, "detectors": [f.detector for f in findings]})
    if response != "continue":
        reason = "; ".join(f.reason for f in findings) or f"The policy's response for risk {risk}"
        core.record_intervention(agent, response, reason, case_key, user_key=event_key or user, once=once)
    return Decision(response, risk, findings, record["id"])
