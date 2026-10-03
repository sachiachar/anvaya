"""Seeded demo data for the governance layer, only for what has no real source yet:
budget requests, guardian interventions, fraud scenarios and guides, judges' demo copies
and the quota headroom. Each one goes when its real feature arrives.

Never seeded: spend (Google Cloud billing, billing.py), and usage and blocked calls, which
the gateway records for real.
"""

from __future__ import annotations

import hashlib
import random
import time

from ..store import new_id, store
from .core import platform_settings, user_key

# Version of the seeded data's format. Version 4 stops seeding usage and blocked calls,
# and removes those written earlier (versions 1 to 3).
DATA_VERSION = 4
# Demo tenants and their number of seeded users.
TENANTS = {"direct": 20, "demo-bank": 18, "judges": 8}
# Collections that held seeded documents. Usage days and blocked calls are only cleared:
# seeded days shared their date ID with the real ledger, so real calls since the gateway
# went live were added to them and can't be told apart. Clearing them leaves only real use.
CLEARED = ("usage_days", "gateway_blocked", "budget_requests", "guardian_events",
           "fraud_scenarios", "fraud_guides", "demo_copies")


def seed(now: float | None = None) -> None:
    """Create clearly labelled demo data for the parts of the portal with no real source yet."""
    now = now or time.time()
    rng = random.Random(42)
    users = {t: [user_key(f"seed-{t}-{i}") for i in range(n)] for t, n in TENANTS.items()}

    for collection in CLEARED:
        for doc in store.list(collection, seeded=True):
            store.delete(collection, doc["id"])

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

    current = platform_settings()  # re-seeding never changes the kill switch
    store.put("platform_settings", {
        "id": "settings", "data_version": DATA_VERSION, "seeded_at": now,
        "kill_switch": current.get("kill_switch", "off"), "kill_switch_reason": current.get("kill_switch_reason"),
        # Gemini quota headroom: seeded until Cloud Monitoring is read (PR-10.5).
        "quota": {"rpm_limit": 1000, "rpm_peak": 212, "tpm_limit": 4_000_000, "tpm_peak": 640_000},
    })


def ensure_seeded() -> None:
    """Seed on first use, and re-seed data written in an older format (versions 1 to 3 seeded usage)."""
    if platform_settings().get("data_version") != DATA_VERSION:
        seed()


