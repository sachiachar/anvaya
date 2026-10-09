"""The dashboard's usage charts: use per agent by hour (kept 14 days) and by day (30 days)."""

import time
from datetime import datetime, timedelta, timezone

import pytest
from app.governance import core, portal
from app.store import store


@pytest.fixture(autouse=True)
def clean():
    for doc in store.list("usage_hours"):
        store.delete("usage_hours", doc["id"])


def test_each_call_is_counted_in_its_hour_per_agent():
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=100, tokens_out=20)
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=10, tokens_out=5)
    core.record_usage("saathi.voice", "gemini-3.8-live", "direct", minutes=2.5)
    [hour] = portal.overview()["usage"]["hourly"]
    assert hour["hour"] == datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    assert hour["agents"]["saathi.chat"] == {"calls": 2, "tokens": 135, "minutes": 0.0}
    assert hour["agents"]["saathi.voice"]["minutes"] == 2.5


def test_an_hourly_row_expires_after_the_kept_days():
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=1)
    [row] = store.list("usage_hours")
    hour = datetime.strptime(row["hour"], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    assert row["expire_at"] == hour + timedelta(days=core.HOURS_KEPT_DAYS)


def test_hours_older_than_the_kept_days_are_not_shown_and_the_rest_are_in_order():
    old = time.strftime("%Y-%m-%dT%H", time.gmtime(time.time() - (core.HOURS_KEPT_DAYS + 1) * 86400))
    store.put("usage_hours", {"id": old, "hour": old, "agents": {"saathi.chat": {"calls": 1, "tokens": 9}}})
    store.put("usage_hours", {"id": "b", "hour": time.strftime("%Y-%m-%dT%H"),
                              "agents": {"saathi.chat": {"calls": 1, "tokens": 1}}})
    earlier = time.strftime("%Y-%m-%dT%H", time.gmtime(time.time() - 3 * 3600))
    store.put("usage_hours", {"id": "a", "hour": earlier, "agents": {"saathi.chat": {"calls": 1, "tokens": 1}}})
    assert [h["hour"] for h in portal.overview()["usage"]["hourly"]] == [earlier, time.strftime("%Y-%m-%dT%H")]


def test_each_day_is_split_per_agent():
    core.record_usage("legal.needs", "gemini-3.1-flash-lite", "direct", tokens_in=40, tokens_out=2)
    today = portal.overview()["usage"]["daily_by_agent"][-1]
    assert today["day"] == core._today() and today["agents"]["legal.needs"]["tokens"] >= 42
