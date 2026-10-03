"""Platform admin portal API (PR-12), on top of the governance core.

Every route is under /api/platform and checks a platform role on the server, from the
signed-in identity, never from the screen. Nothing here returns conversation text or
anyone's name: users appear as hashed keys, and counts under 5 are hidden.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..store import store
from . import billing, core
from .core import Actor, actor, audit, need, platform_settings, registry, small
from .seed import ensure_seeded, seed

# ---- Read models ----------------------------------------------------------------


def _days(n: int = 30) -> list[dict]:
    return sorted(store.list("usage_days"), key=lambda d: d["day"])[-n:]


def _use(rows: list[dict]) -> dict:
    return {"calls": sum(r.get("calls", 0) for r in rows),
            "tokens": sum(r.get("tokens", r.get("tokens_in", 0) + r.get("tokens_out", 0)) for r in rows),
            "minutes": round(sum(r.get("minutes", 0.0) for r in rows), 1)}


def _by(days: list[dict], key: str) -> dict[str, dict]:
    names = {k for d in days for k in d.get(key, {})}
    out = {k: _use([d[key][k] for d in days if k in d.get(key, {})]) for k in names}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["tokens"], -kv[1]["minutes"], kv[0])))


def overview(*, fresh: bool = False) -> dict:
    """Actual spend from Google Cloud billing; usage (not dollars) per agent, model and tenant."""
    days = _days()
    # Tenants with real use. Every call is "direct" until bank tenants arrive, so today's
    # active people (one allowance record each) all belong to it.
    active_today = len(store.list("user_usage", day=core._today()))
    tenants = {t: {**v, "active_users_today": small(active_today) if t == "direct" else None}
               for t, v in _by(days, "tenants").items()}
    s = platform_settings()
    return {
        "spend": billing.spend(fresh=fresh),
        "usage": {
            "daily": [{"day": d["day"], **_use(list(d["agents"].values()))} for d in days],
            "by_agent": _by(days, "agents"),
            "by_model": _by(days, "model_use"),
            "by_tenant": tenants,
        },
        "kill_switch": s.get("kill_switch", "off"),
        "quota": s.get("quota"),
        "pending_approvals": len(store.list("budget_requests", status="pending")),
        "open_interventions": len(store.list("guardian_events", status="open")),
        "blocked_30d": len([b for b in store.list("gateway_blocked") if b["at"] > time.time() - 30 * 86400]),
    }


def agents_view() -> list[dict]:
    days = _days()
    blocked = store.list("gateway_blocked")
    out = []
    for a in registry():
        rows = [d["agents"][a["id"]] for d in days if a["id"] in d["agents"]]
        calls = sum(r["calls"] for r in rows)
        out.append({**a, "usage_30d": {
            "calls": calls,
            "tokens_in": sum(r["tokens_in"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
            "minutes": round(sum(r["minutes"] for r in rows), 1),
            "error_rate": round(sum(r["errors"] for r in rows) / calls, 4) if calls else 0.0,
            "blocked": sum(1 for b in blocked if b["agent"] == a["id"]),
        }})
    return out


# ---- Routes ---------------------------------------------------------------------

router = APIRouter(prefix="/platform")


@router.get("/me")
def me(who: Actor = Depends(actor)) -> Actor:
    return who


@router.get("/overview")
def get_overview(fresh: bool = False, _: Actor = Depends(actor)) -> dict:
    """fresh=true (the portal's Refresh button) reads billing again, at most once a minute."""
    ensure_seeded()
    return overview(fresh=fresh)


@router.get("/agents")
def get_agents(_: Actor = Depends(actor)) -> dict:
    ensure_seeded()
    return {"agents": agents_view(),
            "blocked": sorted(store.list("gateway_blocked"), key=lambda b: -b["at"])}


@router.get("/approvals")
def get_approvals(_: Actor = Depends(actor)) -> dict:
    ensure_seeded()
    reqs = sorted(store.list("budget_requests"), key=lambda r: -r["at"])
    now = time.time()
    return {"pending": [r for r in reqs if r["status"] == "pending"],
            "active": [r for r in reqs if r["status"] == "approved" and r.get("expires_at", 0) > now],
            "history": [r for r in reqs if r["status"] != "pending"]}


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=500)
    hours: int | None = Field(default=None, ge=1, le=168)


@router.post("/approvals/{request_id}/decision")
def decide_approval(request_id: str, body: Decision, who: Actor = Depends(need("approve_budget"))) -> dict:
    req = store.get("budget_requests", request_id)
    if not req:
        raise HTTPException(404, "request not found")
    if req["status"] != "pending":
        raise HTTPException(409, "This request has already been decided.")
    if req["user_key"] == who.key:
        raise HTTPException(403, "You can't approve your own request.")
    now = time.time()
    fields = {"status": "approved" if body.decision == "approve" else "rejected", "decided_at": now,
              "decided_by": who.email or "local", "decision_reason": body.reason}
    if body.decision == "approve":
        fields["expires_at"] = now + (body.hours or req["hours"]) * 3600
    audit(who, f"budget.{body.decision}", request_id, body.reason, amount=req["amount"], kind=req["kind"])
    return store.update("budget_requests", request_id, fields)


@router.get("/guardian")
def get_guardian(_: Actor = Depends(actor)) -> dict:
    ensure_seeded()
    events = sorted(store.list("guardian_events"), key=lambda e: -e["at"])
    decided = [e for e in events if e["status"] != "open"]
    reversed_ = [e for e in decided if e["status"] == "reversed"]
    by_response: dict[str, int] = {}
    for e in events:
        by_response[e["response"]] = by_response.get(e["response"], 0) + 1
    return {"events": events, "by_response": by_response,
            "false_alarm_rate": round(len(reversed_) / len(decided), 3) if decided else None}


class GuardianDecision(BaseModel):
    decision: Literal["confirm", "reverse"]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/guardian/{event_id}/decision")
def decide_guardian(event_id: str, body: GuardianDecision, who: Actor = Depends(need("review_guardian"))) -> dict:
    event = store.get("guardian_events", event_id)
    if not event:
        raise HTTPException(404, "intervention not found")
    if event["status"] != "open":
        raise HTTPException(409, "This intervention has already been reviewed.")
    audit(who, f"guardian.{body.decision}", event_id, body.reason, response=event["response"])
    return store.update("guardian_events", event_id, {
        "status": "confirmed" if body.decision == "confirm" else "reversed", "decided_at": time.time(),
        "decided_by": who.email or "local", "decision_reason": body.reason})


@router.get("/fraud")
def get_fraud(_: Actor = Depends(actor)) -> dict:
    ensure_seeded()
    scenarios = sorted(store.list("fraud_scenarios"), key=lambda s: -s["at"])
    return {"scenarios": scenarios,
            "coverage": {"covered": sum(s["coverage"] == "covered" for s in scenarios),
                         "gaps": sum(s["coverage"] == "gap" for s in scenarios)},
            "guides": sorted(store.list("fraud_guides"), key=lambda g: g["title"])}


class ScenarioDecision(BaseModel):
    decision: Literal["approve", "discard"]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/fraud/scenarios/{scenario_id}/decision")
def decide_scenario(scenario_id: str, body: ScenarioDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    scenario = store.get("fraud_scenarios", scenario_id)
    if not scenario:
        raise HTTPException(404, "scenario not found")
    if scenario["review"] != "pending":
        raise HTTPException(409, "This scenario has already been reviewed.")
    audit(who, f"fraud.scenario.{body.decision}", scenario_id, body.reason)
    return store.update("fraud_scenarios", scenario_id, {
        "review": "approved" if body.decision == "approve" else "discarded", "reviewed_by": who.email or "local"})


@router.post("/fraud/guides/{guide_id}/research")
def research_now(guide_id: str, who: Actor = Depends(need("review_fraud"))) -> dict:
    guide = store.get("fraud_guides", guide_id)
    if not guide:
        raise HTTPException(404, "guide not found")
    audit(who, "fraud.research_requested", guide_id)
    store.update("fraud_guides", guide_id, {"status": "research_requested", "requested_at": time.time()})
    return {"status": "research_requested",
            "message": "Research requested. The research agent (PR-2.5) picks this up when it runs."}


@router.get("/demos")
def get_demos(_: Actor = Depends(actor)) -> dict:
    ensure_seeded()
    return {"copies": sorted(store.list("demo_copies"), key=lambda d: -d["last_used_at"]),
            "cleanup_after_days": 30}


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/demos/{copy_id}/reset")
def reset_demo(copy_id: str, body: Reason, who: Actor = Depends(need("manage_demos"))) -> dict:
    copy = store.get("demo_copies", copy_id)
    if not copy:
        raise HTTPException(404, "demo copy not found")
    audit(who, "demo.reset", copy_id, body.reason)
    return store.update("demo_copies", copy_id, {"allowance_used": 0.0, "reset_at": time.time()})


class KillSwitch(BaseModel):
    state: Literal["off", "optional_paused", "all_paused"]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/kill-switch")
def set_kill_switch(body: KillSwitch, who: Actor = Depends(need("kill_switch"))) -> dict:
    ensure_seeded()
    audit(who, "kill_switch.set", body.state, body.reason)
    return store.update("platform_settings", "settings", {"kill_switch": body.state,
                                                          "kill_switch_reason": body.reason})


@router.post("/reseed")
def reseed(who: Actor = Depends(need("reseed"))) -> dict:
    seed()
    audit(who, "demo_data.reseed", "platform")
    return {"seeded": True}


@router.get("/audit")
def get_audit(_: Actor = Depends(actor)) -> dict:
    entries = sorted(store.list("platform_audit"), key=lambda e: -e["at"])
    return {"entries": entries[:200]}

