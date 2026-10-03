"""The usage ledger behind the portal's spend figures: atomic, so overlapping calls never
lose a count, and readable on days with only real use (no seeded data)."""

import threading

from fastapi.testclient import TestClient

from app import main
from app.governance import core
from app.governance.seed import seed
from app.store import store


def test_overlapping_calls_never_lose_a_count():
    seed()
    before = store.get("usage_days", core._today())["agents"].get("saathi.chat", {"calls": 0, "tokens_in": 0})

    def one_call():
        core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=100, tokens_out=10,
                          cost_usd=0.001)

    threads = [threading.Thread(target=one_call) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    row = store.get("usage_days", core._today())["agents"]["saathi.chat"]
    assert row["calls"] == before["calls"] + 50
    assert row["tokens_in"] == before["tokens_in"] + 5000


def test_ledger_counts_agent_model_and_tenant_in_one_record():
    store.delete("usage_days", core._today())
    core.record_usage("activation.checker", "gemini-3.1-flash-lite", "demo-bank", tokens_in=1000, tokens_out=200,
                      cost_usd=0.0006, error=True)
    day = store.get("usage_days", core._today())
    assert day["agents"]["activation.checker"] == {"calls": 1, "tokens_in": 1000, "tokens_out": 200,
                                                   "minutes": 0.0, "cost_usd": 0.0006, "errors": 1}
    assert day["models"]["gemini-3.1-flash-lite"] == 0.0006
    assert day["tenants"]["demo-bank"]["cost_usd"] == 0.0006


def test_portal_reads_a_day_with_only_real_use():
    seed()
    store.delete("usage_days", core._today())  # today: no seeded data, only real calls
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=500, tokens_out=50,
                      cost_usd=0.0002)
    overview = TestClient(main.app).get("/api/platform/overview")
    assert overview.status_code == 200
    assert overview.json()["daily"][-1]["day"] == core._today()
