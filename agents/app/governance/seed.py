"""Seeded demo data for the governance layer: 30 days of clearly labelled usage,
blocked attempts, budget requests, guardian interventions, fraud scenarios and guides,
and judges' demo copies. Used until the gateway and guardian record real events.
"""

from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timezone

from ..store import new_id, store
from .core import LIVE_INR_PER_MINUTE, PRICE, platform_settings, registry, user_key

# Demo tenants and their number of users.
TENANTS = {"direct": 20, "demo-bank": 18, "judges": 8}
# Typical daily calls per active user, and tokens per call (in, out).
USE = {
    "saathi.chat": (6, 3500, 350), "saathi.voice": (0.5, 0, 0), "saathi.speech": (3, 120, 2600),
    "capture.extractor": (6, 2500, 400), "documents.reader": (0.4, 1800, 300),
    "readiness.planner": (0.8, 9000, 900), "legal.needs": (0.2, 6000, 500),
    "activation.checker": (0.08, 2200, 600), "handover.assistant": (0.15, 12000, 450),
    "settlement.planner": (0.03, 9000, 2200), "guardian.detectors": (2, 900, 120),
}


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def seed(now: float | None = None) -> None:
    """Create 30 days of realistic, clearly labelled demo data for the portal."""
    now = now or time.time()
    rng = random.Random(42)
    agents = {a["id"]: a for a in registry()}
    users = {t: [user_key(f"seed-{t}-{i}") for i in range(n)] for t, n in TENANTS.items()}

    for collection in ("usage_days", "gateway_blocked", "budget_requests", "guardian_events",
                       "fraud_scenarios", "fraud_guides", "demo_copies"):
        for doc in store.list(collection, seeded=True):
            store.delete(collection, doc["id"])

    for back in range(29, -1, -1):
        ts = now - back * 86400
        day = {"id": _day(ts), "day": _day(ts), "seeded": True, "agents": {}, "tenants": {}, "models": {}}
        for tenant, keys in users.items():
            active = max(0, int(len(keys) * rng.uniform(0.35, 0.7)))
            if tenant == "judges" and back > 10:
                active = 0  # judges arrive in the last ten days
            tenant_cost = 0.0
            for agent_id, (per_user, tin, tout) in USE.items():
                a = agents[agent_id]
                expected = active * per_user * rng.uniform(0.6, 1.4)
                calls = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
                if not calls:
                    continue
                model = a["models"]["primary"]
                row = day["agents"].setdefault(agent_id, {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                                                          "minutes": 0.0, "cost_inr": 0.0, "errors": 0})
                if agent_id == "saathi.voice":
                    minutes = calls * rng.uniform(4, 11)
                    cost = minutes * LIVE_INR_PER_MINUTE
                    row["minutes"] = round(row["minutes"] + minutes, 1)
                else:
                    t_in, t_out = calls * tin, calls * tout
                    p_in, p_out = PRICE.get(model, PRICE["gemini-3.1-flash-lite"])
                    cost = t_in / 1e6 * p_in + t_out / 1e6 * p_out
                    row["tokens_in"] += t_in
                    row["tokens_out"] += t_out
                row["calls"] += calls
                row["errors"] += sum(1 for _ in range(calls) if rng.random() < 0.012)
                row["cost_inr"] = round(row["cost_inr"] + cost, 2)
                day["models"][model] = round(day["models"].get(model, 0.0) + cost, 2)
                tenant_cost += cost
            day["tenants"][tenant] = {"cost_inr": round(tenant_cost, 2), "active_users": active}
        if back % 7 == 3:  # the weekly fraud research and dreaming runs
            for agent_id in ("fraud.researcher", "fraud.dreamer"):
                cost = rng.uniform(18, 40)
                day["agents"][agent_id] = {"calls": rng.randint(8, 16), "tokens_in": 0, "tokens_out": 0,
                                           "minutes": 0.0, "cost_inr": round(cost, 2), "errors": 0}
                day["models"]["gemini-3.8-flash"] = round(day["models"].get("gemini-3.8-flash", 0) + cost, 2)
        store.put("usage_days", day)

    blocked = [
        ("handover.assistant", "tool", "read_vault", "Tool not in the registry for this agent"),
        ("saathi.chat", "model", "gemini-3.8-pro", "Model not allowed; fallback must be the same price class or cheaper"),
        ("capture.extractor", "limit", "max_output_tokens", "Output limit of 1,500 tokens reached"),
        ("handover.assistant", "limit", "max_model_calls_per_run", "Stopped after 6 model calls in one run"),
        ("saathi.voice", "limit", "max_call_minutes", "Live call ended at 20 minutes"),
        ("saathi.speech", "budget", "daily_allowance", "Person reached their daily allowance for spoken replies"),
    ]
    for i, (agent_id, kind, what, why) in enumerate(blocked):
        store.put("gateway_blocked", {"id": new_id("blk"), "seeded": True, "at": now - (i * 2 + 1) * 43200,
                                      "agent": agent_id, "kind": kind, "attempted": what, "reason": why,
                                      "user_key": rng.choice(users["direct"] + users["judges"])})

    requests = [
        ("judges", "voice_minutes", 30, 48, "Judge exploring the live call in depth", "pending"),
        ("demo-bank", "daily_allowance", 50, 72, "Customer recording a long family history", "pending"),
        ("direct", "spoken_replies", 40, 24, "Owner with low vision prefers spoken replies", "pending"),
        ("judges", "daily_allowance", 50, 24, "Second judge session", "approved"),
        ("direct", "voice_minutes", 120, 168, "Asked for a week of unlimited voice", "rejected"),
    ]
    for i, (tenant, kind, amount, hours, why, status) in enumerate(requests):
        doc = {"id": new_id("req"), "seeded": True, "at": now - (i + 1) * 9000, "tenant": tenant,
               "user_key": users[tenant][i % len(users[tenant])], "kind": kind, "amount": amount,
               "hours": hours, "reason": why, "status": status}
        if status != "pending":
            doc |= {"decided_at": now - i * 7000, "decided_by": "seed", "decision_reason":
                    "Within the judges' allowance" if status == "approved" else "Above what one person needs"}
            if status == "approved":
                doc["expires_at"] = now + hours * 3600
        store.put("budget_requests", doc)

    events = [
        ("pause", "activation.checker", "Certificate registration number doesn't match the expected format", "open"),
        ("reduce_autonomy", "saathi.chat", "Recipient change requested from a new device", "confirmed"),
        ("add_check", "saathi.voice", "High-risk request by voice; second factor required", "confirmed"),
        ("pause", "activation.checker", "Owner used the app 2 days before the reported date", "open"),
        ("block", "handover.assistant", "Hidden instructions found in an uploaded document", "confirmed"),
        ("add_check", "handover.assistant", "Repeated questions beyond the recipient's entitlement", "open"),
        ("pause", "activation.checker", "Same certificate used in another case", "confirmed"),
        ("add_check", "saathi.chat", "Possible pressure from another person in the conversation", "reversed"),
        ("reduce_autonomy", "readiness.planner", "Nominee change during a waiting period", "confirmed"),
        ("add_check", "documents.reader", "Image shows signs of editing around the date", "reversed"),
    ]
    for i, (response, agent_id, why, status) in enumerate(events):
        doc = {"id": new_id("gev"), "seeded": True, "at": now - (i + 1) * 30000, "response": response,
               "agent": agent_id, "reason": why, "status": status,
               "case_key": "c_" + hashlib.sha256(f"case-{i}".encode()).hexdigest()[:8]}
        if status != "open":
            doc |= {"decided_by": "seed", "decided_at": now - i * 20000,
                    "decision_reason": "Checked with the owner" if status == "reversed" else "Confirmed by review"}
        store.put("guardian_events", doc)

    scenarios = [
        ("Report filed while the owner is in hospital with their phone off", "waiting period",
         "Backup contact alerted; waiting period", "covered", "verified"),
        ("SIM swap silences the owner's alerts", "owner alert", "Email and backup contact; voice alone can't act",
         "covered", "verified"),
        ("Real registrar template with a cloned QR code", "certificate checks",
         "QR checked against the state portal (fraud guide)", "covered", "verified"),
        ("Chosen recipient colludes with an outsider", "two reviewers", None, "gap", "dreamt"),
        ("Deepfake video call to a reviewer claiming to be the claimant", "two reviewers",
         "Reviewers never accept identity by video alone", "covered", "dreamt"),
    ]
    for i, (text, safeguard, caught_by, coverage, status) in enumerate(scenarios):
        store.put("fraud_scenarios", {"id": new_id("scn"), "seeded": True, "at": now - (i + 1) * 50000,
                                      "scenario": text, "safeguard": safeguard, "caught_by": caught_by,
                                      "coverage": coverage, "status": status,
                                      "review": "approved" if i < 3 else "pending",
                                      "evidence": 1 if status == "verified" else 0})

    guides = [
        ("India · certificate of passing (Civil Registration System)", 3, "human-reviewed", 6, 9),
        ("India · legal heir certificate", 1, "machine-confirmed", 3, 5),
        ("Singapore · death registration", 1, "unverified", 2, 3),
    ]
    for i, (title, version, tier, sources, signals) in enumerate(guides):
        store.put("fraud_guides", {"id": new_id("gde"), "seeded": True, "title": title, "version": version,
                                   "trust_tier": tier, "sources": sources, "signals": signals,
                                   "researched_at": now - (i + 1) * 86400 * 3, "status": "current"})

    for i in range(6):
        created = now - (i + 1) * 86400 * rng.uniform(0.5, 1.5)
        store.put("demo_copies", {"id": new_id("demo"), "seeded": True, "user_key": users["judges"][i],
                                  "created_at": created, "last_used_at": created + rng.uniform(600, 40000),
                                  "allowance_used": round(rng.uniform(0.1, 0.95), 2), "status": "active"})

    store.put("platform_settings", {
        "id": "settings", "seeded_at": now, "kill_switch": "off", "kill_switch_reason": None,
        # Gemini quota headroom: seeded until Cloud Monitoring is read (PR-10.5).
        "quota": {"rpm_limit": 1000, "rpm_peak": 212, "tpm_limit": 4_000_000, "tpm_peak": 640_000},
    })


def ensure_seeded() -> None:
    if not platform_settings():
        seed()


