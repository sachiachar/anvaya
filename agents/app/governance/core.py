"""Governance layer: the shared core.

One layer governs every agent (docs/proposals/agent-governance.md, architecture §4.1):

- **Registry** (registry.yaml): what each agent is: its identity, purpose and the models it
  tries, in order. **Policies** (policies.yaml): what it may do: its autonomy, allowed models
  and tools, limits and budget pool. Checked against each other at start-up (files.py).
- **Ledger**: usage per day, per agent, model and tenant, recorded by the gateway (PR-10).
- **Guardian events**: interventions recorded by the guardian (PR-2.7), reviewed by people.
- **Kill switch**, **roles** and the **audit log** for every human decision.

The gateway, the guardian and the platform admin portal (PR-12) all use this core, so
there is one record of what agents did, what was stopped, and who decided what.
Nothing here holds conversation text or names: users appear as hashed keys.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import yaml
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..store import new_id, store
from . import files

ROLES = ("viewer", "demo_admin", "budget_approver", "risk_reviewer", "platform_admin")
PERMISSIONS: dict[str, set[str]] = {
    "viewer": set(),
    # A visitor with their own demo copy (PR-12.9): decides their own copy's budget requests only
    # (portal.py scopes every list and decision to the copy); everything else read-only.
    "demo_admin": {"approve_budget"},
    "budget_approver": {"approve_budget"},
    "risk_reviewer": {"review_guardian", "review_fraud"},
    "platform_admin": {"approve_budget", "review_guardian", "review_fraud", "manage_demos", "kill_switch",
                       "manage_privacy", "manage_model_access"},
}
KILL_SWITCH_STATES = ("off", "optional_paused", "all_paused")
SMALL_COUNT = 5
REGISTRY_PATH = pathlib.Path(__file__).resolve().parents[2] / "registry.yaml"
POLICIES_PATH = REGISTRY_PATH.with_name("policies.yaml")


# ---- Who is asking -----------------------------------------------------------


class Actor(BaseModel):
    email: str | None
    key: str
    role: str
    permissions: list[str]


def user_key(uid: str) -> str:
    """A stable key that identifies a user in the portal without naming them."""
    return "u_" + hashlib.sha256(f"{settings.user_key_salt}:{uid}".encode()).hexdigest()[:10]


def role_for(email: str | None) -> str:
    if email is None:  # sign-in switched off: local development and tests
        return "platform_admin"
    email = email.strip().lower()
    if email in settings.platform_roles:
        return settings.platform_roles[email]
    for entry, role in settings.platform_roles.items():
        if entry.startswith("@") and email.endswith(entry):
            return role
    # With open sign-in anyone can get in (PR-15.2), so an account with no listed role is the admin
    # of its own copy only, even before the copy exists; never a viewer of everyone's records.
    if settings.open_sign_in and settings.platform_default_role == "viewer":
        return "demo_admin"
    return settings.platform_default_role


def actor(request: Request) -> Actor:
    user = getattr(request.state, "user", None)
    email = user.email if user else None
    role = role_for(email)
    if role not in ROLES:
        raise HTTPException(403, "You don't have access to the platform admin portal.")
    key = user_key(user.uid if user else "local")
    if role == "viewer" and store.get("demo_copies", key):
        role = "demo_admin"  # the platform admin of their own demo copy, and nothing more
    return Actor(email=email, key=key, role=role, permissions=sorted(PERMISSIONS[role]))


def need(permission: str):
    def check(who: Actor = Depends(actor)) -> Actor:
        if permission not in who.permissions:
            raise HTTPException(403, f"Your role ({who.role.replace('_', ' ')}) can't do this.")
        return who
    return check


def audit(who: Actor, action: str, target: str, reason: str | None = None, **detail) -> None:
    store.put("platform_audit", {"id": new_id("aud"), "at": time.time(), "actor": who.email or "local",
                                 "actor_key": who.key, "role": who.role, "action": action, "target": target,
                                 "reason": reason, "detail": detail})


def small(n: int) -> int | None:
    """Hide counts under 5 so nobody can be singled out; the UI shows 'fewer than 5'."""
    return n if n >= SMALL_COUNT else None


# ---- Registry and settings ----------------------------------------------------


@lru_cache(maxsize=1)
def _files() -> tuple[dict, dict]:
    """Both files, checked against their schemas and each other; a problem stops start-up.
    The gateway reads the validated objects, never the raw YAML, so what it enforces is
    exactly what passed the checks. Unset optional fields are left out, so a missing limit
    stays missing rather than becoming None."""
    reg, pol = files.check(yaml.safe_load(REGISTRY_PATH.read_text()), yaml.safe_load(POLICIES_PATH.read_text()))
    return reg.model_dump(exclude_none=True), pol.model_dump(exclude_none=True)


def check_files() -> None:
    """Called when the API starts, so a broken registry or policies file stops it."""
    _files()


def registry() -> list[dict]:
    """What each agent is (registry.yaml)."""
    return _files()[0]["agents"]


def policies() -> list[dict]:
    """What each agent may do (policies.yaml)."""
    return _files()[1]["policies"]


def allowances() -> dict[str, dict]:
    """Daily allowance per person for each budget pool (policies.yaml, PR-10.4)."""
    return _files()[1].get("user_allowances", {})


def platform_settings() -> dict:
    return store.get("platform_settings", "settings") or {}


# ---- Gateway checks ------------------------------------------------------------


class Refused(Exception):
    """The gateway refused a call: not in the registry, paused by the kill switch, over a
    limit, or over the person's daily allowance."""


def agent_entry(agent: str) -> dict | None:
    """The agent's registry entry: what it is, and the models it tries in order."""
    return next((a for a in registry() if a["id"] == agent), None)


def policy(agent: str) -> dict | None:
    """The agent's policy: what it may do. None means it may do nothing."""
    return next((p for p in policies() if p["agent"] == agent), None)


def models_in_order(agent: str) -> list[str]:
    """The models the agent tries, primary first (registry.yaml); never an environment variable.
    An agent that isn't registered is refused and recorded, as check_call would."""
    entry = agent_entry(agent)
    if entry is None:
        record_blocked(agent, "agent", agent, "Agent not in the registry or without a policy")
        raise Refused(f"{agent} is not a registered agent.")
    models = entry["models"]
    return [models["primary"], *models.get("fallback", [])]


def primary_model(agent: str) -> str:
    return models_in_order(agent)[0]


def model_kind(model: str) -> str | None:
    """text, live or speech, from the registry's model catalogue."""
    return next((m["kind"] for m in _files()[0]["models"] if m["id"] == model), None)


def autonomy(agent: str) -> str:
    """How much the agent may do on its own (assist, propose or act_within_limits), from its policy;
    the label the app shows on each card, step and check (PR-15.7). Unknown agents only assist."""
    return ((policy(agent) or {}).get("role") or {}).get("autonomy", "assist")


def thinking(agent: str) -> str | None:
    """How much the agent may think, if its policy sets it."""
    return ((policy(agent) or {}).get("models") or {}).get("thinking")


def check_call(agent: str, model: str) -> None:
    """Refuse a model call the agent's policy doesn't allow, or that the kill switch pauses."""
    entry, rules = agent_entry(agent), policy(agent)
    if entry is None or rules is None:
        record_blocked(agent, "agent", agent, "Agent not in the registry or without a policy")
        raise Refused(f"{agent} is not a registered agent.")
    if entry["status"] != "active":
        record_blocked(agent, "agent", agent, f"Agent is {entry['status']}, not active")
        raise Refused(f"{agent} is not active.")
    if model not in rules["models"]["allow"]:
        record_blocked(agent, "model", model, "Model not allowed for this agent")
        raise Refused(f"{agent} may not use {model}.")
    state = kill_switch()
    paused = (state == "all_paused" and not entry.get("safety_critical")) or \
             (state == "optional_paused" and entry.get("optional"))
    if paused:
        record_blocked(agent, "kill_switch", state, "Paused by the kill switch")
        raise Refused("This feature is paused right now. Please try again later.")
    check_allowance(agent)


def check_tool(agent: str, tool: str) -> None:
    """Refuse a tool the agent's policy doesn't grant, e.g. web search."""
    if tool not in [g["tool"] for g in (policy(agent) or {}).get("tools", [])]:
        record_blocked(agent, "tool", tool, "Tool not allowed for this agent")
        raise Refused(f"{agent} may not use {tool}.")


def limit(agent: str, key: str, default: int | None = None) -> int | None:
    """One of the agent's limits from its policy, per call or per run (PR-10.2)."""
    consumption = (policy(agent) or {}).get("consumption", {})
    for group in ("call", "run"):
        if key in consumption.get(group, {}):
            return consumption[group][key]
    return default


def estimate_tokens(chars: int, media: int = 0) -> int:
    """A quick estimate before the call: about 4 characters per token, about 300 tokens per image or clip."""
    return chars // 4 + media * 300


def check_input(agent: str, estimated_tokens: int) -> None:
    """Refuse a request larger than the agent's max_input_tokens, before anything is spent."""
    cap = limit(agent, "max_input_tokens")
    if cap is not None and estimated_tokens > cap:
        record_blocked(agent, "limit", "max_input_tokens",
                       f"Request of about {estimated_tokens:,} tokens is over the limit of {cap:,}")
        raise Refused("This request is too large. Please try a shorter message or a smaller file.")


def on_vertex() -> bool:
    """Request labels (billing per agent) work only on Vertex AI, not with a Gemini API key."""
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"


def labels(agent: str, tenant: str = "direct") -> dict[str, str]:
    """Labels on each Gemini request, so the Google Cloud bill breaks down by agent and tenant.
    Label values allow only lowercase letters, digits, '-' and '_'."""
    def clean(v: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in v.lower())[:63]
    return {"agent": clean(agent), "tenant": clean(tenant), "app": "anvaya"}


# ---- Daily allowances per person (PR-10.4) ----------------------------------------
#
# Each person has a daily allowance per budget pool (policies.yaml "user_allowances"), so one
# person can't use up capacity meant for everyone. The safety pool (certificate checks,
# the guardian) is never limited or counted against anyone, so cancelling a false report
# or checking a certificate always works. Approved requests in the platform admin portal
# raise a person's allowance until they expire.

_current_user: ContextVar[str | None] = ContextVar("current_user", default=None)
WARN_AT = 0.8
# Which measure each agent counts against; everything else counts tokens.
METRIC = {"saathi.voice": "voice_minutes", "saathi.speech": "spoken_replies"}
# Approved request kinds and the measure they raise (daily_allowance is in thousands of tokens).
GRANT_METRIC = {"daily_allowance": ("tokens", 1000), "voice_minutes": ("voice_minutes", 1),
                "spoken_replies": ("spoken_replies", 1)}


# Asking for more (PR-10.6): what a person may ask for in one request, and for how long.
REQUEST_LIMITS = {"daily_allowance": 300, "voice_minutes": 60, "spoken_replies": 100}  # thousands of tokens, minutes, replies
REQUEST_MAX_HOURS = 72


class RequestRefused(ValueError):
    """A request for more that isn't allowed (too much, too long, or one already waiting)."""


def request_more(user: str, kind: str, amount: int, hours: int, reason: str, *, requested_by: str,
                 copy_id: str, tenant: str = "direct") -> dict:
    """A person asks for more of today's allowance. A person in the admin portal decides
    (approvals); an approved request raises the allowance until it expires."""
    if kind not in REQUEST_LIMITS:
        raise RequestRefused("unknown kind of allowance")
    if not 1 <= amount <= REQUEST_LIMITS[kind]:
        raise RequestRefused(f"ask for between 1 and {REQUEST_LIMITS[kind]}")
    if not 1 <= hours <= REQUEST_MAX_HOURS:
        raise RequestRefused(f"ask for between 1 and {REQUEST_MAX_HOURS} hours")
    # One at a time per kind, waiting or still running, so extra allowance can never add up past one
    # request's limit (a demo admin approves their own copy's requests, PR-12.9).
    now = time.time()
    for r in store.list("budget_requests", user_key=user, kind=kind):
        if r["status"] == "pending":
            raise RequestRefused("a request for this is already waiting")
        if r["status"] == "approved" and r.get("expires_at", 0) > now:
            raise RequestRefused("you already have extra allowance for this until it expires")
    return store.put("budget_requests", {"id": new_id("req"), "at": time.time(), "tenant": tenant, "user_key": user,
                                         "copy_id": copy_id, "kind": kind, "amount": amount, "hours": hours,
                                         "reason": reason, "requested_by": requested_by, "status": "pending"})


def my_requests(user: str) -> list[dict]:
    """A person's requests that are waiting, or approved and still running, newest first."""
    now = time.time()
    mine = [r for r in store.list("budget_requests", user_key=user)
            if r["status"] == "pending" or (r["status"] == "approved" and r.get("expires_at", 0) > now)]
    return sorted(({k: r.get(k) for k in ("id", "at", "kind", "amount", "hours", "status", "expires_at")} for r in mine),
                  key=lambda r: -r["at"])


def set_current_user(key: str | None) -> None:
    """Called once at sign-in for each request or live call; the gateway reads it anywhere."""
    _current_user.set(key)


def current_user() -> str | None:
    return _current_user.get()


def _pool(agent: str) -> str:
    return (policy(agent) or {}).get("consumption", {}).get("per_user", {}).get("pool", "owner")


def _usage_today(user: str) -> dict:
    return store.get("user_usage", f"{_today()}:{user}") or {}


def _granted(user: str, metric: str) -> float:
    now, extra = time.time(), 0.0
    for r in store.list("budget_requests", user_key=user, status="approved"):
        kind = GRANT_METRIC.get(r.get("kind", ""))
        if kind and kind[0] == metric and r.get("expires_at", 0) > now:
            extra += r["amount"] * kind[1]
    return extra


def allowance_status(user: str | None = None) -> dict:
    """Today's use against the allowance, per pool and measure: used, limit, share and state."""
    user = user or current_user()
    used_today = _usage_today(user) if user else {}
    out: dict[str, dict] = {}
    for pool, limits in allowances().items():
        for metric, base in (limits or {}).items():
            metric = metric.removesuffix("_per_day")
            cap = base + (_granted(user, metric) if user else 0)
            used = used_today.get(pool, {}).get(metric, 0)
            share = used / cap if cap else 0.0
            out.setdefault(pool, {})[metric] = {
                "used": round(used, 1), "limit": cap, "share": round(share, 3),
                "state": "reached" if share >= 1 else "warning" if share >= WARN_AT else "ok"}
    return out


def remaining(agent: str) -> float | None:
    """What's left today for this agent's pool and measure, or None when there's no limit."""
    user, pool = current_user(), _pool(agent)
    limits = allowances().get(pool)
    if not user or not limits:
        return None
    status = allowance_status(user).get(pool, {}).get(METRIC.get(agent, "tokens"))
    return None if status is None else max(0.0, status["limit"] - status["used"])


def check_allowance(agent: str) -> None:
    """Refuse a call when the person has used today's allowance for the agent's pool. Not when
    their own model key will answer first (PR-10.10): their testing costs us nothing. If their key
    fails and the call moves to ours, the key chain checks again (check_allowance_now)."""
    from . import keys  # keys uses this module

    if not keys.counts_against_allowance():
        return
    check_allowance_now(agent)


def check_allowance_now(agent: str) -> None:
    """The allowance check itself, whatever key the person has: used when a call falls back to ours.
    The service's daily ceiling is checked first (PR-15.1)."""
    check_ceiling(agent)
    left = remaining(agent)
    if left is not None and left <= 0:
        allowance_reached(agent)
        raise Refused("You've used today's allowance for this. It resets tomorrow, or you can ask for more.")


def _add(agent: str, amount: float, day: str | None = None) -> None:
    """Add to (or, with a negative amount, take from) the person's counter for the day (today
    unless given), atomically."""
    user, pool, day = current_user(), _pool(agent), day or _today()
    if not user or not allowances().get(pool):
        return
    store.increment("user_usage", f"{day}:{user}", {pool: {METRIC.get(agent, "tokens"): amount}},
                    {"day": day, "user_key": user})


def count_for_user(agent: str, *, tokens: int = 0, minutes: float = 0.0) -> None:
    """Add one call to the person's counters for today (not for the safety pool)."""
    metric = METRIC.get(agent, "tokens")
    _add(agent, {"tokens": tokens, "voice_minutes": round(minutes, 2), "spoken_replies": 1}[metric])


def reserve(agent: str, amount: float) -> str:
    """Hold part of the allowance and the service ceiling before a long call (live voice), so a
    second call at the same time sees only what's really left. Returns the day it's held on: pass
    it to release() when the call ends, so a call that runs past midnight gives back what it held
    on the right day. The actual use is counted separately."""
    day = _today()
    _add(agent, amount, day)
    _add_service({_service_metric(agent): amount}, day)
    return day


def release(agent: str, amount: float, day: str | None = None) -> None:
    _add(agent, -amount, day)
    _add_service({_service_metric(agent): -amount}, day)


# ---- A daily ceiling for the whole service (PR-15.1) ----------------------------------
#
# With open sign-in (PR-15.2) anyone can use the demo, so besides each person's allowance there
# is one daily ceiling on everything our key spends (policies.yaml "service_ceiling"): tokens, and
# voice minutes. As it fills, optional agents (voice, spoken replies, summaries) stop first, at
# optional_pause_at. Every other agent stops when only the safety reserve is left. Safety-critical
# agents (certificate checks, the guardian) are never refused: the reserve is kept for them, and
# a handover must never wait on the budget. Calls on a person's own key don't count.
#
# Each agent is limited by one measure: live voice by voice minutes, every other agent by tokens.
# Live voice's tokens still count toward the tokens ceiling, because our key pays for them.

STATES = ("ok", "optional_paused", "paused")
# Agents that only ever call a person's own key, never ours: testing a key before it's saved.
OWN_KEY_ONLY = {"access.tester"}


def service_ceiling() -> dict:
    return _files()[1].get("service_ceiling") or {}


def _service_metric(agent: str) -> str:
    return "voice_minutes" if METRIC.get(agent) == "voice_minutes" else "tokens"


def _service_today() -> dict:
    return store.get("service_usage", _today()) or {}


def service_status() -> dict:
    """Today's use of the service ceiling, per measure: used, limit, share and state (ok,
    optional_paused: optional agents limited by this measure stopped; paused: all agents limited by
    it stopped, except safety-critical ones). Live minutes still held by calls count as used.
    "state" is the worst of the measures."""
    ceiling, used_today = service_ceiling(), _service_today()
    out: dict = {"optional_pause_at": ceiling.get("optional_pause_at"),
                 "safety_reserve": ceiling.get("safety_reserve"), "state": "ok"}
    for metric in ("tokens", "voice_minutes"):
        cap = ceiling.get(f"{metric}_per_day")
        if not cap:
            continue
        used = used_today.get(metric, 0)
        share = used / cap
        state = "paused" if share >= 1 - ceiling["safety_reserve"] else \
                "optional_paused" if share >= ceiling["optional_pause_at"] else "ok"
        out[metric] = {"used": round(used, 1), "limit": cap, "share": round(share, 3), "state": state}
        out["state"] = max(out["state"], state, key=STATES.index)
    return out


def service_remaining(agent: str) -> float | None:
    """What this agent may still use today under the ceiling, in its measure, or None when nothing
    limits it (no ceiling set, a safety-critical agent, or one that never uses our key)."""
    ceiling, entry = service_ceiling(), agent_entry(agent) or {}
    cap = ceiling.get(f"{_service_metric(agent)}_per_day")
    if not cap or entry.get("safety_critical") or agent in OWN_KEY_ONLY:
        return None
    line = ceiling["optional_pause_at"] if entry.get("optional") else 1 - ceiling["safety_reserve"]
    return max(0.0, cap * line - _service_today().get(_service_metric(agent), 0))


def check_ceiling(agent: str) -> None:
    """Refuse a call on our key once the service has used what this agent may use today."""
    left = service_remaining(agent)
    if left is not None and left <= 0:
        ceiling_reached(agent)
        raise Refused("This is paused for today: the demo has reached its daily limit. It resets tomorrow.")


def _add_service(amounts: dict[str, float], day: str | None = None) -> None:
    """Add to (or take from) the service's counters for the day (today unless given), atomically."""
    amounts, day = {k: v for k, v in amounts.items() if v}, day or _today()
    if amounts and service_ceiling():
        store.increment("service_usage", day, amounts, {"day": day})


# ---- Recording (used by the gateway and the guardian) -------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_usage(agent: str, model: str, tenant: str, *, tokens_in: int = 0, tokens_out: int = 0,
                 minutes: float = 0.0, error: bool = False, key_source: str = "platform") -> None:
    """Add one model call to today's ledger (the gateway calls this after every call).

    `key_source` is whose key answered (keys.py, PR-10.10): "platform", or a person's own
    ("judge_studio", "judge_vertex"). Only calls on the platform key count against the
    person's daily allowance and the service's daily ceiling.

    An atomic increment, so calls that finish at the same time (on several server instances)
    never overwrite each other's counts in the portal's usage figures. Dollars come only
    from Google Cloud billing (billing.py), never from the ledger.
    """
    day_id = _today()
    store.increment("usage_days", day_id, {
        "agents": {agent: {"calls": 1, "tokens_in": tokens_in, "tokens_out": tokens_out, "minutes": minutes,
                           "errors": int(error)}},
        # "model_use", not "models": older days hold a dollar figure per model there.
        "model_use": {model: {"calls": 1, "tokens": tokens_in + tokens_out, "minutes": minutes}},
        "tenants": {tenant: {"calls": 1, "tokens": tokens_in + tokens_out, "minutes": minutes}},
        "key_sources": {key_source: {"calls": 1, "tokens": tokens_in + tokens_out, "minutes": minutes}},
    }, {"day": day_id})
    if user := current_user():  # per person and key source, for the portal's Model keys report
        store.increment("key_usage", f"{day_id}:{user}", {key_source: {
            "calls": 1, "tokens": tokens_in + tokens_out, "minutes": minutes, "errors": int(error)}},
            {"day": day_id, "user_key": user})
        # A simple trace for the agents panel (PR-15.7): one line per call, never any text.
        now = time.time()
        store.put("agent_steps", {"id": new_id("stp"), "at": now, "day": day_id, "user_key": user, "agent": agent,
                                  "model": model, "tokens": tokens_in + tokens_out, "minutes": round(minutes, 2),
                                  "result": "failed" if error else "done", "key_source": key_source,
                                  "expire_at": datetime.fromtimestamp(now + STEPS_KEPT_DAYS * 86400, timezone.utc)})
    if key_source == "platform":  # everything our key spends, safety work included (PR-15.1)
        _add_service({"tokens": tokens_in + tokens_out, "voice_minutes": round(minutes, 2)})
    if not error and key_source == "platform":
        count_for_user(agent, tokens=tokens_in + tokens_out, minutes=minutes)


STEPS_SHOWN = 50
STEPS_HOURS = 24


STEPS_KEPT_DAYS = 2  # each line's expire_at; a Firestore TTL policy on it removes old lines (a person sets it)
NOT_STOPPED = {"key_fallback"}  # recorded by the gateway, but the call went on with the next key


def _recent_days() -> list[str]:
    now = datetime.now(timezone.utc)
    return [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in (0, 1)]


def start_trace(user: str) -> None:
    """A reset demo starts with an empty agents panel: older steps are no longer shown."""
    store.put("trace_marks", {"id": user, "from": time.time()})


def agent_steps(user: str) -> dict:
    """The agents panel (PR-15.7): one demo copy's recent AI steps, newest first: the calls that ran
    (done or failed) and those the gateway stopped, with each agent's purpose and autonomy, and how
    many next steps Saathi didn't pursue. Only this copy's, and never any text: only fixed words.
    It reads only today's and yesterday's lines, never the whole history."""
    trace_from = (store.get("trace_marks", user) or {}).get("from", 0)  # the last reset
    since = max(time.time() - STEPS_HOURS * 3600, trace_from)
    ran, stopped = [], []
    for day in _recent_days():
        ran += [s for s in store.list("agent_steps", user_key=user, day=day) if s["at"] >= since]
        stopped += [{"id": b["id"], "at": b["at"], "agent": b["agent"], "model": None, "tokens": 0, "minutes": 0.0,
                     "result": "stopped", "reason": b["reason"], "key_source": None}
                    for b in store.list("gateway_blocked", user_key=user, day=day)
                    if b["at"] >= since and b.get("kind") not in NOT_STOPPED]
    steps = sorted(ran + stopped, key=lambda s: -s["at"])[:STEPS_SHOWN]
    return {
        "steps": [{"at": s["at"], "agent": s["agent"], "purpose": (agent_entry(s["agent"]) or {}).get("purpose"),
                   "model": s["model"], "tokens": s["tokens"], "minutes": s["minutes"],
                   "autonomy": autonomy(s["agent"]), "result": s["result"], "reason": s.get("reason"),
                   "key": None if s["key_source"] is None else "ours" if s["key_source"] == "platform" else "yours"}
                  for s in steps],
        "steps_not_pursued_30d": sum(1 for s in store.list("steps_not_pursued", user_key=user)
                                     if s["at"] >= max(time.time() - 30 * 86400, trace_from)),
    }


def record_blocked(agent: str, kind: str, attempted: str, reason: str, user: str | None = None) -> None:
    """The gateway refused a call: a model or tool not in the registry, a limit or a budget. It
    belongs to the person whose request this is, unless another is given."""
    store.put("gateway_blocked", {"id": new_id("blk"), "at": time.time(), "day": _today(), "agent": agent,
                                  "kind": kind, "attempted": attempted, "reason": reason,
                                  "user_key": user or current_user()})


SERVICE = "service"  # an event about the whole service (the daily ceiling), not one demo copy
LIMIT_EVENT_IDS = ("gev_allowance_", "gev_ceiling_")  # the daily-limit refusals' events, by their stable ids


def record_intervention(agent: str, response: str, reason: str, case_key: str | None = None, *,
                        user_key: str | None = None, once: str | None = None) -> dict | None:
    """The guardian intervened (add_check, reduce_autonomy, pause or block); a risk reviewer
    decides each one (PR-15.6).

    `reason` is fixed text from code: never conversation text, a name or anything a person typed,
    because judges and the fraud dreamer read it. `user_key` is the hashed key of the demo copy it
    concerns, the caller's when not given, or SERVICE for the whole service. An event with neither
    (no one signed in, or one recorded before PR-15.6) is shown only to the Anvaya team. With `once`,
    the event is written only the first time (a stable id), so a repeated refusal or a step dropped
    again doesn't add another; then it returns None."""
    event = {"id": once or new_id("gev"), "at": time.time(), "agent": agent, "response": response,
             "reason": reason, "case_key": case_key, "user_key": user_key or current_user(), "status": "open"}
    if once is None:
        return store.put("guardian_events", event)
    return event if store.create("guardian_events", event) else None


def allowance_reached(agent: str, channel: str = "app") -> None:
    """A call refused because the person's daily allowance is used: one blocked call, and the
    guardian's decision (PR-2.7): one event per person, measure, day and limit (an approved raise
    that runs out too is new)."""
    from . import guardian  # guardian uses this module

    user, metric = current_user(), METRIC.get(agent, "tokens")
    where = f"{_pool(agent)}.{metric}"
    cap = (allowance_status(user).get(_pool(agent), {}).get(metric) or {}).get("limit", 0)
    record_blocked(agent, "allowance", where, "Daily allowance reached", user=user)
    guardian.decide("model_call", agent, {"limit": "allowance", "where": where}, channel=channel,
                    once=f"gev_allowance_{_today()}_{user}_{where}_{int(cap)}")


def ceiling_reached(agent: str, channel: str = "app") -> None:
    """A call refused by the service's daily ceiling: one blocked call, and the guardian's decision:
    one event for the whole service per measure, line (optional agents, or everything but safety)
    and day."""
    from . import guardian

    where = f"service.{_service_metric(agent)}"
    line = "optional agents" if (agent_entry(agent) or {}).get("optional") else "all but safety-critical agents"
    record_blocked(agent, "ceiling", where, "Daily service ceiling reached", user=current_user())
    guardian.decide("model_call", agent, {"limit": "ceiling", "where": where, "line": line}, channel=channel,
                    event_key=SERVICE, once=f"gev_ceiling_{_today()}_{where}_{line.split()[0]}")


def kill_switch() -> str:
    """Current kill-switch state; the gateway reads it before every call."""
    return platform_settings().get("kill_switch", "off")
