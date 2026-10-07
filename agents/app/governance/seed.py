"""The admin portal shows only real data. Earlier versions seeded examples (usage, blocked calls,
budget requests, guardian interventions, fraud scenarios and guides, judges' demo copies and
the quota headroom); this removes any that are left, once, on the first portal request.

Sections fill as their features record real events: budget requests from "ask for more",
interventions from the guardian (PR-2.7), fraud scenarios and guides from the research and
dreaming agents, demo copies from sign-in (PR-1), and quota from Cloud Monitoring (PR-10.5).
"""

from __future__ import annotations

from ..store import store
from .core import platform_settings

# Version of the portal's data. Version 5 holds no seeded examples at all.
DATA_VERSION = 5
# Collections that held seeded documents (each marked seeded=True). Real ones are kept.
CLEARED = ("usage_days", "gateway_blocked", "budget_requests", "guardian_events",
           "fraud_scenarios", "fraud_guides", "demo_copies", "steps_not_pursued")


def clear_samples() -> None:
    """Remove every seeded example. Never touches real data or the settings people chose."""
    for collection in CLEARED:
        for doc in store.list(collection, seeded=True):
            store.delete(collection, doc["id"])
    current = platform_settings()
    # Keep every setting people chose (the kill switch, privacy settings...); drop only the samples.
    kept = {k: v for k, v in current.items() if k not in ("quota", "seeded_at")}
    store.put("platform_settings", {**kept, "id": "settings", "data_version": DATA_VERSION,
                                    "kill_switch": current.get("kill_switch", "off"),
                                    "kill_switch_reason": current.get("kill_switch_reason")})


def ensure_current() -> None:
    """Run once per data version: on the first portal request after a release."""
    if platform_settings().get("data_version") != DATA_VERSION:
        clear_samples()
