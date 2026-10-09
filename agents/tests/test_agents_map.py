"""The agents map in the admin portal: which agents are working now, and a snapshot per agent.

A model call shows as working exactly while it runs (an open row, deleted in a finally); a row a
stopped server left behind stops counting after the agent's longest call; every registered agent
is on exactly one loop; and the live view holds no user and no text."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from app import gemini
from app.governance import core, portal
from app.schemas import DocumentCheck
from app.store import store
from tests.test_governance import client, h  # noqa: F401  (the portal fixture, signed-in roles)


@pytest.fixture(autouse=True)
def clean():
    for collection in ("agent_open_calls", "agent_pulse", "fraud_runs"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    store.put("platform_settings", {**core.platform_settings(), "id": "settings", "kill_switch": "off"})


def live(agent):
    return portal.agents_live()["agents"][agent]


def test_every_registered_agent_is_on_exactly_one_loop():
    on_map = [a for agents in portal.LOOPS.values() for a in agents]
    assert sorted(on_map) == sorted(a["id"] for a in core.registry())


def test_a_call_shows_as_working_while_it_runs_and_not_after():
    with core.working("saathi.chat"):
        assert live("saathi.chat")["state"] == "working" and live("saathi.chat")["running_calls"] == 1
    assert live("saathi.chat")["running_calls"] == 0


def test_a_failed_call_still_closes_its_open_row():
    with pytest.raises(RuntimeError), core.working("saathi.chat"):
        raise RuntimeError("model failed")
    assert store.list("agent_open_calls") == []


def test_a_gemini_call_is_open_during_the_request(monkeypatch):
    seen = []
    parsed = DocumentCheck(document_type="x", is_expected_type=True, matches_expected_person=True, confidence=0.9)

    class FakeModels:
        async def generate_content(self, **_):
            seen.append(live("activation.checker")["state"])
            return SimpleNamespace(parsed=parsed, text=None, usage_metadata=None)

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    asyncio.run(gemini.generate_json(model=core.primary_model("activation.checker"), schema=DocumentCheck,
                                     prompt="p", agent="activation.checker"))
    assert seen == ["working"]
    after = live("activation.checker")
    assert after["state"] == "active" and after["last"]["result"] == "done" and after["running_calls"] == 0


def test_an_open_row_left_by_a_stopped_server_stops_counting():
    old = time.time() - core.open_call_seconds("saathi.chat") - 1
    store.put("agent_open_calls", {"id": "cal_lost", "agent": "saathi.chat", "at": old})
    assert live("saathi.chat")["state"] == "idle"
    # A live call may last its max_call_minutes, so the same age still counts for voice.
    store.put("agent_open_calls", {"id": "cal_voice", "agent": "saathi.voice", "at": old})
    assert live("saathi.voice")["state"] == "working"


def test_the_last_call_and_todays_use_come_from_the_ledger():
    core.record_usage("legal.needs", "gemini-3.1-flash-lite", "direct", tokens_in=100, tokens_out=20)
    core.record_usage("legal.needs", "gemini-3.1-flash-lite", "direct", error=True)
    agent = live("legal.needs")
    assert agent["state"] == "active" and agent["last"]["result"] == "failed"
    assert agent["today"]["calls"] >= 2 and agent["today"]["tokens"] >= 120 and agent["today"]["errors"] >= 1


def test_the_kill_switch_shows_as_paused_except_for_safety_agents():
    store.put("platform_settings", {**core.platform_settings(), "id": "settings", "kill_switch": "all_paused"})
    assert live("saathi.chat")["state"] == "paused" and live("saathi.chat")["paused_by"] == "kill_switch"
    assert live("activation.checker")["paused_by"] is None


def test_a_running_fraud_run_shows_its_agent_working_with_its_counts():
    store.put("fraud_runs", {"id": "run_1", "kind": "research", "status": "running", "started_at": time.time(),
                             "counts": {"subjects": 1}, "steps": [{"line": "some page text"}], "by": "x@y.test"})
    agent = live("fraud.researcher")
    assert agent["state"] == "working" and agent["last_run"]["counts"] == {"subjects": 1}
    assert "steps" not in agent["last_run"] and "by" not in agent["last_run"]  # state and counts only
    assert live("fraud.dreamer")["state"] != "working"


def test_the_live_view_needs_a_platform_role_and_holds_no_user(client, monkeypatch):  # noqa: F811
    from app.config import settings
    with core.working("saathi.chat"):
        page = client.get("/api/platform/agents/live", headers=h("judge")).json()
    assert page["agents"]["saathi.chat"]["state"] == "working" and "talk" in page["loops"]
    assert "user_key" not in str(page) and "u-judge" not in str(page)
    monkeypatch.setattr(settings, "platform_default_role", "none")
    assert client.get("/api/platform/agents/live", headers=h("judge")).status_code == 403
