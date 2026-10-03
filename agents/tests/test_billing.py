"""Actual spend comes only from the Cloud Billing export (PR-10.9): summed in US dollars net of
credits, cached for an hour, and "not available" with a reason instead of a guess."""

from datetime import date

import pytest

from app.config import settings
from app.governance import billing
from app.store import store

ROWS = [
    {"day": "2026-10-01", "service": "Gemini API", "sku": "Flash Lite input", "usd": 1.25},
    {"day": "2026-10-01", "service": "Cloud Run", "sku": "CPU", "usd": 0.5},
    {"day": "2026-10-02", "service": "Gemini API", "sku": "Flash Lite input", "usd": 2.0},
    {"day": "2026-09-30", "service": "Gemini API", "sku": "TTS output", "usd": 4.0},
]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.delete("billing_cache", "spend")
    monkeypatch.setattr(settings, "billing_export_table", "life-after.anvaya_platform")


def test_month_to_date_forecast_and_breakdowns():
    s = billing.summarise(ROWS, date(2026, 10, 3), 300.0)
    assert s["available"] and s["through"] == "2026-10-02" and s["currency"] == "USD"
    assert s["month_to_date_usd"] == 3.75
    # The latest loaded day (2 Oct) may still be filling up: the forecast uses 1 Oct only.
    assert s["days_counted"] == 1 and s["forecast_usd"] == round(1.75 * 31, 2)
    assert s["daily"] == [{"day": "2026-09-30", "cost_usd": 4.0}, {"day": "2026-10-01", "cost_usd": 1.75},
                          {"day": "2026-10-02", "cost_usd": 2.0}]
    assert list(s["by_service"]) == ["Gemini API", "Cloud Run"] and s["by_service"]["Gemini API"] == 7.25
    assert s["top_skus"][0] == {"service": "Gemini API", "sku": "TTS output", "cost_usd": 4.0}


def test_service_totals_cover_the_same_30_days_as_the_chart():
    old = [{"day": f"2026-09-{d:02d}", "service": "Cloud Run", "sku": "CPU", "usd": 1.0} for d in range(1, 31)]
    s = billing.summarise([*old, *ROWS], date(2026, 10, 3), 300.0)
    assert [d["day"] for d in s["daily"]][0] == "2026-09-03" and len(s["daily"]) == 30
    assert s["by_service"]["Cloud Run"] == 28 + 0.5  # 3 to 30 September, and 1 October


def test_first_day_of_the_month_has_no_forecast_yet():
    s = billing.summarise([r for r in ROWS if r["day"] == "2026-09-30"], date(2026, 10, 1), 300.0)
    assert s["month_to_date_usd"] == 0 and s["forecast_usd"] == 0 and s["days_counted"] == 0


def test_not_configured_says_so(monkeypatch):
    monkeypatch.setattr(settings, "billing_export_table", "")
    assert billing.spend() == {"available": False, "reason": "not_configured",
                               "message": billing.NOT_AVAILABLE["not_configured"]}


def test_empty_export_and_errors_are_not_guessed(monkeypatch):
    monkeypatch.setattr(billing, "_fetch_rows", lambda: [])
    assert billing.spend(now=1000)["reason"] == "no_data"

    store.delete("billing_cache", "spend")

    def broken():
        raise RuntimeError("403 access denied on dataset")

    monkeypatch.setattr(billing, "_fetch_rows", broken)
    s = billing.spend(now=1000)
    assert s["reason"] == "error" and "403" not in s["message"]  # no internals on the screen


def test_cached_for_an_hour(monkeypatch):
    calls = []
    monkeypatch.setattr(billing, "_fetch_rows", lambda: calls.append(1) or ROWS)
    billing.spend(now=1000)
    billing.spend(now=1000 + billing.CACHE_SECONDS - 1)
    assert len(calls) == 1
    billing.spend(now=1000 + billing.CACHE_SECONDS + 1)
    assert len(calls) == 2


def test_dataset_finds_the_detailed_export_table():
    assert billing._table("life-after.anvaya_platform") == \
        "life-after.anvaya_platform.gcp_billing_export_resource_v1_*"
    assert billing._table("p.d.gcp_billing_export_v1_X") == "p.d.gcp_billing_export_v1_X"
    sql = billing._query("p.d.t")
    assert "currency_conversion_rate" in sql and "UNNEST(credits)" in sql


def test_reads_bigquery_with_the_project_filter(monkeypatch):
    from google.cloud import bigquery

    seen = {}

    class FakeJob:
        def result(self, timeout):
            return [{"day": date(2026, 10, 2), "service": "Gemini API", "sku": "x", "usd": 1.5}]

    class FakeClient:
        def __init__(self, project):
            seen["project"] = project

        def query(self, sql, job_config):
            seen["sql"], seen["params"] = sql, job_config.query_parameters
            return FakeJob()

    monkeypatch.setattr(bigquery, "Client", FakeClient)
    monkeypatch.setattr(settings, "firebase_project_id", "life-after")
    rows = billing._fetch_rows()
    assert rows == [{"day": "2026-10-02", "service": "Gemini API", "sku": "x", "usd": 1.5}]
    assert seen["project"] == "life-after" and "project.id = @project" in seen["sql"]
    assert seen["params"][0].value == "life-after"


def test_refresh_reads_again_but_at_most_once_a_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(billing, "_fetch_rows", lambda: calls.append(1) or ROWS)
    billing.spend(now=1000)
    billing.spend(now=1000 + billing.FRESH_SECONDS - 1, fresh=True)  # clicked again straight away
    assert len(calls) == 1
    billing.spend(now=1000 + billing.FRESH_SECONDS + 1, fresh=True)
    assert len(calls) == 2
    billing.spend(now=1000 + billing.FRESH_SECONDS + 2)  # a normal load uses the new cache
    assert len(calls) == 2
