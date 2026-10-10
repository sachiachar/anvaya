"""The conversation planner (PR-2.12.2): the only source of what Saathi talks about with the owner.

Design: docs/proposals/guardian-led-conversations.md. One stored plan per owner, built in code:

  1. safe-route steps, after the guardian deflected something the owner said (this conversation);
  2. the initial plan's steps not done yet (agents/conversation_plan.yaml, reviewed like code);
  3. the gaps that are due (gaps.due: harm-ranked, with reminder waits);
  4. the readiness planner's suggestions Saathi can talk about.

The gap finder and the readiness planner feed only this; Saathi follows the current step
(PR-2.12.3). Each step says who starts it (Saathi opens with it, or Saathi waits for the owner)
and its tone. The owner's mood, which the reader of each turn reports from a closed list, can
change the tone during the conversation, put a listening step first, or hold back practical steps:
the planner decides that in code, never Saathi. Every step passes the guardian's next-step check
(step_guard.pursue) before it joins the plan.

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
MAX_SAATHI_STEPS = 5   # steps Saathi opens, in order
MAX_OWNER_STEPS = 3    # steps that wait for the owner; they keep their own room
STALE_SECONDS = 3600            # a plan read after this long is worked out again (gaps fall due over time)
SAFE_STEP_SECONDS = gaps.CONVERSATION_GAP  # a safe-route step belongs to the conversation it came from
VERSIONS_KEPT_DAYS = 30         # each version's expire_at; a Firestore TTL policy removes it (deploy.yml)
OUTCOMES = ("answered", "unsure", "not_now", "declined")
Opens = Literal["saathi", "owner"]
Tone = Literal["warm", "gentle", "light", "clear", "reassuring", "patient"]
DONE_KINDS = ("person", "story", "advice", "practical", "wish", "inventory")
MOODS = ("calm", "happy", "sad", "anxious", "tired", "upset")

# How Saathi speaks in each tone: the words it's given with the step.
TONE_WORDS = {
    "warm": "Warm and friendly, at an easy pace; show real interest.",
    "gentle": "Extra empathy: slow down, acknowledge their feelings first, never press, and leave room for silence.",
    "light": "Glad and appreciative: let the happy memory breathe and share their joy briefly.",
    "clear": "Simple and practical: one thing at a time, in plain words.",
    "reassuring": "Calm and steady: no alarm and no blame; offer the safe step simply.",
    "patient": "Short sentences; repeat kindly if needed, check they're comfortable, and offer to stop.",
}
# The tone a gap gets, by its kind: the ones about the family's future are gentle, the rest clear.
GAP_TONES = {"will": "gentle", "heir": "gentle", "conflict": "gentle", "first_call": "gentle"}


# ---- The initial plan -------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InitialStep(_Strict):
    id: str
    topic: str
    ask: str
    done_when: Literal["person", "story", "advice", "practical", "wish", "inventory"]
    opens: Opens
    tone: Tone

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
# Steps the owner's mood puts first, for this conversation (fixed words, like the safe routes).
# When another voice is heard (PR-2.13.2): once a conversation, warmly, without suspicion.
VOICE_STEP = ("Who's with you", "Warmly ask who else is there with them, without any suspicion, and carry on once they "
              "say.", "warm")
FIRST_STEPS = ("safe_route", "voice")  # added for this conversation, kept first while it lasts

MOOD_STEPS = {
    "sad": ("Listening", "Just listen. Acknowledge how they feel in a sentence, and ask nothing new unless they "
            "want to go on.", "gentle"),
    "upset": ("Listening", "Just listen. Acknowledge how they feel in a sentence, and ask nothing new unless they "
              "want to go on.", "gentle"),
    "tired": ("A rest", "Say kindly that they've shared a lot today, and offer to stop here; nothing is lost.",
              "patient"),
}

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


def _step(step_id: str, source: str, topic: str, ask: str, opens: Opens, ref: str, tone: str = "warm") -> dict:
    return {"id": step_id, "source": source, "topic": topic, "ask": ask, "opens": opens, "ref": ref, "tone": tone}


def mood_now(plan: dict, now: float) -> str:
    """The owner's mood in this conversation, as last reported; calm once the conversation is over."""
    mood = plan.get("mood") or {}
    return mood.get("label", "calm") if now - mood.get("at", 0) < SAFE_STEP_SECONDS else "calm"


def _with_mood(steps: list[dict], mood: str, turn: str) -> list[dict]:
    """The owner's mood, applied in code: sad or upset puts a listening step first and holds back
    practical steps for this conversation; tired puts an offer to stop first and makes every step
    patient; happy makes a warm step light. Safe-route steps always stay, first."""
    safe = [s for s in steps if s["source"] in FIRST_STEPS]
    rest = [s for s in steps if s["source"] not in FIRST_STEPS]
    if mood in ("sad", "upset"):
        rest = [{**s, "tone": "gentle"} for s in rest if s["tone"] != "clear"]
    elif mood == "tired":
        rest = [{**s, "tone": "patient"} for s in rest]
    elif mood == "happy":
        rest = [{**s, "tone": "light"} if s["tone"] == "warm" else s for s in rest]
    first = []
    if mood in MOOD_STEPS:
        topic, ask, tone = MOOD_STEPS[mood]
        first = [_step(f"mood:{mood}:{turn}", "mood", topic, ask, "saathi", mood, tone)]
    # Steps Saathi may open come first, so a step that waits for the owner never holds them up; the
    # owner's steps stay in the plan, for when they bring them up. The order within each is kept.
    rest = [s for s in rest if s["opens"] == "saathi"] + [s for s in rest if s["opens"] == "owner"]
    return [*safe, *first, *rest]


def _candidates(owner_id: str, plan: dict, now: float) -> list[dict]:
    steps = [s for s in plan.get("steps", []) if s["source"] in FIRST_STEPS and now - s.get("at", 0) < SAFE_STEP_SECONDS
             and s["id"] not in plan.get("finished", [])]
    finished = set(plan.get("finished", []))
    kept = _kept_kinds(owner_id)
    for s in initial_plan().steps:
        if s.done_when not in kept and f"initial:{s.id}" not in finished:
            steps.append(_step(f"initial:{s.id}", "initial", s.topic, s.ask, s.opens, s.id, s.tone))
    for g in gaps.due(owner_id, n=3, now=now):
        # A gap that would hurt the family most is raised by Saathi; a smaller one waits for the owner.
        opens: Opens = "saathi" if g["harm"] in ("high", "medium") else "owner"
        steps.append(_step(f"gap:{g['key']}", "gap", g["text"], g["text"], opens, g["key"], GAP_TONES.get(g["kind"], "clear")))
    for t in tasks.open_tasks(owner_id):
        if t.get("action") in ("talk", "family") and t.get("question_for_owner"):
            steps.append(_step(f"task:{t['id']}", "suggestion", t.get("title") or t["question_for_owner"],
                               t["question_for_owner"], "owner", t["id"]))
    steps = [s for s in steps if s["id"] not in finished]
    mood = plan.get("mood") or {}
    return _with_mood(steps, mood_now(plan, now), mood.get("turn", ""))


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
    steps = ([s for s in steps if s["opens"] == "saathi"][:MAX_SAATHI_STEPS]
             + [s for s in steps if s["opens"] == "owner"][:MAX_OWNER_STEPS])
    version = plan["version"] + 1
    saved = store.put("conversation_plans", {**plan, "version": version, "at": now, "cause": cause, "steps": steps,
                                             "current": steps[0]["id"] if steps else None})
    # The audit record: ids, sources and who starts each step; no words.
    store.put("plan_versions", {
        "id": f"{owner_id}:{version}", "owner_id": owner_id, "version": version, "at": now, "cause": cause,
        "steps": [{"id": s["id"], "source": s["source"], "opens": s["opens"], "tone": s["tone"]} for s in steps],
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
    """What the Talk screen and Saathi see: the current step and the ones after it, each with who
    starts it and its tone in words."""
    steps = [{**{k: s.get(k) for k in ("id", "source", "topic", "ask", "opens")}, "tone": s.get("tone", "warm"),
              "tone_words": TONE_WORDS[s.get("tone", "warm")]} for s in plan.get("steps", [])]
    return {"version": plan.get("version", 0), "current": steps[0] if steps else None, "next": steps[1:]}


# ---- What changes the plan ----------------------------------------------------------------


def after_turn(owner_id: str, turn_id: str, decisions: list[tuple[str, str, str | None]], *, mood: str = "calm",
               answered_step: str | None = None, outcome: str = "none", another_voice: bool = False,
               now: float | None = None) -> dict:
    """Everything one turn changes, in one new version (PR-2.12.3): a deflected statement's safe
    route, a change in the owner's mood, and how they answered the step Saathi carried out. Without
    any of these the plan stays as it is (still worked out again if it's stale)."""
    now = time.time() if now is None else now
    plan = current(owner_id, now=now)
    cause: dict = {"kind": "turn", "turn": turn_id}
    held = [(aid, kind) for aid, response, kind in decisions if response in ("deflect", "block")]
    add = []
    for _, kind in held:
        if kind and kind in SAFE_ROUTES and not any(s["ref"] == kind for s in add):
            topic, ask = SAFE_ROUTES[kind]
            add.append(_step(f"safe:{kind}:{turn_id}", "safe_route", topic, ask, "saathi", kind, "reassuring"))
    if held:
        cause["guardian"] = held[0][0]
    # Another voice: Saathi warmly asks who's there, once a conversation (PR-2.13.2).
    recent_voice = any(s["source"] == "voice" and now - s.get("at", 0) < SAFE_STEP_SECONDS for s in plan.get("steps", []))
    if another_voice and not recent_voice:
        topic, ask, tone = VOICE_STEP
        add.append(_step(f"voice:{turn_id}", "voice", topic, ask, "saathi", "voice", tone))
        cause["voice"] = "another"
    mood = mood if mood in MOODS else "calm"
    if mood != mood_now(plan, now):
        store.update("conversation_plans", owner_id, {"mood": {"label": mood, "at": now, "turn": turn_id}})
        cause["mood"] = mood
    elif mood != "calm":  # the same mood again keeps it current for this conversation
        store.update("conversation_plans", owner_id, {"mood": {**plan.get("mood", {}), "at": now}})
    step = next((s for s in plan.get("steps", []) if s["id"] == answered_step), None) if answered_step else None
    if step and outcome in OUTCOMES:
        cause.update(step=step["id"], outcome=outcome)
        # An answered safe-route or mood step has done its job; so has an initial step the owner
        # declined or put off (a kept item finishes one on its own).
        done_once = step["source"] in ("safe_route", "mood", "voice")
        if done_once or (step["source"] == "initial" and outcome in ("not_now", "declined")):
            fresh = store.get("conversation_plans", owner_id) or plan
            store.update("conversation_plans", owner_id, {"finished": [*fresh.get("finished", []), step["id"]]})
    if len(cause) == 2:
        return plan
    return refresh(owner_id, cause, add=add, now=now)


def after_statements(owner_id: str, turn_id: str, decisions: list[tuple[str, str, str | None]]) -> dict | None:
    """The guardian's decisions on one turn alone: a deflected statement adds its safe-route step
    first. `decisions` are (action id, response, safe route). None when nothing was held back."""
    if not any(response in ("deflect", "block") for _, response, _ in decisions):
        return None
    return after_turn(owner_id, turn_id, decisions)


def step_outcome(owner_id: str, step_id: str, outcome: str) -> dict:
    """How the owner answered a step, on its own (Saathi's report_step tool in a call, PR-2.12.3)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    return after_turn(owner_id, "", [], answered_step=step_id, outcome=outcome)
