"""The usage ledger behind the portal's usage figures: atomic, so overlapping calls never
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
        core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=100, tokens_out=10)

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
                      error=True)
    day = store.get("usage_days", core._today())
    assert day["agents"]["activation.checker"] == {"calls": 1, "tokens_in": 1000, "tokens_out": 200,
                                                   "minutes": 0.0, "errors": 1}
    assert day["model_use"]["gemini-3.1-flash-lite"] == {"calls": 1, "tokens": 1200, "minutes": 0.0}
    assert day["tenants"]["demo-bank"] == {"calls": 1, "tokens": 1200, "minutes": 0.0}


def test_portal_reads_a_day_with_only_real_use():
    seed()
    store.delete("usage_days", core._today())  # today: no seeded data, only real calls
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=500, tokens_out=50)
    overview = TestClient(main.app).get("/api/platform/overview")
    assert overview.status_code == 200
    usage = overview.json()["usage"]
    assert usage["daily"][-1]["day"] == core._today() and usage["daily"][-1]["calls"] >= 1


def test_portal_reads_a_day_written_before_costs_came_from_billing():
    seed()
    store.put("usage_days", {"id": core._today(), "day": core._today(),
                             "agents": {"saathi.chat": {"calls": 2, "tokens_in": 10, "tokens_out": 5, "minutes": 0.0,
                                                        "cost_usd": 0.01, "errors": 0}},
                             "models": {"gemini-3.1-flash-lite": 0.01}, "tenants": {"direct": {"cost_usd": 0.01}}})
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=1, tokens_out=1)
    overview = TestClient(main.app).get("/api/platform/overview")
    assert overview.status_code == 200
    assert overview.json()["usage"]["daily"][-1]["calls"] == 3
