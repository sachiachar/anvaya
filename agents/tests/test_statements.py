"""Statements and wishes (PR-13.1): what the owner says comes back as
subject, predicate and object from a closed list in code; something they want makes the card a
wish; nothing is kept without Keep; a kept wish links to the people in it, and its graph part holds
only closed values, never what was said."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import knowledge, live, loops, main, proposals
from app.schemas import ConversationTurn, Extraction, Moment, PlanReply, Statement
from app.store import new_id
from tests.test_knowledge import OWNER

FLAT = {"kind": "story", "title": "The Mysore flat for Sunita", "people": ["Sunita"], "places": ["Mysore"],
        "summary": "He wants the Mysore flat to go to his wife Sunita.", "quote": "The Mysore flat should go to Sunita.",
        "statements": [{"subject": "owner", "predicate": "wants to give", "object": "the Mysore flat",
                        "recipient": "Sunita"}]}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    if card := proposals.save_person(OWNER, {"name": "Sunita Rao", "relationship": "wife"}):
        proposals.approve("people", card)
    return c


def _sunita() -> str:
    cat = knowledge.catalog(OWNER)
    return next(f"/{c['path']}" for c in cat["concepts"].values()
                if c["collection"] == "people" and c["record"]["name"].startswith("Sunita") and c["graph"]["status"] == "stable")


def test_predicates_come_from_a_closed_list_in_code():
    assert Statement(subject="owner", predicate="Wants to give", object="flat").predicate == "wants_to_give"
    made_up = Statement(subject="owner", predicate="dreams of", object="a trip to Kashi")
    assert made_up.predicate == "other" and made_up.words == "dreams of"  # kept as words, never acted on


def test_a_statement_of_intent_makes_the_card_a_wish_and_nothing_is_kept_without_keep(client):
    card = proposals.save_moment(OWNER, Moment(**FLAT).model_dump(), {})
    assert card["status"] == "pending" and card["kind"] == "wish"  # the model said story; code says wish
    assert card["statements"][0]["predicate"] == "wants_to_give"
    assert FLAT["title"] not in {m["title"] for m in knowledge.items(OWNER, "moments")}


def test_a_kept_wish_links_to_its_people_and_its_graph_part_holds_no_words(client):
    kept = proposals.approve("moments", proposals.save_moment(OWNER, Moment(**FLAT).model_dump(), {}))
    entry = knowledge.catalog(OWNER)["concepts"][kept["id"]]
    assert entry["record"]["wish_state"] == "kept"
    assert _sunita() in entry["graph"]["links"]["wants"]
    part = next(p for p in knowledge.graph_parts(OWNER) if p["path"] == f"/{entry['path']}")
    assert part["predicates"] == ["wants_to_give"] and part["wish_state"] == "kept"
    assert "Mysore" not in str(part) and "flat" not in str(part).lower()  # closed values only


def test_a_follow_up_adds_statements_once(client):
    proposals.save_moment(OWNER, Moment(**FLAT).model_dump(), {})
    more = {**FLAT, "summary": "Sunita should also know where the flat's papers are.",
            "quote": "Tell Sunita the papers are in the blue folder.",
            "statements": [*FLAT["statements"], {"subject": "owner", "predicate": "wants_family_to_know",
                                                  "object": "where the flat's papers are", "recipient": "Sunita"}]}
    card = proposals.save_moment(OWNER, Moment(**more).model_dump(), {})
    assert [s["predicate"] for s in card["statements"]] == ["wants_to_give", "wants_family_to_know"]


def test_typed_chat_and_live_calls_both_return_statements(client, monkeypatch):
    async def understand(*args):
        return ConversationTurn(heard="", owner_language="English", moments=[Moment(**FLAT)])

    async def respond(*args):
        return PlanReply(reply="That's a lovely thought, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    r = client.post(f"/api/owners/{OWNER}/conversation", data={"text": FLAT["quote"]})
    typed = r.json()["assistant_turn"]["proposals"][0]
    assert typed["kind"] == "wish" and typed["statements"][0]["recipient"] == "Sunita"

    async def extract(owner, heard, said, known):
        return Extraction(moments=[Moment(**{**FLAT, "title": "Kashi with Sunita", "summary": "He wants to visit Kashi "
                                             "with Sunita.", "quote": "I want to take Sunita to Kashi.",
                                             "statements": [{"subject": "owner", "predicate": "wants_to_do",
                                                             "object": "visit Kashi", "recipient": "Sunita"}]})])

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    sent = []

    class WS:
        async def send_json(self, data):
            sent.append(data)

    asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, "I want to take Sunita to Kashi.",
                                    "How lovely.", main._expand))
    spoken = sent[-1]["assistant_turn"]["proposals"][0]
    assert spoken["kind"] == "wish" and spoken["statements"][0]["predicate"] == "wants_to_do"
    kept = {m["title"] for m in knowledge.items(OWNER, "moments")}
    assert not kept & {FLAT["title"], "Kashi with Sunita"}  # still nothing kept without Keep


def test_a_follow_up_never_turns_a_wish_back_into_a_story(client):
    proposals.save_moment(OWNER, Moment(**FLAT).model_dump(), {})
    plain = {**FLAT, "kind": "story", "summary": "The flat has a mango tree.", "quote": "It has a mango tree.",
             "statements": [{"subject": "the Mysore flat", "predicate": "other", "object": "a mango tree",
                             "words": "has"}]}
    card = proposals.save_moment(OWNER, Moment(**plain).model_dump(), {})  # same title: merged
    assert card["kind"] == "wish" and [s["predicate"] for s in card["statements"]] == ["wants_to_give", "other"]
    again = proposals.save_moment(OWNER, Moment(**{**FLAT, "kind": "story", "statements": []}).model_dump(), {})
    assert again["kind"] == "wish" and again["statements"]  # a near-identical repeat keeps the wish too


def test_different_other_statements_are_both_kept(client):
    one = {**FLAT, "statements": [{"subject": "owner", "predicate": "other", "object": "Mysore", "words": "grew up in"}]}
    two = {**one, "summary": "He also taught at a school in Mysore.", "quote": "I taught in Mysore.",
           "statements": [{"subject": "owner", "predicate": "other", "object": "Mysore", "words": "taught in"}]}
    proposals.save_moment(OWNER, Moment(**one).model_dump(), {})
    card = proposals.save_moment(OWNER, Moment(**two).model_dump(), {})
    assert [s["words"] for s in card["statements"]] == ["grew up in", "taught in"]


def test_a_wish_kept_earlier_as_a_story_is_still_offered_as_replace_or_keep_both(client):
    old = proposals.approve("moments", proposals.save_moment(OWNER, {**FLAT, "statements": [],
                                                                     "title": "Flat for Sunita"}, {}))
    assert old["kind"] == "story"
    reworded = {**FLAT, "title": "Sunita gets the Mysore flat", "quote": "Sunita gets the flat in Mysore.",
                "summary": "Sunita should get the flat in Mysore."}
    card = proposals.save_moment(OWNER, Moment(**reworded).model_dump(), {})
    assert card["kind"] == "wish" and card["looks_like"]["id"] == old["id"]


def test_only_a_wish_has_a_wish_state(client):
    kept = proposals.approve("moments", proposals.save_moment(OWNER, Moment(**FLAT).model_dump(), {}))
    story = knowledge.commit("moments", {**kept, "id": new_id("m"), "kind": "story", "statements": []}, OWNER,
                             replaces=kept["id"])
    entry = knowledge.catalog(OWNER)["concepts"][story["id"]]
    assert "wish_state" not in entry["record"] and "wish_state" not in entry["graph"]
