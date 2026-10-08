"""The agents panel (PR-15.7): a simple trace of each demo copy's AI steps. One line per call
(done or failed) and per call the gateway stopped, newest first, with the agent's purpose and
autonomy; only the caller's own copy, never any text. Each card, step and check carries its
autonomy label from the agent's policy."""

import time

import pytest
from fastapi.testclient import TestClient

from app import demo, loops, main, proposals
from app.governance import core
from app.schemas import DocumentCheck
from app.store import store

ME = core.user_key(demo.LOCAL)  # the caller's copy when sign-in is off
OTHER = "u_someoneelse"
STEP_FIELDS = {"at", "agent", "purpose", "model", "tokens", "minutes", "autonomy", "result", "reason", "key"}


@pytest.fixture(autouse=True)
def fresh():
    for collection in ("agent_steps", "gateway_blocked", "steps_not_pursued"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    yield
    core.set_current_user(None)


@pytest.fixture()
def client():
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    return c


def call(user: str, agent: str = "saathi.chat", **kw) -> None:
    core.set_current_user(user)
    core.record_usage(agent, "gemini-3.1-flash-lite", "direct", **kw)


def test_each_call_is_one_line_and_a_judge_sees_only_their_own(client):
    call(ME, tokens_in=900, tokens_out=100)
    call(ME, "handover.assistant", error=True)
    call(OTHER, tokens_in=5)
    steps = client.get("/api/agents/steps").json()["steps"]
    assert [(s["agent"], s["result"]) for s in steps] == [("handover.assistant", "failed"), ("saathi.chat", "done")]
    chat = steps[1]
    assert set(chat) == STEP_FIELDS
    assert (chat["tokens"], chat["autonomy"], chat["key"]) == (1000, "act_within_limits", "ours")
    assert chat["purpose"] == "Typed or photo conversation with the owner"


def test_a_stopped_call_shows_as_stopped_with_the_gateways_reason(client):
    core.set_current_user(ME)
    core.record_blocked("saathi.voice", "ceiling", "service.voice_minutes", "Daily service ceiling reached", user=ME)
    core.record_blocked("saathi.chat", "allowance", "owner.tokens", "Daily allowance reached", user=OTHER)
    [step] = client.get("/api/agents/steps").json()["steps"]
    assert (step["agent"], step["result"], step["reason"]) == ("saathi.voice", "stopped", "Daily service ceiling reached")
    assert step["autonomy"] == "act_within_limits" and step["key"] is None


def test_no_text_ever_and_a_persons_own_key_is_marked(client):
    call(ME, tokens_in=10, key_source="judge_studio")
    [step] = client.get("/api/agents/steps").json()["steps"]
    assert step["key"] == "yours"
    stored = store.list("agent_steps", user_key=ME)[0]
    assert set(stored) == {"id", "at", "user_key", "agent", "model", "tokens", "minutes", "result", "key_source"}


def test_only_the_last_day_and_at_most_50(client):
    for _ in range(55):
        call(ME, tokens_in=1)
    store.put("agent_steps", {"id": "stp_old", "at": time.time() - 25 * 3600, "user_key": ME, "agent": "saathi.chat",
                              "model": "m", "tokens": 1, "minutes": 0, "result": "done", "key_source": "platform"})
    body = client.get("/api/agents/steps").json()
    assert len(body["steps"]) == 50 and all(s["at"] > time.time() - 24 * 3600 for s in body["steps"])


def test_steps_not_pursued_are_counted_for_the_copy_only(client):
    store.put("steps_not_pursued", {"id": "snp_1", "at": time.time(), "rule": "secret", "user_key": ME})
    store.put("steps_not_pursued", {"id": "snp_2", "at": time.time(), "rule": "secret", "user_key": OTHER})
    assert client.get("/api/agents/steps").json()["steps_not_pursued_30d"] == 1


def test_resetting_a_demo_deletes_its_trace(client):
    call(ME, tokens_in=1)
    call(OTHER, tokens_in=1)
    client.post("/api/demo/seed")
    assert store.list("agent_steps", user_key=ME) == [] and store.list("agent_steps", user_key=OTHER)


def test_each_card_step_and_check_carries_its_autonomy_label(client):
    owner = client.get("/api/me").json()["owner_id"]
    proposals.save_moment(owner, {"kind": "story", "title": "The scooter", "summary": "s", "quote": "q",
                                  "people": [], "places": [], "year": None}, {})
    cards = client.get(f"/api/owners/{owner}/review").json()["moments"]
    assert cards and all(c["autonomy"] == "propose" for c in cards)
    assert all(t["autonomy"] == "propose" for t in client.get(f"/api/owners/{owner}/tasks").json()["tasks"])
    check = DocumentCheck(document_type="death certificate", is_expected_type=True, matches_expected_person=True,
                          confidence=0.9)
    assert loops.review_summary(check)["autonomy"] == "assist"
