"""Platform admin portal API (PR-12), on top of the governance core.

Every route is under /api/platform and checks a platform role on the server, from the
signed-in identity, never from the screen. Nothing here returns conversation text or
anyone's name: users appear as hashed keys, and counts under 5 are hidden.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import demo, fraud_cues, fraud_guides, fraud_pipeline, model_access, scheduler
from ..config import settings
from ..store import store
from . import billing, core, guardian, keys
from .core import Actor, actor, audit, need, platform_settings, registry, small
from .seed import ensure_current

log = logging.getLogger(__name__)

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


def overview(*, fresh: bool = False, who: Actor | None = None) -> dict:
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
        "service_ceiling": core.service_status(),  # today's use under the daily ceiling (PR-15.1)
        "quota": s.get("quota"),
        "pending_approvals": len([r for r in store.list("budget_requests", status="pending")
                                  if who is None or who.role != "demo_admin" or r.get("copy_id") == who.key]),
        "open_interventions": len([e for e in store.list("guardian_events", status="open") if _sees_event(who, e)]),
        "blocked_30d": len([b for b in store.list("gateway_blocked") if b["at"] > time.time() - 30 * 86400]),
        # Next steps Saathi never pursued (PR-13.3): counts only, no words or names. A demo admin
        # sees their own copy's.
        "steps_not_pursued_30d": len([s for s in store.list("steps_not_pursued") if s["at"] > time.time() - 30 * 86400
                                      and (who is None or who.role != "demo_admin" or s.get("user_key") == who.key)]),
    }


def agents_view() -> list[dict]:
    days = _days()
    blocked = store.list("gateway_blocked")
    out = []
    for a in registry():
        rules = core.policy(a["id"]) or {}
        consumption = rules.get("consumption", {})
        rows = [d["agents"][a["id"]] for d in days if a["id"] in d["agents"]]
        calls = sum(r["calls"] for r in rows)
        # What it is (registry.yaml) beside what it may do (policies.yaml).
        out.append({**a, "autonomy": rules.get("role", {}).get("autonomy"),
                    "allowed_models": rules.get("models", {}).get("allow", []),
                    "tools": [g["tool"] for g in rules.get("tools", [])],
                    "limits": {**consumption.get("call", {}), **consumption.get("run", {})},
                    "budget_class": consumption.get("per_user", {}).get("pool"), "usage_30d": {
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
def get_overview(fresh: bool = False, who: Actor = Depends(actor)) -> dict:
    """fresh=true (the portal's Refresh button) reads billing again, at most once a minute."""
    ensure_current()
    return overview(fresh=fresh, who=who)


@router.get("/agents")
def get_agents(who: Actor = Depends(actor)) -> dict:
    ensure_current()
    blocked = store.list("gateway_blocked")
    if who.role == "demo_admin":  # only calls refused for their own copy
        blocked = [b for b in blocked if b.get("user_key") == who.key]
    return {"agents": agents_view(), "blocked": sorted(blocked, key=lambda b: -b["at"])}


def _sees_event(who: Actor | None, event: dict) -> bool:
    """A demo admin sees the guardian's events for their own copy, and those for the whole service
    (e.g. the daily ceiling); the Anvaya team sees all (PR-15.6). An event with no key at all (from
    before PR-15.6, or recorded with no one signed in) is the team's only."""
    return who is None or who.role != "demo_admin" or event.get("user_key") in (who.key, core.SERVICE)


def _hide_deciders(rows: list[dict], who: Actor) -> list[dict]:
    """For a demo admin: a decision someone else made shows "Anvaya team", never their email or
    written reason (with open sign-in, anyone can be a demo admin, PR-15.2). Their own stay."""
    def hide(r: dict) -> dict:
        if not r.get("decided_by") or r["decided_by"] == who.email:
            return r
        return {k: v for k, v in r.items() if k != "decision_reason"} | {"decided_by": "Anvaya team"}
    return [hide(r) for r in rows]


@router.get("/approvals")
def get_approvals(who: Actor = Depends(actor)) -> dict:
    ensure_current()
    reqs = sorted(store.list("budget_requests"), key=lambda r: -r["at"])
    if who.role == "demo_admin":  # only their own demo copy's requests
        reqs = _hide_deciders([r for r in reqs if r.get("copy_id") == who.key], who)
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
    if not req or (who.role == "demo_admin" and req.get("copy_id") != who.key):
        raise HTTPException(404, "request not found")
    if req["status"] != "pending":
        raise HTTPException(409, "This request has already been decided.")
    # Nobody approves their own request. A demo admin deciding a request from their own copy acts
    # as that copy's platform admin, a different person in the demo from the one who asked
    # (PR-12.9); for real roles the rule always holds.
    if req["user_key"] == who.key and who.role != "demo_admin":
        raise HTTPException(403, "You can't approve your own request.")
    now = time.time()
    fields = {"status": "approved" if body.decision == "approve" else "rejected", "decided_at": now,
              "decided_by": who.email or "local", "decision_reason": body.reason}
    if body.decision == "approve":
        # An approver can shorten a grant, never lengthen it past what was asked (at most 72 hours).
        fields["expires_at"] = now + min(body.hours or req["hours"], req["hours"]) * 3600
    audit(who, f"budget.{body.decision}", request_id, body.reason, amount=req["amount"], kind=req["kind"])
    return store.update("budget_requests", request_id, fields)


@router.get("/guardian")
def get_guardian(who: Actor = Depends(actor)) -> dict:
    ensure_current()
    events = sorted((e for e in store.list("guardian_events") if _sees_event(who, e)), key=lambda e: -e["at"])
    if who.role == "demo_admin":
        events = _hide_deciders(events, who)
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
    # One decision per event, even if two reviewers press at the same moment. A held action finishes
    # (reverse) or is cancelled (confirm) first; if that fails, the claim is let go so it can be retried.
    if not store.create("guardian_decisions", {"id": event_id, "at": time.time()}):
        raise HTTPException(409, "This intervention has already been reviewed.")
    try:
        guardian.resolve(event_id, body.decision)
    except Exception:
        store.delete("guardian_decisions", event_id)
        raise
    audit(who, f"guardian.{body.decision}", event_id, body.reason, response=event["response"])
    return store.update("guardian_events", event_id, {
        "status": "confirmed" if body.decision == "confirm" else "reversed", "decided_at": time.time(),
        "decided_by": who.email or "local", "decision_reason": body.reason})


@router.get("/fraud")
def get_fraud(_: Actor = Depends(actor)) -> dict:
    ensure_current()
    fraud_pipeline.expire_stale()  # a run left 'running' by a restart doesn't block the page for good
    scenarios = sorted((s for s in store.list("fraud_scenarios") if s.get("review") != "faded"),
                       key=lambda s: -s["at"])
    return {"scenarios": scenarios,
            "coverage": {"covered": sum(s["coverage"] == "covered" for s in scenarios),
                         "partly": sum(s["coverage"] == "partly" for s in scenarios),
                         "gaps": sum(s["coverage"] == "gap" for s in scenarios),
                         "faded": len(store.list("fraud_scenarios", review="faded"))},
            "drafts": sorted(store.list("fraud_drafts", review="pending"), key=lambda d: -d["at"]),
            "privacy": _cue_privacy(),
            "runs": sorted(store.list("fraud_runs"), key=lambda r: -r["started_at"])[:10],
            "guides": fraud_guides.summaries(),
            # What the pipeline added on its own (PR-2.7.y): a person can remove any of it, and 1 in 10
            # is marked for an optional spot check. Adding pauses for a guide after 2 removals in a week.
            # Every active one, spot checks first, so nothing added is ever out of a person's reach.
            "added": sorted(store.list("fraud_auto", status="active"), key=lambda a: (not a["spot_check"], -a["at"])),
            "adding_paused": sorted(a["id"] for a in store.list("fraud_autonomy") if a.get("paused"))}


class AddedDecision(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/fraud/added/{item_id}/remove")
def remove_added(item_id: str, body: AddedDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    """Retire something the pipeline added on its own. Two removals in a week pause adding for that guide."""
    try:
        item = fraud_pipeline.remove_added(item_id)
    except KeyError as e:
        raise HTTPException(404, "Nothing active with that id was added.") from e
    audit(who, "fraud.added_removed", item_id, body.reason, kind=item["kind"])
    return {"item": item, "adding_paused": fraud_pipeline.adding_paused(item["guide_key"])}


@router.post("/fraud/added/{item_id}/keep")
def keep_added(item_id: str, body: AddedDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    """A spot check found it right: it stays, and is no longer marked for checking."""
    item = store.get("fraud_auto", item_id)
    if not item or item["status"] != "active":
        raise HTTPException(404, "Nothing active with that id was added.")
    audit(who, "fraud.added_kept", item_id, body.reason, kind=item["kind"])
    return store.update("fraud_auto", item_id, {"spot_check": False, "checked_at": time.time()})


@router.post("/fraud/guides/{guide_id}/resume-adding")
def resume_adding(guide_id: str, body: AddedDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    """Let the pipeline add on its own again for this guide, after it paused."""
    if not fraud_guides.ensure_saved(guide_id):
        raise HTTPException(404, "guide not found")
    fraud_pipeline.resume_adding(guide_id)
    audit(who, "fraud.adding_resumed", guide_id, body.reason)
    return {"guide": guide_id, "adding_paused": False}


def _claim(item_id: str) -> None:
    """Only one decision per scenario or draft, even if two reviewers press at the same moment."""
    if not store.create("fraud_decisions", {"id": item_id, "at": time.time()}):
        raise HTTPException(409, "This has already been reviewed.")


class ScenarioDecision(BaseModel):
    decision: Literal["approve", "discard"]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/fraud/scenarios/{scenario_id}/decision")
def decide_scenario(scenario_id: str, body: ScenarioDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    """Approve a confirmed scenario (it becomes validated and joins the guide), or discard any
    open one (it becomes invalidated, and the dreamer is told it's ruled out)."""
    scenario = store.get("fraud_scenarios", scenario_id)
    if not scenario:
        raise HTTPException(404, "scenario not found")
    if scenario["review"] not in ("pending", "unconfirmed"):
        raise HTTPException(409, "This scenario has already been reviewed.")
    if body.decision == "approve" and scenario["review"] != "pending":
        raise HTTPException(409, "Only a scenario a cited source confirms can be approved.")
    _claim(scenario_id)
    audit(who, f"fraud.scenario.{body.decision}", scenario_id, body.reason)
    if body.decision == "approve":
        fraud_pipeline.approve_scenario(scenario)
    return store.update("fraud_scenarios", scenario_id, {
        "review": "approved" if body.decision == "approve" else "discarded",
        "label": "validated" if body.decision == "approve" else "invalidated",
        "reviewed_by": who.key, "reviewed_at": time.time()})


@router.post("/fraud/drafts/{draft_id}/decision")
def decide_draft(draft_id: str, body: ScenarioDecision, who: Actor = Depends(need("review_fraud"))) -> dict:
    """Approve a researched tactic (a new guide version), or discard it."""
    draft = store.get("fraud_drafts", draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    if draft["review"] != "pending":
        raise HTTPException(409, "This draft has already been reviewed.")
    _claim(draft_id)
    audit(who, f"fraud.draft.{body.decision}", draft_id, body.reason)
    if body.decision == "approve":
        fraud_pipeline.approve_draft(draft)
    return store.update("fraud_drafts", draft_id, {
        "review": "approved" if body.decision == "approve" else "discarded",
        "label": "derived" if body.decision == "approve" else "invalidated",
        "reviewed_by": who.key, "reviewed_at": time.time()})


def _start(kind: Literal["dream", "research"], guide_id: str, who: Actor, background: BackgroundTasks) -> dict:
    if not fraud_guides.ensure_saved(guide_id):  # a built-in guide's first run saves it as version 1
        raise HTTPException(404, "guide not found")
    # On Google Cloud the run is queued and the hourly job is asked to run now: a run started in the
    # background after this reply would get almost no CPU from Cloud Run and stall (scheduler.py).
    on_cloud = bool(settings.fraud_scheduler_job)
    try:
        run = fraud_pipeline.start_run(kind, guide_id, who.key, queued=on_cloud)
    except fraud_pipeline.Busy as e:
        raise HTTPException(409, str(e)) from e
    audit(who, f"fraud.{kind}_requested", guide_id)
    if kind == "research":
        store.update("fraud_guides", guide_id, {"status": "research_requested", "requested_at": time.time()})
    if on_cloud:
        try:
            scheduler.run_now()
        except Exception as e:
            log.exception("couldn't ask the scheduler to run now")
            fraud_pipeline.cancel_queued(run, f"Couldn't start the run: {type(e).__name__}")
            raise HTTPException(502, "The run couldn't be started. Try again, or wait for the hourly run.") from e
    else:  # local development: this machine keeps its CPU
        background.add_task(fraud_pipeline.execute, run)
    return run


@router.post("/fraud/guides/{guide_id}/research")
def research_now(guide_id: str, background: BackgroundTasks, who: Actor = Depends(need("review_fraud"))) -> dict:
    run = _start("research", guide_id, who, background)
    return {"status": "research_requested", "run": run,
            "message": "Research started. New tactics a source confirms will wait here for review."}


@router.post("/fraud/runs/{run_id}/stop")
def stop_run(run_id: str, who: Actor = Depends(need("review_fraud"))) -> dict:
    """Stop a running fraud run to control cost. It stops before its next model call and keeps
    what it has checked so far."""
    try:
        run = fraud_pipeline.request_stop(run_id)
    except KeyError as e:
        raise HTTPException(409, "This run isn't running.") from e
    audit(who, "fraud.run_stopped", run_id)
    return {"run": run, "message": "Stopping before the next step. What was checked so far is kept."}


@router.post("/fraud/guides/{guide_id}/dream")
def dream_now(guide_id: str, background: BackgroundTasks, who: Actor = Depends(need("review_fraud"))) -> dict:
    run = _start("dream", guide_id, who, background)
    return {"status": "dreaming", "run": run,
            "message": "Dreaming started. New scenarios appear on the coverage map as they're checked."}


def _allowance_used(key: str) -> float:
    """Share of today's owner token allowance this copy has used, counting approved extra
    allowance, exactly as the gateway does (PR-10.4)."""
    tokens = core.allowance_status(key).get("owner", {}).get("tokens") or {}
    return round(min(1.0, tokens.get("share", 0.0)), 4)


@router.get("/demos")
def get_demos(who: Actor = Depends(actor)) -> dict:
    """Every private demo copy, without names. A visitor's optional nickname is shown only to
    platform admins; the viewer's own copy is marked."""
    ensure_current()
    copies = []
    copies_seen = store.list("demo_copies")
    if who.role == "demo_admin":  # only their own copy
        copies_seen = [d for d in copies_seen if d["id"] == who.key]
    for d in sorted(copies_seen, key=lambda d: -d.get("last_used_at", 0)):
        row = {k: d.get(k) for k in ("id", "user_key", "created_at", "last_used_at", "status", "reset_at")}
        row |= {"allowance_used": _allowance_used(d["id"]), "is_you": d["id"] == who.key}
        if who.role == "platform_admin":
            row["nickname"] = d.get("nickname")
        copies.append(row)
    return {"copies": copies, "cleanup_after_days": 30, "nicknames_visible": who.role == "platform_admin"}


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/demos/{copy_id}/reset")
def reset_demo(copy_id: str, body: Reason, who: Actor = Depends(need("manage_demos"))) -> dict:
    copy = store.get("demo_copies", copy_id)
    if not copy:
        raise HTTPException(404, "demo copy not found")
    audit(who, "demo.reset", copy_id, body.reason)
    demo.reset(copy_id)  # the copy's demo data back to the start; never another copy's
    return store.update("demo_copies", copy_id, {"reset_at": time.time()})


def _cue_privacy() -> dict:
    s = platform_settings()
    return {"min_owners": fraud_cues.min_owners(), "floor": fraud_cues.FLOOR, "ceiling": fraud_cues.CEILING,
            "changed_at": s.get(f"{fraud_cues.SETTING}_changed_at"), "reason": s.get(f"{fraud_cues.SETTING}_reason")}


class CueMinimum(BaseModel):
    min_owners: int
    reason: str = Field(min_length=3, max_length=500)


@router.post("/privacy/cue-min-owners")
def set_cue_min_owners(body: CueMinimum, who: Actor = Depends(need("manage_privacy"))) -> dict:
    """How many owners must share a gap before it becomes a cue for dreaming (fraud-foresight §5).
    A platform admin's setting, logged; it can be raised freely but never set below the floor."""
    if not fraud_cues.FLOOR <= body.min_owners <= fraud_cues.CEILING:
        raise HTTPException(422, f"Choose between {fraud_cues.FLOOR} and {fraud_cues.CEILING}; below "
                                 f"{fraud_cues.FLOOR}, a count could point at one owner.")
    audit(who, "privacy.cue_min_owners", str(body.min_owners), body.reason)
    store.update("platform_settings", "settings", {fraud_cues.SETTING: body.min_owners,
                                                   f"{fraud_cues.SETTING}_reason": body.reason,
                                                   f"{fraud_cues.SETTING}_changed_at": time.time()})
    return _cue_privacy()


class KillSwitch(BaseModel):
    state: Literal["off", "optional_paused", "all_paused"]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/kill-switch")
def set_kill_switch(body: KillSwitch, who: Actor = Depends(need("kill_switch"))) -> dict:
    ensure_current()
    audit(who, "kill_switch.set", body.state, body.reason)
    return store.update("platform_settings", "settings", {"kill_switch": body.state,
                                                          "kill_switch_reason": body.reason})



@router.get("/audit")
def get_audit(who: Actor = Depends(actor)) -> dict:
    entries = sorted(store.list("platform_audit"), key=lambda e: -e["at"])
    if who.role == "demo_admin":  # only their own decisions
        entries = [e for e in entries if e.get("actor_key") == who.key]
    return {"entries": entries[:200]}


# ---- Model keys (PR-10.10): each person's own key, with ours as failover -------------------
#
# A report of whose key answered, and each person's key status, with a platform admin's controls.
# Never a key: only its kind, last 4 characters and status. People appear as hashed keys (with the
# nickname they chose, for platform admins only); a demo admin sees only their own row.

KEY_REPORT_DAYS = 30


def _key_use(rows: list[dict]) -> dict[str, dict]:
    """Calls, tokens, minutes, errors and failovers per key source, over these rows."""
    out: dict[str, dict] = {}
    for row in rows:
        for source, use in row.items():
            if source in ("id", "day", "user_key") or not isinstance(use, dict):
                continue
            total = out.setdefault(source, {"calls": 0, "tokens": 0, "minutes": 0.0, "errors": 0, "failovers": 0})
            for k in total:
                total[k] += use.get(k, 0)
    for total in out.values():
        total["minutes"] = round(total["minutes"], 1)
    return out


@router.get("/model-keys")
def get_model_keys(who: Actor = Depends(actor)) -> dict:
    """The Model keys report: usage by whose key answered, and each person's key and settings."""
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - KEY_REPORT_DAYS * 86400))
    usage = [u for u in store.list("key_usage") if u.get("day", "") >= since]
    if who.role == "demo_admin":  # only their own
        usage = [u for u in usage if u.get("user_key") == who.key]
    people_keys = {u["user_key"] for u in usage}
    people_keys |= {d["id"] for d in store.list(keys.ACCESS)} | {d["id"] for d in store.list(keys.POLICY)}
    if who.role == "demo_admin":
        people_keys = {who.key}
    elif who.role != "platform_admin":
        people_keys = set()  # other roles see the totals only
    people = []
    for key in sorted(people_keys):
        copy = store.get("demo_copies", key) or {}
        row = {"user_key": key, "is_you": key == who.key, "policy": keys.policy(key), "access": keys.access(key),
               "usage_30d": _key_use([u for u in usage if u.get("user_key") == key])}
        if who.role == "platform_admin":
            row["nickname"] = copy.get("nickname")
        people.append(row)
    people.sort(key=lambda r: -sum(v["calls"] for v in r["usage_30d"].values()))
    # Totals by key source: everyone's for portal roles, a demo admin's own (usage is filtered above).
    return {"days": KEY_REPORT_DAYS, "totals": _key_use(usage), "people": people,
            "default": keys.default_policy(), "platform_key": keys.platform_status(), "ready": keys.ready(),
            "kinds": list(keys.KINDS), "can_manage": "manage_model_access" in who.permissions}


class KeyPolicy(BaseModel):
    enabled: bool
    kinds: list[Literal["studio", "vertex_express"]]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/model-keys/default")
def set_model_keys_default(body: KeyPolicy, who: Actor = Depends(need("manage_model_access"))) -> dict:
    """The policy for everyone without a setting of their own."""
    audit(who, "model_keys.default", "default", body.reason, enabled=body.enabled, kinds=body.kinds)
    return keys.set_default_policy(body.enabled, list(body.kinds))


@router.post("/model-keys/{user_key}/policy")
def set_model_keys_policy(user_key: str, body: KeyPolicy, who: Actor = Depends(need("manage_model_access"))) -> dict:
    """Turn a person's own key on or off, or limit its kinds. Turning it off stops it being used
    (ours answers instead); the key stays stored until it's removed."""
    audit(who, "model_keys.policy", user_key, body.reason, enabled=body.enabled, kinds=body.kinds)
    return keys.set_policy(user_key, body.enabled, list(body.kinds))


@router.post("/model-keys/{user_key}/test")
async def test_model_key(user_key: str, body: Reason, who: Actor = Depends(need("manage_model_access"))) -> dict:
    """Test a person's saved key again. The admin sees the result, never the key."""
    saved = keys.saved_key(user_key)
    if not saved:
        raise HTTPException(404, "This person hasn't added a key.")
    kind, key = saved
    token = core._current_user.set(user_key)  # the test's use is the key owner's, not the admin's
    try:
        with keys.held(key):
            found, usable = await model_access.test_key(kind, key)
    finally:
        core._current_user.reset(token)
    audit(who, "model_keys.test", user_key, body.reason, usable=usable)
    return {"access": keys.record_test(user_key, found, usable), "features": found, "usable": usable}


@router.post("/model-keys/{user_key}/remove")
def remove_model_key(user_key: str, body: Reason, who: Actor = Depends(need("manage_model_access"))) -> dict:
    if not keys.access(user_key):
        raise HTTPException(404, "This person hasn't added a key.")
    audit(who, "model_keys.remove", user_key, body.reason)
    keys.remove(user_key)
    return {"access": None}
