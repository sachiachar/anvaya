"""A daily ceiling for the whole service (PR-15.1): stub calls fill it, optional agents stop
first, then everything but safety; certificate checks always run; calls on a person's own key
don't count; a live call's minutes are held; and the admin portal shows it."""

import copy

import pytest
import yaml
from fastapi.testclient import TestClient

from app import main
from app.governance import core, files
from app.store import store


@pytest.fixture(autouse=True)
def fresh():
    core.set_current_user(None)  # no person, so only the service ceiling applies
    for doc in store.list("service_usage"):
        store.delete("service_usage", doc["id"])
    yield
    core.set_current_user(None)


def ceiling() -> dict:
    return core.service_ceiling()


def spend_tokens(share: float) -> None:
    """Stub calls on our key, adding up to this share of today's token ceiling."""
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=round(ceiling()["tokens_per_day"] * share))


def test_the_ceiling_is_in_the_policies_file():
    c = ceiling()
    assert c["tokens_per_day"] == 10_000_000 and c["voice_minutes_per_day"] == 300
    assert c["safety_reserve"] > 0  # capacity is kept for certificate checks
    assert c["optional_pause_at"] < 1 - c["safety_reserve"]  # optional agents stop before the rest


def test_optional_agents_stop_first_then_everything_but_safety():
    spend_tokens(ceiling()["optional_pause_at"])
    with pytest.raises(core.Refused, match="daily limit"):
        core.check_call("saathi.summary", "gemini-3.1-flash-lite")  # optional
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")         # not optional: still runs
    assert core.service_status()["state"] == "optional_paused"

    spend_tokens(1 - ceiling()["safety_reserve"] - ceiling()["optional_pause_at"])
    with pytest.raises(core.Refused, match="daily limit"):
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    core.check_call("activation.checker", "gemini-3.1-flash-lite")  # a certificate check still runs
    assert core.service_status()["state"] == "paused"
    assert any(b["kind"] == "ceiling" and b["agent"] == "saathi.chat" for b in store.list("gateway_blocked"))


def test_a_certificate_check_runs_even_past_the_ceiling():
    spend_tokens(1.5)
    core.check_call("activation.checker", "gemini-3.1-flash-lite")
    core.record_usage("activation.checker", "gemini-3.1-flash-lite", "direct", tokens_in=1000)  # and is counted
    assert store.get("service_usage", core._today())["tokens"] == round(ceiling()["tokens_per_day"] * 1.5) + 1000


def test_calls_on_a_persons_own_key_dont_count():
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=ceiling()["tokens_per_day"],
                      key_source="judge_studio")
    assert store.get("service_usage", core._today()) is None
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")


def test_voice_is_limited_by_minutes_and_its_tokens_still_count():
    core.record_usage("saathi.voice", "gemini-3.8-live", "direct", minutes=ceiling()["voice_minutes_per_day"],
                      tokens_in=4000, tokens_out=1000)
    with pytest.raises(core.Refused):
        core.check_call("saathi.voice", "gemini-3.8-live")
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")     # tokens are far from the line
    core.check_call("saathi.summary", "gemini-3.1-flash-lite")  # optional, but limited by tokens
    status = core.service_status()
    assert status["voice_minutes"]["used"] == ceiling()["voice_minutes_per_day"]
    assert status["tokens"]["used"] == 5000  # our key paid for them
    assert status["voice_minutes"]["state"] == "paused" and status["tokens"]["state"] == "ok"


def test_testing_a_persons_own_key_is_never_refused_by_the_ceiling():
    spend_tokens(1.5)
    core.check_call("access.tester", "gemini-3.1-flash-lite")  # it calls only the key being tested


def test_a_live_call_past_midnight_gives_back_its_minutes_on_the_day_it_held_them(monkeypatch):
    monkeypatch.setattr(core, "_today", lambda: "2026-10-08")
    held_on = core.reserve("saathi.voice", 20)
    monkeypatch.setattr(core, "_today", lambda: "2026-10-09")
    core.release("saathi.voice", 20, held_on)
    assert store.get("service_usage", "2026-10-08")["voice_minutes"] == 0
    assert store.get("service_usage", "2026-10-09") is None


def test_a_live_call_holds_its_minutes_and_gives_them_back():
    voice_line = ceiling()["voice_minutes_per_day"] * ceiling()["optional_pause_at"]
    assert core.service_remaining("saathi.voice") == voice_line
    core.reserve("saathi.voice", 20)
    assert core.service_remaining("saathi.voice") == voice_line - 20
    core.release("saathi.voice", 20)
    assert core.service_remaining("saathi.voice") == voice_line


def test_the_ceiling_resets_each_day():
    store.put("service_usage", {"id": "2000-01-01", "day": "2000-01-01", "tokens": 10**12})
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")


def test_a_wrong_ceiling_stops_start_up():
    reg = yaml.safe_load(core.REGISTRY_PATH.read_text())
    pol = yaml.safe_load(core.POLICIES_PATH.read_text())
    files.check(reg, pol)
    for bad in ({"safety_reserve": 1.0}, {"optional_pause_at": 0}, {"tokens_per_day": "10000000"}, {"extra": 1},
                {"optional_pause_at": 0.95}):  # optional agents would run after everything else stopped
        broken = copy.deepcopy(pol)
        broken["service_ceiling"] |= bad
        with pytest.raises(ValueError):
            files.check(reg, broken)


def test_the_portal_overview_shows_todays_use_under_the_ceiling():
    spend_tokens(0.5)
    body = TestClient(main.app).get("/api/platform/overview").json()["service_ceiling"]
    assert body["state"] == "ok"
    assert body["tokens"]["share"] == 0.5 and body["tokens"]["limit"] == 10_000_000
