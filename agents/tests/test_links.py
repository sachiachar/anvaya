"""Typed links between kept items (PR-7.3, PR-7.9): worked out from what cards say, against the
people the owner kept; written as frontmatter fields and as ordinary Markdown links; and a graph
part a fixed-code reader can use without ever seeing names or text."""

import json

import pytest
from fastapi.testclient import TestClient

from app import knowledge, main, media, proposals
from app.store import new_id

OWNER = "owner_demo_vikram"
POLICY = {"category": "insurance", "institution": "LIC", "product": "Term life policy", "account_hint": "••4821",
          "holder_name": "Vikram Rao", "nominee_or_beneficiary": "Ajay", "approximate_value": None,
          "missing_fields": [], "papers_at": "blue folder, bedroom cupboard"}
FLAT = {"category": "property", "institution": "Self", "product": "Flat in Jayanagar", "account_hint": "••0000",
        "intended_for": "Meena", "missing_fields": []}
STORY = {"kind": "story", "title": "Priya's first day at school", "people": ["Priya"], "places": [], "year": "1998",
         "summary": "Vikram walked Priya to her first day at school.", "quote": "I walked her there myself."}


@pytest.fixture(autouse=True)
def fresh():
    TestClient(main.app).post("/api/demo/seed")


def _keep(collection: str, fields: dict) -> dict:
    save = {"moments": proposals.save_moment, "inventory": proposals.save_inventory,
            "people": proposals.save_person}[collection]
    return proposals.approve(collection, save(OWNER, fields, {}) if collection != "people" else save(OWNER, fields))


def _person(name: str, relationship: str) -> dict:
    return _keep("people", {"name": name, "relationship": relationship})


def _front(item: dict) -> tuple[dict, str]:
    return knowledge.parse(media.load(OWNER, f"kept/{item['concept']}").decode())


def test_a_story_links_to_the_people_it_mentions():
    story = _keep("moments", STORY)
    priya = f"/{knowledge.items(OWNER, 'people')[0]['concept']}"
    front, body = _front(story)
    assert front["links"] == {"people": [priya]}
    assert f"- Mentions: [Priya Rao (daughter)]({priya})" in body  # an ordinary Markdown link, for any OKF reader


def test_a_policy_links_to_its_nominee_once_that_person_is_kept():
    policy = _keep("inventory", POLICY)
    assert _front(policy)[0]["links"] == {}  # Ajay isn't kept yet: the name stays on the card, unlinked
    ajay = _person("Ajay Rao", "nephew")
    front, body = _front(knowledge.current(OWNER, policy["id"]))
    assert front["links"] == {"nominee": [f"/{ajay['concept']}"]} and front["status"] == "stable"
    assert "Nominee: [Ajay Rao (nephew)]" in body
    assert knowledge.catalog(OWNER)["concepts"][policy["id"]]["graph"]["links"]["nominee"] == [f"/{ajay['concept']}"]


def test_property_and_wishes_link_to_who_they_are_meant_for():
    meena = _person("Meena Rao", "wife")
    flat = _keep("inventory", FLAT)
    wish = _keep("moments", {**STORY, "kind": "wish", "title": "The flat", "people": [], "intended_for": ["Meena"],
                             "summary": "The flat should go to Meena.", "quote": "Give the flat to Meena."})
    assert _front(flat)[0]["links"] == {"intended_for": [f"/{meena['concept']}"]}
    assert _front(wish)[0]["links"] == {"intended_for": [f"/{meena['concept']}"]}


def test_an_ambiguous_first_name_is_not_linked():
    _person("Ajay Rao", "nephew")
    _person("Ajay Kumar", "friend")
    policy = _keep("inventory", POLICY)
    assert _front(policy)[0]["links"] == {}  # two kept Ajays: we don't guess
    full = _keep("inventory", {**POLICY, "account_hint": "••1111", "nominee_or_beneficiary": "Ajay Kumar"})
    assert len(_front(full)[0]["links"]["nominee"]) == 1


def test_several_names_link_to_several_people():
    ajay, meena = _person("Ajay Rao", "nephew"), _person("Meena Rao", "wife")
    policy = _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Ajay Rao and Meena Rao"})
    assert _front(policy)[0]["links"]["nominee"] == sorted([f"/{ajay['concept']}", f"/{meena['concept']}"])


def test_the_graph_part_holds_no_names_or_text():
    _person("Ajay Rao", "nephew")
    policy = _keep("inventory", POLICY)
    graph = knowledge.catalog(OWNER)["concepts"][policy["id"]]["graph"]
    assert graph["has"] == ["nominee_or_beneficiary", "holder_name", "papers_at"]  # presence, never values
    text = json.dumps(graph)
    for private in ("Ajay", "Vikram", "LIC", "4821", "blue folder"):
        assert private not in text, private


def test_links_follow_a_person_when_they_are_replaced():
    ajay = _person("Ajay Rao", "nephew")
    policy = _keep("inventory", POLICY)
    update = proposals.save_person(OWNER, {"name": "Ajay Rao", "relationship": "son"})
    # save_person won't make a second card for a kept person; replace by a new version directly.
    assert update is None
    newer = knowledge.commit("people", {"id": new_id("p"), "name": "Ajay Rao", "relationship": "son"}, OWNER,
                             replaces=ajay["id"])
    assert _front(knowledge.current(OWNER, policy["id"]))[0]["links"]["nominee"] == [f"/{newer['concept']}"]


def test_the_family_assistant_sees_the_links():
    _person("Ajay Rao", "nephew")
    _keep("inventory", POLICY)
    reading = {c["type"]: c["text"] for c in knowledge.for_reading(OWNER)}
    assert "Nominee: [Ajay Rao (nephew)]" in reading["Insurance Policy"]
    assert "Papers kept at:** blue folder, bedroom cupboard" in reading["Insurance Policy"]


def test_the_rebuilt_catalog_keeps_the_links():
    _person("Ajay Rao", "nephew")
    _keep("inventory", POLICY)
    before = knowledge.catalog(OWNER)["concepts"]
    media.delete(OWNER, "kept/.catalog.json")
    assert knowledge.rebuild(OWNER)["concepts"] == before


def test_the_knowledge_map_draws_the_typed_links_once():
    from app import knowledge_map

    meena = _person("Meena Rao", "wife")
    flat = _keep("inventory", FLAT)
    story = _keep("moments", STORY)
    edges = knowledge_map.build(OWNER, "Vikram Rao")["edges"]
    assert {"from": flat["id"], "to": meena["id"], "label": "meant for", "derived": False} in edges
    mentions = [e for e in edges if e["from"] == story["id"] and e["label"] == "mentions"]
    assert len(mentions) == 1 and mentions[0]["derived"] is False


def test_relinking_never_brings_back_a_replaced_version(monkeypatch):
    policy = _keep("inventory", POLICY)
    real_save_if = media.save_if

    def replace_meanwhile(owner, name, data, mime, version):
        # Another request replaces the policy while relinking runs.
        if name.endswith(policy["concept"]) and not knowledge.get(OWNER, policy["id"])["okf_status"] == "deprecated":
            update = {**POLICY, "id": new_id("inv"), "approximate_value": "10 lakh"}
            monkeypatch.setattr(media, "save_if", real_save_if)
            knowledge.commit("inventory", update, OWNER, replaces=policy["id"])
        return real_save_if(owner, name, data, mime, version)

    monkeypatch.setattr(media, "save_if", replace_meanwhile)
    _person("Ajay Rao", "nephew")
    current = knowledge.items(OWNER, "inventory")
    assert len(current) == 1 and current[0]["approximate_value"] == "10 lakh"
    assert knowledge.get(OWNER, policy["id"])["okf_status"] == "deprecated"
