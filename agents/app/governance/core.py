"""Governance layer: the shared core.

One layer governs every agent (docs/proposals/agent-governance.md, architecture §4.1):

- **Registry** (registry.yaml): each agent's identity, role, autonomy, allowed models and
  tools, limits and budget pool.
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
from datetime import datetime, timezone
from functools import lru_cache

import yaml
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..store import new_id, store

ROLES = ("viewer", "budget_approver", "risk_reviewer", "platform_admin")
PERMISSIONS: dict[str, set[str]] = {
    "viewer": set(),
    "budget_approver": {"approve_budget"},
    "risk_reviewer": {"review_guardian", "review_fraud"},
    "platform_admin": {"approve_budget", "review_guardian", "review_fraud", "manage_demos", "kill_switch",
                       "reseed"},
}
KILL_SWITCH_STATES = ("off", "optional_paused", "all_paused")
SMALL_COUNT = 5
REGISTRY_PATH = pathlib.Path(__file__).resolve().parents[2] / "registry.yaml"


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
    return settings.platform_default_role


def actor(request: Request) -> Actor:
    user = getattr(request.state, "user", None)
    email = user.email if user else None
    role = role_for(email)
    if role not in ROLES:
        raise HTTPException(403, "You don't have access to the platform admin portal.")
    return Actor(email=email, key=user_key(user.uid if user else "local"), role=role,
                 permissions=sorted(PERMISSIONS[role]))


def need(permission: str):
    def check(who: Actor = Depends(actor)) -> Actor:
        if permission not in who.permissions:
            raise HTTPException(403, f"Your role ({who.role.replace('_', ' ')}) can't do this.")
        return who
    return check


def audit(who: Actor, action: str, target: str, reason: str | None = None, **detail) -> None:
    store.put("platform_audit", {"id": new_id("aud"), "at": time.time(), "actor": who.email or "local",
                                 "role": who.role, "action": action, "target": target,
                                 "reason": reason, "detail": detail})


def small(n: int) -> int | None:
    """Hide counts under 5 so nobody can be singled out; the UI shows 'fewer than 5'."""
    return n if n >= SMALL_COUNT else None


# ---- Registry and settings ----------------------------------------------------


@lru_cache(maxsize=1)
def _registry_file() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text())


def registry() -> list[dict]:
    return _registry_file()["agents"]


def allowances() -> dict[str, dict]:
    """Daily allowance per person for each budget pool (registry.yaml, PR-10.4)."""
    return _registry_file().get("allowances", {})


def platform_settings() -> dict:
    return store.get("platform_settings", "settings") or {}


# ---- Prices ------------------------------------------------------------------

# Model prices in US dollars, as Google publishes them (Gemini API pricing): per million
# tokens in and out, or per minute of live audio. A dated, versioned price file replaces
# this table in #44 (F-10.1).
PRICE = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.25, 1.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.8-flash-lite-tts": (0.50, 6.00),
}
LIVE_USD_PER_MINUTE = 0.03


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICE.get(model, PRICE["gemini-3.1-flash-lite"])
    return tokens_in / 1e6 * p_in + tokens_out / 1e6 * p_out


# ---- Gateway checks ------------------------------------------------------------


class Refused(Exception):
    """The gateway refused a call: not in the registry, paused by the kill switch, over a
    limit, or over the person's daily allowance."""


def agent_entry(agent: str) -> dict | None:
    return next((a for a in registry() if a["id"] == agent), None)


def check_call(agent: str, model: str) -> None:
    """Refuse a model call the registry doesn't allow, or that the kill switch pauses."""
    entry = agent_entry(agent)
    if entry is None:
        record_blocked(agent, "agent", agent, "Agent not in the registry")
        raise Refused(f"{agent} is not a registered agent.")
    allowed = [entry["models"]["primary"], *entry["models"].get("fallback", [])]
    if model not in allowed:
        record_blocked(agent, "model", model, "Model not allowed for this agent")
        raise Refused(f"{agent} may not use {model}.")
    state = kill_switch()
    paused = (state == "all_paused" and not entry.get("safety_critical")) or \
             (state == "optional_paused" and entry.get("optional"))
    if paused:
        record_blocked(agent, "kill_switch", state, "Paused by the kill switch")
        raise Refused("This feature is paused right now. Please try again later.")
    check_allowance(agent)


def limit(agent: str, key: str, default: int | None = None) -> int | None:
    """One of the agent's limits from the registry (PR-10.2)."""
    entry = agent_entry(agent) or {}
    return entry.get("limits", {}).get(key, default)


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
# Each person has a daily allowance per budget pool (registry.yaml "allowances"), so one
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


def set_current_user(key: str | None) -> None:
    """Called once at sign-in for each request or live call; the gateway reads it anywhere."""
    _current_user.set(key)


def current_user() -> str | None:
    return _current_user.get()


def _pool(agent: str) -> str:
    return (agent_entry(agent) or {}).get("budget_class", "owner")


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
    """Refuse a call when the person has used today's allowance for the agent's pool."""
    left = remaining(agent)
    if left is not None and left <= 0:
        metric = METRIC.get(agent, "tokens")
        record_blocked(agent, "allowance", f"{_pool(agent)}.{metric}", "Daily allowance reached",
                       user=current_user())
        raise Refused("You've used today's allowance for this. It resets tomorrow, or you can ask for more.")


def _add(agent: str, amount: float) -> None:
    """Add to (or, with a negative amount, take from) the person's counter for today, atomically."""
    user, pool = current_user(), _pool(agent)
    if not user or not allowances().get(pool):
        return
    store.increment("user_usage", f"{_today()}:{user}", {pool: {METRIC.get(agent, "tokens"): amount}},
                    {"day": _today(), "user_key": user})


def count_for_user(agent: str, *, tokens: int = 0, minutes: float = 0.0) -> None:
    """Add one call to the person's counters for today (not for the safety pool)."""
    metric = METRIC.get(agent, "tokens")
    _add(agent, {"tokens": tokens, "voice_minutes": round(minutes, 2), "spoken_replies": 1}[metric])


def reserve(agent: str, amount: float) -> None:
    """Hold part of the allowance before a long call (live voice), so a second call at the same
    time sees only what's really left. Release it when the call ends; the actual use is counted."""
    _add(agent, amount)


def release(agent: str, amount: float) -> None:
    _add(agent, -amount)


# ---- Recording (used by the gateway and the guardian) -------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_usage(agent: str, model: str, tenant: str, *, tokens_in: int = 0, tokens_out: int = 0,
                 minutes: float = 0.0, cost_usd: float = 0.0, error: bool = False) -> None:
    """Add one model call to today's ledger (the gateway calls this after every call).

    An atomic increment, so calls that finish at the same time (on several server instances)
    never overwrite each other's counts in the portal's spend figures.
    """
    day_id = _today()
    store.increment("usage_days", day_id, {
        "agents": {agent: {"calls": 1, "tokens_in": tokens_in, "tokens_out": tokens_out, "minutes": minutes,
                           "cost_usd": cost_usd, "errors": int(error)}},
        "models": {model: cost_usd},
        "tenants": {tenant: {"cost_usd": cost_usd}},
    }, {"day": day_id})
    if not error:
        count_for_user(agent, tokens=tokens_in + tokens_out, minutes=minutes)


def record_blocked(agent: str, kind: str, attempted: str, reason: str, user: str | None = None) -> None:
    """The gateway refused a call: a model or tool not in the registry, a limit or a budget."""
    store.put("gateway_blocked", {"id": new_id("blk"), "at": time.time(), "agent": agent, "kind": kind,
                                  "attempted": attempted, "reason": reason, "user_key": user})


def record_intervention(agent: str, response: str, reason: str, case_key: str | None = None) -> dict:
    """The guardian intervened (add_check, reduce_autonomy, pause or block); people review it."""
    return store.put("guardian_events", {"id": new_id("gev"), "at": time.time(), "agent": agent,
                                         "response": response, "reason": reason, "case_key": case_key,
                                         "status": "open"})


def kill_switch() -> str:
    """Current kill-switch state; the gateway reads it before every call."""
    return platform_settings().get("kill_switch", "off")
