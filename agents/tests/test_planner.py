"""The conversation planner (PR-2.12.2): the only source of what Saathi talks about with the owner.

A new owner starts from the initial plan; each step says who starts it; a kept item, an outcome
or a guardian deflection moves the plan on; gaps and readiness feed only the plan; every step
passes the guardian's next-step check; and each version records its cause with no words.
All data here is synthetic."""

import time
import uuid

import pytest
from app import knowledge, planner, step_guard
from app.schemas import Moment
from app.store import store
from tests.test_grounding import OWNER, _chat, _turn, client  # noqa: F401  (the app with a seeded demo)

NEW = ""  # a fresh owner for each test, with nothing kept


@pytest.fixture(autouse=True)
def clean():
    global NEW
    for collection in ("conversation_plans", "plan_versions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    NEW = f"owner_test_{uuid.uuid4().hex[:8]}"
    store.put("owners", {"id": NEW, "name": "Test Owner"})


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def test_the_initial_plan_file_is_valid_and_says_who_starts_each_step():
    plan = planner.initial_plan()
    assert [s.id for s in plan.steps][:3] == ["people", "story", "money"]
    assert {s.opens for s in plan.steps} == {"saathi", "owner"}
    with pytest.raises(ValueError):
        planner.InitialPlan.model_validate({"version": 1, "steps": [
            {"id": "x", "topic": "t", "ask": "a", "done_when": "person", "opens": "anyone"}]})


def test_a_new_owner_starts_from_the_initial_plan_and_saathi_opens_the_first_step():
    view = planner.view(planner.current(NEW))
    assert view["current"]["id"] == "initial:people" and view["current"]["opens"] == "saathi"
    wishes = next(s for s in view["next"] if s["id"] == "initial:wishes")
    assert wishes["opens"] == "owner"  # a sensitive step waits for the owner to start it
    order = [s["source"] for s in [view["current"], *view["next"]]]
    assert order.index("initial") < len(order) and "gap" not in order[:order.count("initial")]
    [version] = store.list("plan_versions", owner_id=NEW)
    assert version["cause"] == {"kind": "cold_start"} and version["current"] == "initial:people"
    assert "closest" not in str(version) and "topic" not in str(version)  # ids and sources only, no words


def test_keeping_a_person_finishes_the_first_step():
    planner.current(NEW)
    knowledge.commit("people", {"id": "p_test", "owner_id": NEW, "name": "Meena", "relationship": "wife"}, NEW)
    plan = planner.refresh(NEW, {"kind": "keep", "ref": "p_test"})
    assert plan["current"] == "initial:story" and "initial:people" not in ids(plan)
    assert store.get("plan_versions", f"{NEW}:2")["cause"] == {"kind": "keep", "ref": "p_test"}


def test_an_initial_step_the_owner_declines_leaves_the_plan():
    planner.current(NEW)
    plan = planner.step_outcome(NEW, "initial:people", "declined")
    assert plan["current"] == "initial:story"
    with pytest.raises(ValueError):
        planner.step_outcome(NEW, "initial:story", "maybe")


def test_a_deflected_statement_puts_a_safe_route_step_first_for_this_conversation():
    planner.current(NEW)
    plan = planner.after_statements(NEW, "turn_x", [("gac_1", "deflect", "secret")])
    assert plan["current"] == "safe:secret:turn_x" and plan["steps"][0]["opens"] == "saathi"
    assert plan["cause"] == {"kind": "guardian", "ref": "gac_1", "turn": "turn_x"}
    later = planner.refresh(NEW, {"kind": "time"}, now=time.time() + planner.SAFE_STEP_SECONDS + 1)
    assert not [s for s in later["steps"] if s["source"] == "safe_route"]  # it belonged to that conversation


def test_a_blocked_statement_is_recorded_without_a_safe_step_and_an_allowed_one_changes_nothing():
    planner.current(NEW)
    assert planner.after_statements(NEW, "turn_y", [("gac_2", "continue", None)]) is None
    plan = planner.after_statements(NEW, "turn_y", [("gac_3", "block", None)])
    assert plan["cause"]["ref"] == "gac_3" and not [s for s in plan["steps"] if s["source"] == "safe_route"]


def test_a_step_the_step_guard_drops_never_joins_the_plan(monkeypatch):
    real = step_guard.check
    monkeypatch.setattr(step_guard, "check", lambda step, tactics: "secret" if "story" in step["title"] else real(step, tactics))
    plan = planner.current(NEW)
    assert "initial:story" not in ids(plan) and "initial:people" in ids(plan)


def test_unreadable_fraud_guides_leave_the_plan_as_it_was(monkeypatch):
    before = planner.current(NEW)
    def unavailable():
        raise step_guard.TacticsUnavailable
    monkeypatch.setattr(step_guard, "approved_tactics", unavailable)
    assert planner.refresh(NEW, {"kind": "time"})["version"] == before["version"]


def test_an_old_plan_is_worked_out_again_when_read():
    planner.current(NEW)
    assert planner.current(NEW, now=time.time() + planner.STALE_SECONDS + 1)["cause"] == {"kind": "time"}


def test_a_secret_said_in_the_chat_turns_the_plan_to_the_vault(client, monkeypatch):  # noqa: F811
    said = "My ATM PIN is 4471, write that down for my son."
    _chat(client, monkeypatch, said, _turn(moments=[Moment(kind="practical", title="ATM PIN", summary=said, quote=said)]))
    plan = store.get("conversation_plans", OWNER)
    assert plan["steps"][0]["source"] == "safe_route" and plan["steps"][0]["ref"] == "secret"
    assert "4471" not in str(store.list("plan_versions", owner_id=OWNER))


def test_the_talk_screen_reads_the_plan_and_a_keep_moves_it_on(client, monkeypatch):  # noqa: F811
    view = client.get(f"/api/owners/{OWNER}/plan").json()
    assert view["current"] is None or view["current"]["opens"] in ("saathi", "owner")
    said = "We went to Shimla in 1985 and it snowed all week."
    [card] = [c for c in _chat(client, monkeypatch, said, _turn(moments=[Moment(kind="story", title="Shimla",
                                                                                summary=said, quote=said)]))
              if c["collection"] == "moments"]
    assert client.post(f"/api/proposals/{card['id']}/decision",
                       json={"collection": "moments", "decision": "approve"}).status_code == 200
    assert store.get("conversation_plans", OWNER)["cause"] == {"kind": "keep", "ref": card["id"]}
