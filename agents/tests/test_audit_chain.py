"""One audit chain (PR-2.12.5): a reviewer follows one conversation turn by ids, turn → guardian
decision → card → Keep → plan version → next step, with no words anywhere. Synthetic data only."""

import json
import re

import pytest
from app import audit_chain
from app.governance import core, portal
from app.schemas import Moment
from app.store import store
from fastapi import HTTPException
from tests.test_grounding import OWNER, _chat, _turn, client  # noqa: F401  (the app with a seeded demo)


@pytest.fixture(autouse=True)
def clean():
    for collection in ("guardian_actions", "guardian_events", "conversation_plans", "plan_versions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])


def last_owner_turn() -> str:
    return max((t for t in store.list("turns", owner_id=OWNER) if t["role"] == "owner"), key=lambda t: t["at"])["id"]


def story(text: str, kind: str = "story", title: str = "Shimla in the snow") -> Moment:
    return Moment(kind=kind, title=title, summary=text, quote=text)


def test_a_kept_story_is_followed_from_its_turn_to_its_kept_item(client, monkeypatch):  # noqa: F811
    said = "We went to Shimla in 1985 and it snowed all week."
    [card] = [c for c in _chat(client, monkeypatch, said, _turn(moments=[story(said)])) if c["collection"] == "moments"]
    assert client.post(f"/api/proposals/{card['id']}/decision",
                       json={"collection": "moments", "decision": "approve"}).status_code == 200
    chain = audit_chain.for_turn(last_owner_turn())
    [statement] = chain["statements"]
    assert statement["response"] == "continue" and statement["card"] == "moments:0"
    [kept] = chain["cards"]
    assert kept["id"] == card["id"] and kept["status"] == "kept" and kept["statement"] == statement["id"]
    assert kept["keep"]["kept"] and kept["keep"]["response"] == "continue"
    assert any(p["cause"].get("card") == card["id"] for p in chain["plans"])  # the Keep moved the plan on
    assert chain["reply"]["step"]  # the step Saathi carried out in its reply
    assert "Shimla" not in json.dumps(chain) and "snowed" not in json.dumps(chain)  # ids and decisions only


def test_a_deflected_secret_is_followed_from_its_event_to_the_safe_route(client, monkeypatch):  # noqa: F811
    said = "My ATM PIN is 4471, write that down for my son."
    _chat(client, monkeypatch, said, _turn(moments=[story(said, "practical", "ATM PIN")]))
    [statement] = audit_chain.for_turn(last_owner_turn())["statements"]
    assert statement["response"] == "deflect" and statement["event_id"]
    chain = audit_chain.for_event(statement["event_id"])
    assert chain["cards"] == [] and chain["turn"]["held_back"] == 1
    [plan] = [p for p in chain["plans"] if p["cause"].get("guardian") == statement["id"]]
    assert plan["current"] == "safe:secret" and chain["reply"]["step"] == "safe:secret"
    # As a whole word: a random id or a timestamp (1760164471.5) may contain these digits by chance.
    assert not re.search(r"\b4471\b", json.dumps(chain))


def test_a_plan_step_shows_only_its_kind():
    assert audit_chain.safe_step("gap:nominee:sbi|4808|savings account") == "gap:nominee"
    assert audit_chain.safe_step("safe:secret:turn_1") == "safe:secret"
    assert audit_chain.safe_step("mood:sad:turn_2") == "mood:sad"
    assert audit_chain.safe_step("task:task_123") == "suggestion"
    assert audit_chain.safe_step("initial:people") == "initial:people"


def test_recent_conversations_list_counts_and_hashed_keys_only(client, monkeypatch):  # noqa: F811
    said = "My ATM PIN is 4471, write that down for my son."
    _chat(client, monkeypatch, said, _turn(moments=[story(said, "practical", "ATM PIN")]))
    [row] = audit_chain.recent()
    assert row["turn"] == last_owner_turn() and row["decisions"] == 1 and row["held"] == 1
    assert set(row) == {"turn", "at", "user_key", "decisions", "held"}


def test_the_portal_shows_a_chain_and_a_demo_admin_sees_only_their_own(client, monkeypatch):  # noqa: F811
    said = "We went to Shimla in 1985 and it snowed all week."
    _chat(client, monkeypatch, said, _turn(moments=[story(said)]))
    turn = last_owner_turn()
    owner_key = audit_chain.for_turn(turn)["turn"]["user_key"]
    team = core.Actor(email="team@example.com", key="u_team", role="risk_reviewer", permissions=[])
    stranger = core.Actor(email="judge@example.com", key="u_stranger", role="demo_admin", permissions=[])
    mine = core.Actor(email="me@example.com", key=owner_key, role="demo_admin", permissions=[])
    assert portal.get_chain(turn, team)["turn"]["id"] == turn
    assert portal.get_chain(turn, mine)["turn"]["id"] == turn
    with pytest.raises(HTTPException):
        portal.get_chain(turn, stranger)
    with pytest.raises(HTTPException):
        portal.get_chain("turn_missing", team)
    assert portal.get_conversations(stranger)["conversations"] == []
    assert [c["turn"] for c in portal.get_conversations(team)["conversations"]] == [turn]
