"""A reworded card that looks like a kept story or wish asks "Replace it" or "Keep both": the owner
decides, nothing merges silently, and Replace keeps it as a new version, not a second node."""

import pytest
from fastapi.testclient import TestClient

from app import knowledge, main, media, proposals
from tests.test_knowledge import OWNER, STORY, _keep

WISH = {**STORY, "kind": "wish", "title": "Transfer of SBI account", "people": ["my wife"],
        "summary": "He wants his SBI savings account to go to his wife.", "quote": "SBI should go to my wife."}
REWORDED = {**WISH, "title": "SBI account for my wife", "quote": "The SBI money is for my wife, Anjana.",
            "summary": "His SBI savings account should go to his wife Anjana when the time comes."}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def decide(client, card, **body):
    return client.post(f"/api/proposals/{card['id']}/decision",
                       json={"collection": "moments", "decision": "approve", **body})


def test_a_reworded_wish_asks_instead_of_adding_a_second_one(client):
    kept = _keep("moments", WISH)
    card = proposals.save_moment(OWNER, REWORDED, {})
    assert card["looks_like"] == {"id": kept["id"], "title": "Transfer of SBI account"} and not card.get("updates")
    r = decide(client, card, replace=True)
    assert r.status_code == 200
    wishes = knowledge.items(OWNER, "moments", kind="wish")
    assert [w["title"] for w in wishes] == ["SBI account for my wife"]
    assert knowledge.current(OWNER, kept["id"])["id"] == r.json()["id"]  # the old one is a replaced version
    assert "looks_like" not in "".join((media.load(OWNER, n) or b"").decode() for n in media.names(OWNER, "kept/"))


def test_keep_both_keeps_two(client):
    _keep("moments", WISH)
    card = proposals.save_moment(OWNER, REWORDED, {})
    assert decide(client, card).status_code == 200
    assert len(knowledge.items(OWNER, "moments", kind="wish")) == 2


def test_a_different_wish_doesnt_look_like_an_update(client):
    _keep("moments", WISH)
    card = proposals.save_moment(OWNER, {**STORY, "kind": "wish", "title": "Mysore house to Priya", "people": ["Priya"],
                                         "summary": "The Mysore house should go to Priya.", "quote": "House for Priya."}, {})
    assert "looks_like" not in card
    assert decide(client, card, replace=True).status_code == 409  # nothing to replace


def test_a_story_never_replaces_a_wish(client):
    _keep("moments", WISH)
    card = proposals.save_moment(OWNER, {**REWORDED, "kind": "story"}, {})
    assert "looks_like" not in card


def test_edits_cant_point_a_card_at_another_item(client):
    _keep("moments", WISH)
    other = _keep("moments", {**STORY, "title": "College days", "people": ["Animesh"],
                              "summary": "He ran the college magazine.", "quote": "We ran the magazine."})
    card = proposals.save_moment(OWNER, REWORDED, {})
    r = decide(client, card, replace=True, edits={"looks_like": {"id": other["id"]}, "updates": other["id"]})
    assert r.status_code == 200 and knowledge.current(OWNER, other["id"])["title"] == "College days"


def test_replace_needs_the_same_kind_and_a_refusal_changes_nothing(client):
    kept = _keep("moments", WISH)
    card = proposals.save_moment(OWNER, REWORDED, {})
    r = decide(client, card, replace=True, edits={"kind": "story", "title": "Edited title"})
    assert r.status_code == 409
    assert proposals.store.get("moments", card["id"])["title"] == "SBI account for my wife"
    assert knowledge.current(OWNER, kept["id"])["kind"] == "wish"
