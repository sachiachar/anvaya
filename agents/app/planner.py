"""The conversation planner (PR-2.12.2): the only source of what Saathi talks about with the owner.

Design: docs/proposals/guardian-led-conversations.md. One stored plan per owner, built in code:

  1. safe-route steps, after the guardian deflected something the owner said (this conversation);
  2. the initial plan's steps not done yet (agents/conversation_plan.yaml, reviewed like code);
  3. the gaps that are due (gaps.due: harm-ranked, with reminder waits);
  4. the readiness planner's suggestions Saathi can talk about.

The gap finder and the readiness planner feed only this; Saathi follows the current step
(PR-2.12.3). Each step says who starts it: Saathi opens with it, or Saathi waits for the owner.
Every step passes the guardian's next-step check (step_guard.pursue) before it joins the plan.

Each change is a new version with its cause (a guardian decision, a Keep, readiness, a step
outcome), kept in `plan_versions` as ids, sources and who starts each step, never any words, so
the audit chain runs turn → guardian decision → card → Keep → plan version → next step.
"""

from __future__ import annotations

import pathlib
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from . import gaps, knowledge, step_guard, tasks
from .store import store

PLAN_PATH = pathlib.Path(__file__).resolve().parents[1] / "conversation_plan.yaml"
MAX_STEPS = 6
STALE_SECONDS = 3600            # a plan read after this long is worked out again (gaps fall due over time)
SAFE_STEP_SECONDS = gaps.CONVERSATION_GAP  # a safe-route step belongs to the conversation it came from
VERSIONS_KEPT_DAYS = 30         # each version's expire_at; a Firestore TTL policy removes it (deploy.yml)
OUTCOMES = ("answered", "not_now", "declined")
Opens = Literal["saathi", "owner"]
DONE_KINDS = ("person", "story", "advice", "practical", "wish", "inventory")


# ---- The initial plan -------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InitialStep(_Strict):
    id: str
    topic: str
    ask: str
    done_when: Literal["person", "story", "advice", "practical", "wish", "inventory"]
    opens: Opens

    @field_validator("id")
    @classmethod
    def _plain_id(cls, v: str) -> str:
        if not v.isidentifier() or v != v.lower():
            raise ValueError(f"step id {v!r} must be short, lower case and plain")
        return v


class InitialPlan(_Strict):
    version: Literal[1]
    steps: list[InitialStep]

    @field_validator("steps")
    @classmethod
    def _unique(cls, steps: list[InitialStep]) -> list[InitialStep]:
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)) or not ids:
            raise ValueError("the initial plan needs at least one step, and each id once")
        return steps


@lru_cache(maxsize=1)
def initial_plan() -> InitialPlan:
    """The plan file, checked against its schema; a problem stops start-up (check_plan)."""
    return InitialPlan.model_validate(yaml.safe_load(PLAN_PATH.read_text()))


def check_plan() -> None:
    initial_plan()


# ---- Safe-route steps (after the guardian deflected something) ------------------------------
# Fixed words, chosen in code by what the guardian found: never the owner's words.

# They pass the step guard like every step, so they avoid the words it watches for (a secret's name,
# a fee): they describe the safe route without naming the risky one.
SAFE_ROUTES = {
    "secret": ("Keeping private details safe",
               "Say kindly that private details like that are safest kept in their own vault, not in a "
               "conversation, and offer to put it there."),
    "someone_else": ("Someone asking for money or changes",
                     "Say kindly that real banks and insurers don't ask for money or changes this way; suggest "
                     "phoning them on the number in their own papers, or talking to family first."),
}


# ---- Building the plan ------------------------------------------------------------------


def _kept_kinds(owner_id: str) -> set[str]:
    kinds = {m.get("kind") for m in knowledge.items(owner_id, "moments")}
    if knowledge.items(owner_id, "people"):
        kinds.add("person")
    if knowledge.items(owner_id, "inventory"):
        kinds.add("inventory")
    return {k for k in kinds if k}


def _step(step_id: str, source: str, topic: str, ask: str, opens: Opens, ref: str) -> dict:
    return {"id": step_id, "source": source, "topic": topic, "ask": ask, "opens": opens, "ref": ref}


def _candidates(owner_id: str, plan: dict, now: float) -> list[dict]:
    steps = [s for s in plan.get("steps", []) if s["source"] == "safe_route" and now - s.get("at", 0) < SAFE_STEP_SECONDS
             and s["id"] not in plan.get("finished", [])]
    finished = set(plan.get("finished", []))
    kept = _kept_kinds(owner_id)
    for s in initial_plan().steps:
        if s.done_when not in kept and f"initial:{s.id}" not in finished:
            steps.append(_step(f"initial:{s.id}", "initial", s.topic, s.ask, s.opens, s.id))
    for g in gaps.due(owner_id, n=3, now=now):
        # A gap that would hurt the family most is raised by Saathi; a smaller one waits for the owner.
        opens: Opens = "saathi" if g["harm"] in ("high", "medium") else "owner"
        steps.append(_step(f"gap:{g['key']}", "gap", g["text"], g["text"], opens, g["key"]))
    for t in tasks.open_tasks(owner_id):
        if t.get("action") in ("talk", "family") and t.get("question_for_owner"):
            steps.append(_step(f"task:{t['id']}", "suggestion", t.get("title") or t["question_for_owner"],
                               t["question_for_owner"], "owner", t["id"]))
    return [s for s in steps if s["id"] not in finished]


def refresh(owner_id: str, cause: dict, *, add: list[dict] | None = None, now: float | None = None) -> dict:
    """Work the plan out again, with what caused it (e.g. {"kind": "keep", "ref": card id}), and
    save it as a new version. `add` puts safe-route steps first. If the fraud guides can't be read,
    the stored plan stays as it is."""
    now = time.time() if now is None else now
    plan = store.get("conversation_plans", owner_id) or {"id": owner_id, "owner_id": owner_id, "version": 0,
                                                          "steps": [], "finished": []}
    if add:
        plan = {**plan, "steps": [*[{**s, "at": now} for s in add], *plan["steps"]]}
    candidates = _candidates(owner_id, plan, now)
    # Each step passes the guardian's next-step check, as a step Saathi would talk about in the app.
    typed = [{**s, "title": s["topic"], "question_for_owner": s["ask"], "type": "talk_more",
              "channel": "anvaya_app", "flags": []} for s in candidates]
    try:
        allowed = {s["id"] for s in step_guard.pursue(owner_id, typed)}
    except step_guard.TacticsUnavailable:
        return plan
    seen: set[str] = set()
    steps = []
    for s in candidates:
        if s["id"] in allowed and s["id"] not in seen:
            seen.add(s["id"])
            steps.append(s)
    steps = steps[:MAX_STEPS]
    version = plan["version"] + 1
    saved = store.put("conversation_plans", {**plan, "version": version, "at": now, "cause": cause, "steps": steps,
                                             "current": steps[0]["id"] if steps else None})
    # The audit record: ids, sources and who starts each step; no words.
    store.put("plan_versions", {
        "id": f"{owner_id}:{version}", "owner_id": owner_id, "version": version, "at": now, "cause": cause,
        "steps": [{"id": s["id"], "source": s["source"], "opens": s["opens"]} for s in steps],
        "current": saved["current"],
        "expire_at": datetime.fromtimestamp(now + VERSIONS_KEPT_DAYS * 86400, timezone.utc)})
    return saved


def current(owner_id: str, now: float | None = None) -> dict:
    """The owner's plan, worked out first if there's none yet (a cold start) or it's over an hour old."""
    now = time.time() if now is None else now
    plan = store.get("conversation_plans", owner_id)
    if not plan:
        return refresh(owner_id, {"kind": "cold_start"}, now=now)
    if now - plan.get("at", 0) > STALE_SECONDS:
        return refresh(owner_id, {"kind": "time"}, now=now)
    return plan


def view(plan: dict) -> dict:
    """What the Talk screen shows: the current step and the ones after it."""
    steps = [{k: s[k] for k in ("id", "source", "topic", "ask", "opens")} for s in plan.get("steps", [])]
    return {"version": plan.get("version", 0), "current": steps[0] if steps else None, "next": steps[1:]}


# ---- What changes the plan ----------------------------------------------------------------


def after_statements(owner_id: str, turn_id: str, decisions: list[tuple[str, str, str | None]]) -> dict | None:
    """The guardian's decisions on what the owner said in one turn (guardian_inputs.screen): a
    deflected statement adds its safe-route step first. `decisions` are (action id, response,
    safe kind). The plan changes only when something was held back."""
    held = [(aid, response, kind) for aid, response, kind in decisions if response in ("deflect", "block")]
    if not held:
        return None
    add = []
    for _, _, kind in held:
        if kind and kind in SAFE_ROUTES and not any(s["ref"] == kind for s in add):
            topic, ask = SAFE_ROUTES[kind]
            add.append(_step(f"safe:{kind}:{turn_id}", "safe_route", topic, ask, "saathi", kind))
    return refresh(owner_id, {"kind": "guardian", "ref": held[0][0], "turn": turn_id}, add=add)


def step_outcome(owner_id: str, step_id: str, outcome: str) -> dict:
    """How the owner answered a step (Saathi reports it from PR-2.12.3). An answered safe-route
    step, or an initial step the owner declined or put off, leaves the plan; a kept item finishes
    an initial step on its own."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    plan = current(owner_id)
    step = next((s for s in plan.get("steps", []) if s["id"] == step_id), None)
    if step and (step["source"] == "safe_route" or (step["source"] == "initial" and outcome != "answered")):
        store.update("conversation_plans", owner_id, {"finished": [*plan.get("finished", []), step_id]})
    return refresh(owner_id, {"kind": "outcome", "ref": step_id, "outcome": outcome})
