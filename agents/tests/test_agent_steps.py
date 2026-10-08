"""The agents panel (PR-15.7): a simple trace of each demo copy's AI steps. One line per call
(done or failed) and per call the gateway stopped, newest first, with the agent's purpose and
autonomy; only the caller's own copy, never any text. Each card, step and check carries its
autonomy label from the agent's policy."""

import pathlib
import time

import pytest
import yaml
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
    assert set(stored) == {"id", "at", "day", "user_key", "agent", "model", "tokens", "minutes", "result",
                           "key_source", "expire_at"}


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


def test_resetting_a_demo_empties_its_panel_and_no_one_elses(client):
    call(ME, tokens_in=1)
    call(OTHER, tokens_in=1)
    core.record_blocked("saathi.chat", "allowance", "owner.tokens", "Daily allowance reached", user=ME)
    store.put("steps_not_pursued", {"id": "snp_1", "at": time.time(), "rule": "secret", "user_key": ME})
    time.sleep(0.01)
    client.post("/api/demo/seed")
    body = client.get("/api/agents/steps").json()
    assert body == {"steps": [], "steps_not_pursued_30d": 0}
    assert store.list("agent_steps", user_key=OTHER)  # another copy keeps its trace
    assert store.list("gateway_blocked", user_key=ME)  # the team's record of refused calls stays


def test_a_refused_call_belongs_to_the_person_asking_and_a_key_fallback_isnt_stopped(client):
    core.set_current_user(ME)
    core.record_blocked("saathi.chat", "kill_switch", "all_paused", "Paused by the kill switch")  # no user given
    core.record_blocked("saathi.chat", "key_fallback", "judge_studio", "Key failed (429); trying the next one")
    [step] = client.get("/api/agents/steps").json()["steps"]
    assert (step["result"], step["reason"]) == ("stopped", "Paused by the kill switch")


def test_the_release_turns_on_the_traces_expiry_for_the_same_field():
    """The TTL policy is part of every API release (deploy.yml), not a step someone must remember;
    it names the collection and field the code writes, and the deployer may set it."""
    repo = pathlib.Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "deploy.yml").read_text())
    run = next(s["run"] for s in workflow["jobs"]["api"]["steps"] if "ttls update" in s.get("run", ""))
    call(ME, tokens_in=1)
    stored = store.list("agent_steps", user_key=ME)[0]
    assert "expire_at" in stored and "--collection-group=agent_steps" in run and "ttls update expire_at" in run
    assert "roles/datastore.indexAdmin" in (repo / "infra" / "setup-github-actions.sh").read_text()


def test_the_labels_come_from_registered_agents():
    for agent in (main.CARDS, main.STEPS, loops.CHECKER):
        assert core.policy(agent) is not None, agent  # a typo would quietly label everything "assist"


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
