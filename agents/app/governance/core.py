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
import pathlib
import time
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
def registry() -> list[dict]:
    return yaml.safe_load(REGISTRY_PATH.read_text())["agents"]


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
    """The gateway refused a call (not in the registry, or paused by the kill switch)."""


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
    if state == "all_paused" and not entry.get("safety_critical"):
        record_blocked(agent, "kill_switch", state, "Paused by the kill switch")
        raise Refused("This feature is paused right now. Please try again later.")


# ---- Recording (used by the gateway and the guardian) -------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_usage(agent: str, model: str, tenant: str, *, tokens_in: int = 0, tokens_out: int = 0,
                 minutes: float = 0.0, cost_usd: float = 0.0, error: bool = False) -> None:
    """Add one model call to today's ledger (the gateway calls this after every call)."""
    day_id = _today()
    day = store.get("usage_days", day_id) or {"id": day_id, "day": day_id, "agents": {}, "tenants": {},
                                               "models": {}}
    row = day["agents"].setdefault(agent, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "minutes": 0.0,
                                           "cost_usd": 0.0, "errors": 0})
    row["calls"] += 1
    row["tokens_in"] += tokens_in
    row["tokens_out"] += tokens_out
    row["minutes"] = round(row["minutes"] + minutes, 1)
    row["cost_usd"] = round(row["cost_usd"] + cost_usd, 6)
    row["errors"] += int(error)
    day["models"][model] = round(day["models"].get(model, 0.0) + cost_usd, 6)
    t = day["tenants"].setdefault(tenant, {"cost_usd": 0.0, "active_users": 0})
    t["cost_usd"] = round(t["cost_usd"] + cost_usd, 6)
    store.put("usage_days", day)


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
