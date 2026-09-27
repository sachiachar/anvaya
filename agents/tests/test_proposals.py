"""Merging rules for AI suggestions, based on the gaps found in a real test run."""

import pytest
from fastapi.testclient import TestClient

from app import main, proposals
from app.store import store

OWNER = "owner_demo_vikram"
SALARY = {"kind": "story", "title": "My first salary", "people": [], "places": ["Mysore"], "year": "1987",
          "summary": "Vikram's first job was at a textile mill in Mysore in 1987; he gave his first salary to his mother.",
          "quote": "My first job was a textile mill in Mysore in 1987. I gave my whole first salary to my mother."}
PAPERS = {"kind": "practical", "title": "Location of important documents", "people": [], "places": [], "year": None,
          "summary": "Original papers are in the blue folder in the bedroom steel cupboard.",
          "quote": "All my original papers are in the blue folder in the steel cupboard in our bedroom."}


@pytest.fixture(autouse=True)
def fresh():
    TestClient(main.app).post("/api/demo/seed")


def test_a_repeat_of_a_kept_story_adds_no_card():
    first = proposals.save_moment(OWNER, SALARY, {})
    proposals.approve("moments", first)
    again = {**SALARY, "title": "First salary in Mysore"}  # what the model produced after "Yes"
    assert proposals.save_moment(OWNER, again, {}) is None
    assert len(store.list("moments", owner_id=OWNER)) == 1


def test_a_follow_up_detail_updates_the_pending_card():
    card = proposals.save_moment(OWNER, PAPERS, {})
    detail = {**PAPERS, "summary": PAPERS["summary"] + " Priya is responsible for them.", "quote": "Yes, that would be Priya."}
    merged = proposals.save_moment(OWNER, detail, {})
    assert merged["id"] == card["id"]
    assert "Priya" in merged["summary"] and "Yes, that would be Priya." in merged["quote"]


def test_a_follow_up_to_a_kept_moment_is_an_update_card():
    kept = proposals.approve("moments", proposals.save_moment(OWNER, PAPERS, {}))
    update = proposals.save_moment(OWNER, {**PAPERS, "summary": PAPERS["summary"] + " Meena has the key.",
                                           "quote": "Meena should have it."}, {})
    assert update["updates"] == kept["id"]
    proposals.approve("moments", update)
    moments = store.list("moments", owner_id=OWNER)
    assert len(moments) == 1 and "Meena has the key" in moments[0]["summary"]


def test_people_become_family_once_kept():
    card = proposals.save_person(OWNER, {"name": "Ajay", "relationship": "nephew"})
    assert proposals.save_person(OWNER, {"name": "Ajay", "relationship": "nephew"})["id"] == card["id"]
    proposals.approve("people", card)
    assert store.get("owners", OWNER)["family"] == ["Ajay (nephew)"]
    assert proposals.save_person(OWNER, {"name": "Ajay", "relationship": "nephew"}) is None


def test_a_follow_up_never_wipes_earlier_details():
    # From a real run: "she was too happy" replaced the Mysore / 1987 facts.
    kept = proposals.approve("moments", proposals.save_moment(OWNER, SALARY, {}))
    update = proposals.save_moment(OWNER, {**SALARY, "summary": "His mother was overjoyed when he gave her his first salary.",
                                           "quote": "Oh, she was too happy."}, {})
    proposals.approve("moments", update)
    summary = store.get("moments", kept["id"])["summary"]
    assert "Mysore" in summary and "1987" in summary and "overjoyed" in summary
