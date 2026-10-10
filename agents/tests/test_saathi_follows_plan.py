"""Saathi carries out the plan (PR-2.12.3): each step has a tone, the owner's mood (reported by the
reader of each turn) changes the plan in code, typed chat replies only after the guardian and the
plan have seen the turn, and a call reads and reports steps through two gateway-checked tools.
All data here is synthetic."""

import asyncio
import time
import uuid

import pytest
from app import live, loops, main, planner
from app.governance import core
from app.schemas import ConversationTurn, Extraction, Moment, PlanReply
from app.store import store
from tests.test_grounding import OWNER, client  # noqa: F401  (the app with a seeded demo)

NEW = ""


@pytest.fixture(autouse=True)
def fresh():
    global NEW
    for doc in store.list("conversation_plans"):
        store.delete("conversation_plans", doc["id"])
    NEW = f"owner_test_{uuid.uuid4().hex[:8]}"
    store.put("owners", {"id": NEW, "name": "Test Owner"})


def steps(plan: dict) -> list[tuple[str, str]]:
    return [(s["id"], s["tone"]) for s in plan["steps"]]


# ---- Tone and mood, decided by the planner -------------------------------------------------


def test_each_step_carries_its_tone_in_words():
    view = planner.view(planner.current(NEW))
    assert view["current"]["tone"] == "warm" and view["current"]["tone_words"] == planner.TONE_WORDS["warm"]
    assert dict(steps(planner.current(NEW)))["initial:money"] == "clear"


def test_sad_puts_a_listening_step_first_and_holds_back_practical_steps():
    planner.current(NEW)
    plan = planner.after_turn(NEW, "t1", [], mood="sad")
    assert plan["steps"][0]["source"] == "mood" and plan["steps"][0]["tone"] == "gentle"
    assert "initial:money" not in dict(steps(plan))  # practical, so it waits for another conversation
    assert all(tone == "gentle" for _, tone in steps(plan)) and plan["cause"]["mood"] == "sad"
    calm = planner.after_turn(NEW, "t2", [], mood="calm")
    assert calm["steps"][0]["id"] == "initial:people" and "initial:money" in dict(steps(calm))


def test_tired_offers_to_stop_and_makes_every_step_patient():
    planner.current(NEW)
    plan = planner.after_turn(NEW, "t1", [], mood="tired")
    assert plan["steps"][0]["topic"] == "A rest" and {tone for _, tone in steps(plan)} == {"patient"}


def test_happy_makes_a_warm_step_light():
    planner.current(NEW)
    plan = planner.after_turn(NEW, "t1", [], mood="happy")
    assert dict(steps(plan))["initial:people"] == "light" and dict(steps(plan))["initial:money"] == "clear"


def test_the_same_mood_again_adds_no_version_and_a_mood_ends_with_the_conversation():
    planner.current(NEW)
    first = planner.after_turn(NEW, "t1", [], mood="sad")
    assert planner.after_turn(NEW, "t2", [], mood="sad")["version"] == first["version"]
    later = planner.refresh(NEW, {"kind": "time"}, now=time.time() + planner.SAFE_STEP_SECONDS + 1)
    assert later["steps"][0]["id"] == "initial:people"


def test_an_answered_safe_route_step_leaves_the_plan():
    planner.current(NEW)
    plan = planner.after_turn(NEW, "t1", [("gac_1", "deflect", "secret")])
    safe = plan["steps"][0]
    assert safe["source"] == "safe_route" and safe["tone"] == "reassuring"
    after = planner.after_turn(NEW, "t2", [], answered_step=safe["id"], outcome="answered")
    assert safe["id"] not in dict(steps(after)) and after["cause"]["outcome"] == "answered"


# ---- Typed chat: the guardian and the plan first, then the reply ---------------------------------


def chat(client, monkeypatch, text: str, reading: ConversationTurn) -> dict:  # noqa: F811
    given = {}

    async def understand(*args):
        return reading

    async def respond(owner, step, last_said, said, known):
        given["step"] = step
        return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    assert client.post(f"/api/owners/{OWNER}/conversation", data={"text": text}).status_code == 200
    return given["step"]


def test_saathi_replies_from_the_step_the_mood_chose(client, monkeypatch):  # noqa: F811
    step = chat(client, monkeypatch, "I miss my brother so much.", ConversationTurn(heard="", owner_language="English",
                                                                                  owner_mood="sad"))
    assert step["source"] == "mood" and step["tone"] == "gentle"
    assert store.get("owners", OWNER)["last_said"]["step"] == step["id"]


def test_saathi_replies_from_the_safe_route_after_a_deflection(client, monkeypatch):  # noqa: F811
    said = "My ATM PIN is 4471, write that down for my son."
    reading = ConversationTurn(heard="", owner_language="English",
                               moments=[Moment(kind="practical", title="ATM PIN", summary=said, quote=said)])
    step = chat(client, monkeypatch, said, reading)
    assert step["source"] == "safe_route" and step["tone"] == "reassuring" and "4471" not in step["ask"]


def test_talk_about_this_remembers_the_plan_step_it_opens(client):  # noqa: F811
    current = planner.view(planner.current(OWNER))["current"]
    client.post(f"/api/owners/{OWNER}/conversation/prompt", json={"text": current["ask"], "step_id": current["id"]})
    assert store.get("owners", OWNER)["last_said"]["step"] == current["id"]
    client.post(f"/api/owners/{OWNER}/conversation/prompt", json={"text": "Hello", "step_id": "initial:made_up"})
    assert store.get("owners", OWNER)["last_said"]["step"] is None


# ---- A call: the plan tools, checked by the gateway -------------------------------------------


def test_the_voice_policy_grants_the_plan_tools():
    granted = {g["tool"]: g["scope"] for g in core.policy("saathi.voice")["tools"]}
    assert granted == {"current_step": "read", "report_step": "write", "voice_joined": "write"}
    assert [f.name for f in live.PLAN_TOOLS[0].function_declarations] == ["current_step", "report_step",
                                                                           "voice_joined"]


def test_current_step_gives_the_step_and_report_step_moves_the_plan():
    state = {"step": None, "correct": None}
    out = live._plan_tool(NEW, "current_step", {}, state)
    assert out["step"]["id"] == "initial:people" and state["step"] == "initial:people"
    assert live._plan_tool(NEW, "report_step", {"outcome": "declined"}, state) == {"ok": True}
    assert planner.current(NEW)["current"] == "initial:story"
    assert live._plan_tool(NEW, "report_step", {"outcome": "whatever"}, state) == {"ok": False}


def test_a_tool_the_policy_does_not_grant_is_refused(monkeypatch):
    def refuse(agent, tool):
        raise core.Refused("no")
    monkeypatch.setattr(core, "check_tool", refuse)
    assert "error" in live._plan_tool(NEW, "current_step", {}, {"step": None, "correct": None})


def test_a_spoken_reply_that_breaks_a_rule_is_recorded_and_corrected_once(client, monkeypatch):  # noqa: F811
    async def extract(owner, heard, said, known):
        return Extraction()

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    state = {"step": None, "correct": None}
    before = len(store.list("guardian_events"))
    asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, "My LIC policy ends 1234.",
                                    "I've noted that, Vikram ji.", main._expand, state))
    assert state["correct"] and len(store.list("guardian_events")) == before + 1
    first = live._plan_tool(OWNER, "current_step", {}, state)
    assert first["correct"] and "correct" not in live._plan_tool(OWNER, "current_step", {}, state)
