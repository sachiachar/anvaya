"""Gaps and conflicts drive the conversation (PR-7.10): fixed rules find what's missing, ranked by
harm; Saathi gets the top two; a skipped gap never comes back; a nominee is a fact, not a wish."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gaps, knowledge, loops, main, proposals
from app.schemas import ConversationTurn
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


def test_an_account_without_a_nominee_is_a_gap_and_insurance_comes_first(client):
    bank = _keep("inventory", SBI)
    policy = _keep("inventory", {**POLICY, "nominee_or_beneficiary": None})
    found = gaps.find(OWNER)
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


def test_saathi_gets_the_top_two_gaps(client):
    _keep("inventory", SBI)
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": None})
    _keep("inventory", {**SBI, "institution": "HDFC", "account_hint": "••1111"})
    known = proposals.known(OWNER)
    assert len(known["open_gaps"]) == 2 and "LIC" in known["open_gaps"][0]
    assert "SBI Savings account ••4808 (nominee: not recorded)" in known["inventory"]


def test_the_conversation_is_told_about_gaps_and_the_nominee_rule(monkeypatch):
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return ConversationTurn(heard="", owner_language="English", reply="Thank you, Vikram ji.",
                                reply_language="en-IN")

    monkeypatch.setattr(loops, "generate_json", fake)
    owner = {"name": "Vikram Rao", "city": "Bengaluru", "country": "India", "family": []}
    known = {"open_gaps": ["SBI Savings account ••4808 has no nominee recorded."]}
    asyncio.run(loops.converse(owner, [], "My wife is the nominee for SBI", None, known))
    assert "SBI Savings account ••4808 has no nominee recorded." in seen["prompt"]
    assert "at most ONE" in seen["system"] and "IS, that is a fact" in seen["system"]
    assert "open_gaps" in loops.live_instructions(owner, known)


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
    key = gaps.find(OWNER)[0]["key"]
    r = client.post(f"/api/owners/{OWNER}/gaps/{key}/skip")
    assert r.status_code == 200 and r.json()["gaps"] == []
    assert proposals.known(OWNER)["open_gaps"] == []
    assert client.post(f"/api/owners/{OWNER}/gaps/nominee:nope/skip").status_code == 404
    assert client.get(f"/api/owners/{OWNER}/gaps", headers=PRIYA).status_code == 403


def test_a_skip_holds_after_the_account_is_updated(client):
    kept = _keep("inventory", SBI)
    gaps.skip(OWNER, gaps.find(OWNER)[0]["key"])
    card = proposals.save_inventory(OWNER, {**SBI, "approximate_value": "2 lakh"}, {})
    assert card["updates"] == kept["id"]
    proposals.approve("inventory", card)
    assert gaps.find(OWNER) == []


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
