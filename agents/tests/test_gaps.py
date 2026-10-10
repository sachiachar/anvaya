"""Gaps and conflicts drive the conversation (PR-7.10): fixed rules find what's missing, ranked by
harm; Saathi gets the top two; a skipped gap never comes back; a nominee is a fact, not a wish."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gaps, knowledge, loops, main, planner, proposals
from app.schemas import ConversationTurn, PlanReply
from app.store import store
from tests.people import PRIYA
from tests.test_knowledge import OWNER, POLICY, STORY, _keep

SBI = {**POLICY, "category": "bank_account", "institution": "SBI", "product": "Savings account",
       "account_hint": "••4808", "nominee_or_beneficiary": None}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def keys():
    return [g["key"].split(":")[0] for g in gaps.find(OWNER)]


def of(kind: str, found: list[dict] | None = None) -> list[dict]:
    return [g for g in (gaps.find(OWNER) if found is None else found) if g["kind"] == kind]


def test_an_account_without_a_nominee_is_a_gap_and_insurance_comes_first(client):
    bank = _keep("inventory", SBI)
    policy = _keep("inventory", {**POLICY, "nominee_or_beneficiary": None})
    found = of("nominee")
    assert [g["about"] for g in found[:2]] == [policy["id"], bank["id"]]
    assert found[1]["text"] == "SBI Savings account ••4808 has no nominee recorded."


def test_a_wish_against_the_nominee_is_a_conflict(client):
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Ajay"})
    _keep("moments", {**STORY, "kind": "wish", "title": "LIC money for Meena", "people": ["Meena"],
                      "summary": "He wants the LIC policy money to go to his niece Meena.", "quote": "LIC for Meena."})
    found = gaps.find(OWNER)
    assert found[0]["kind"] == "conflict" and "Meena" in found[0]["text"] and "Ajay" in found[0]["text"]


def test_a_wish_that_agrees_with_the_nominee_by_relationship_is_no_conflict(client):
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Sunita", "relationship": "wife"}))
    _keep("inventory", {**SBI, "nominee_or_beneficiary": "Sunita"})
    _keep("moments", {**STORY, "kind": "wish", "title": "SBI account to my wife", "people": ["my wife"],
                      "summary": "He wants the SBI account to go to his wife.", "quote": "SBI to my wife."})
    assert "conflict" not in keys()


def test_property_without_an_heir_and_a_person_without_a_relationship(client):
    _keep("inventory", {**POLICY, "category": "property", "institution": "Jayanagar flat", "product": "Flat",
                        "account_hint": "", "nominee_or_beneficiary": None})
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Raghu", "relationship": "unknown"}))
    assert {"heir", "relationship"} <= set(keys())
    _keep("moments", {**STORY, "kind": "wish", "title": "Jayanagar flat to Priya", "people": ["Priya"],
                      "summary": "The Jayanagar flat should go to Priya.", "quote": "The flat is for Priya."})
    assert "heir" not in keys()


def test_gaps_feed_the_plan_and_saathi_never_sees_them(client):
    _keep("inventory", SBI)
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": None})
    known = proposals.known(OWNER)
    assert "open_gaps" not in known and "SBI Savings account ••4808 (nominee: not recorded)" in known["inventory"]
    for doc in store.list("conversation_plans"):
        store.delete("conversation_plans", doc["id"])
    gap_steps = [s for s in planner.current(OWNER)["steps"] if s["source"] == "gap"]
    assert gap_steps and all(s["tone"] in ("gentle", "clear") for s in gap_steps)


def test_saathi_is_given_the_plan_step_and_its_tone_never_the_gaps(monkeypatch):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        if kw["schema"] is PlanReply:
            return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")
        return ConversationTurn(heard="", owner_language="English")

    monkeypatch.setattr(loops, "generate_json", fake)
    owner = {"name": "Vikram Rao", "city": "Bengaluru", "country": "India", "family": []}
    asyncio.run(loops.understand(owner, None, "My wife is the nominee for SBI", None, {}))
    assert "IS, that is a fact" in seen[0]["system"] and seen[0]["agent"] == "capture.extractor"
    step = {"topic": "A nominee for SBI", "ask": "Gently ask who the nominee is.", "opens": "saathi", "tone": "gentle",
            "tone_words": planner.TONE_WORDS["gentle"]}
    asyncio.run(loops.respond(owner, step, None, "Hello", {}))
    assert "Gently ask who the nominee is." in seen[1]["prompt"] and planner.TONE_WORDS["gentle"] in seen[1]["prompt"]
    assert "never choose a topic" in seen[1]["system"] and "open_gaps" not in seen[1]["system"]
    assert seen[1]["agent"] == "saathi.chat"
    assert "current_step" in loops.live_instructions(owner, {}) and "open_gaps" not in loops.live_instructions(owner, {})


def test_naming_the_nominee_with_just_the_bank_updates_that_account(client):
    kept = _keep("inventory", SBI)
    card = proposals.save_inventory(OWNER, {"category": "bank_account", "institution": "SBI", "product": "",
                                            "account_hint": "", "nominee_or_beneficiary": "my wife"}, {})
    assert card["updates"] == kept["id"] and card["account_hint"] == "••4808"
    new = proposals.approve("inventory", card)
    assert new["nominee_or_beneficiary"] == "my wife" and new["product"] == "Savings account"
    assert "nominee" not in keys()


def test_a_skipped_gap_never_comes_back(client):
    _keep("inventory", SBI)
    key = of("nominee")[0]["key"]
    r = client.post(f"/api/owners/{OWNER}/gaps/{key}/skip")
    assert r.status_code == 200 and of("nominee", r.json()["gaps"]) == []
    assert f"gap:{key}" not in [s["id"] for s in planner.refresh(OWNER, {"kind": "test"})["steps"]]
    assert client.post(f"/api/owners/{OWNER}/gaps/nominee:nope/skip").status_code == 404
    assert client.get(f"/api/owners/{OWNER}/gaps", headers=PRIYA).status_code == 403


def test_a_skip_holds_after_the_account_is_updated(client):
    kept = _keep("inventory", SBI)
    gaps.skip(OWNER, of("nominee")[0]["key"])
    card = proposals.save_inventory(OWNER, {**SBI, "approximate_value": "2 lakh"}, {})
    assert card["updates"] == kept["id"]
    proposals.approve("inventory", card)
    assert of("nominee") == []


def test_a_different_kind_of_account_at_the_same_bank_is_a_new_card(client):
    _keep("inventory", SBI)
    card = proposals.save_inventory(OWNER, {"category": "investment", "institution": "SBI", "product": "Fixed deposit",
                                            "account_hint": "", "nominee_or_beneficiary": "my wife"}, {})
    assert not card.get("updates")


def test_with_two_accounts_at_one_bank_a_bank_only_update_doesnt_guess(client):
    proposals.save_inventory(OWNER, SBI, {})
    proposals.save_inventory(OWNER, {**SBI, "account_hint": "••1234"}, {})
    card = proposals.save_inventory(OWNER, {"category": "bank_account", "institution": "SBI", "product": "",
                                            "account_hint": "", "nominee_or_beneficiary": "my wife"}, {})
    assert not card.get("updates") and card["account_hint"] == ""


def test_a_relationship_nobody_has_yet_is_no_conflict(client):
    _keep("inventory", {**SBI, "nominee_or_beneficiary": "Anjana"})
    _keep("moments", {**STORY, "kind": "wish", "title": "SBI account to my wife", "people": ["my wife"],
                      "summary": "He wants the SBI account to go to his wife.", "quote": "SBI to my wife."})
    assert "conflict" not in keys()


def test_a_person_saved_by_relationship_is_renamed_not_duplicated(client):
    wife = proposals.approve("people", proposals.save_person(OWNER, {"name": "Wife", "relationship": "wife"}))
    card = proposals.save_person(OWNER, {"name": "Anjana", "relationship": "wife"})
    assert card["updates"] == wife["id"]
    proposals.approve("people", card)
    wives = [p for p in knowledge.items(OWNER, "people") if p["relationship"] == "wife"]
    assert [p["name"] for p in wives] == ["Anjana"]
    assert proposals.save_person(OWNER, {"name": "Wife", "relationship": "wife"}) is None
