"""The knowledge map (PR-7, demo): the owner's kept knowledge as a graph, filling in live."""

import pytest
from fastapi.testclient import TestClient

from app import knowledge_map, main, proposals
from tests.people import PRIYA
from tests.test_knowledge import OWNER, POLICY, STORY, _keep


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def graph():
    return knowledge_map.build(OWNER, "Vikram Rao")


def node(g, label):
    """Kept people are titled "Name (relationship)"."""
    return next(n for n in g["nodes"] if n["label"] == label or n["label"].startswith(f"{label} ("))


def linked(g, a, b, label):
    return any(e["from"] == a and e["to"] == b and e["label"] == label for e in g["edges"])


def test_a_kept_story_links_to_the_people_it_mentions(client):
    kept = _keep("moments", STORY)
    g = graph()
    amma = node(g, "Amma")
    assert amma["group"] == "mentioned"  # mentioned, not kept as a person yet
    assert linked(g, kept["id"], amma["id"], "mentions")
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Amma", "relationship": "mother"}))
    g = graph()
    amma = node(g, "Amma")
    assert amma["group"] == "person" and linked(g, kept["id"], amma["id"], "mentions")
    assert linked(g, amma["id"], "owner", "mother")
    assert not any(n["group"] == "mentioned" for n in g["nodes"])


def test_a_policy_links_to_its_nominee_and_institution_and_the_owner_is_its_holder(client):
    kept = _keep("inventory", POLICY)
    g = graph()
    assert linked(g, kept["id"], node(g, "Ajay")["id"], "nominee")
    assert linked(g, kept["id"], node(g, "LIC")["id"], "with") and node(g, "LIC")["group"] == "institution"
    assert not any(n["label"] == "Vikram Rao" and n["group"] == "mentioned" for n in g["nodes"])
    assert g["gaps"] == []


def test_a_policy_without_a_nominee_is_a_gap(client):
    kept = _keep("inventory", {**POLICY, "nominee_or_beneficiary": None})
    assert graph()["gaps"] == [{"node": kept["id"], "missing": "nominee"}]


def test_two_accounts_at_one_bank_share_its_node(client):
    a = _keep("inventory", POLICY)
    b = _keep("inventory", {**POLICY, "product": "Endowment plan", "account_hint": "••1234", "institution": "lic "})
    g = graph()
    lic = [n for n in g["nodes"] if n["group"] == "institution"]
    assert len(lic) == 1 and linked(g, a["id"], lic[0]["id"], "with") and linked(g, b["id"], lic[0]["id"], "with")


def test_recent_changes_come_newest_first(client):
    _keep("moments", STORY)
    _keep("inventory", POLICY)
    recent = graph()["recent"]
    assert recent[0]["title"] != recent[1]["title"] and recent[0]["at"] >= recent[1]["at"]


def test_only_the_owner_sees_the_map(client):
    _keep("moments", STORY)
    url = f"/api/owners/{OWNER}/knowledge-map"
    r = client.get(url)
    assert r.status_code == 200 and r.json()["counts"]["concepts"] >= 1
    assert client.get(url, headers=PRIYA).status_code == 403


def test_the_vault_never_appears(client):
    client.post(f"/api/owners/{OWNER}/vault/items", json={"label": "Netbanking password", "ciphertext": "x", "iv": "y"})
    assert "Netbanking" not in str(client.get(f"/api/owners/{OWNER}/knowledge-map").json())


def test_names_match_in_full_and_a_shared_first_name_doesnt_guess(client):
    for name in ("Ajay Kumar", "Ajay Singh"):
        proposals.approve("people", proposals.save_person(OWNER, {"name": name, "relationship": "nephew"}))
    kept = _keep("moments", {**STORY, "title": "Cricket at Lalbagh", "people": ["Ajay Singh", "Ajay", "Vikram Mehta"]})
    g = graph()
    singh = node(g, "Ajay Singh")
    assert linked(g, kept["id"], singh["id"], "mentions")
    assert not linked(g, kept["id"], node(g, "Ajay Kumar")["id"], "mentions")
    assert node(g, "Ajay")["group"] == "mentioned"  # two kept Ajays: no guess
    assert node(g, "Vikram Mehta")["group"] == "mentioned" and not linked(g, kept["id"], "owner", "mentions")


def test_each_link_is_drawn_once(client):
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Meena Rao", "relationship": "niece"}))
    _keep("moments", {**STORY, "title": "Meena's wedding", "people": ["Meena", "Meena Rao"]})
    g = graph()
    keys = [(e["from"], e["to"], e["label"]) for e in g["edges"]]
    assert len(keys) == len(set(keys)) == g["counts"]["links"]


def test_a_wish_about_an_account_links_to_it_and_to_the_person_by_relationship(client):
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Wife", "relationship": "wife"}))
    account = _keep("inventory", {**POLICY, "category": "bank_account", "institution": "SBI",
                                  "product": "Savings account", "account_hint": "••4808", "nominee_or_beneficiary": None})
    wish = _keep("moments", {**STORY, "kind": "wish", "title": "Transfer of SBI account", "people": ["my wife"],
                             "summary": "He wants his SBI savings account to go to his wife.",
                             "quote": "SBI account should go to my wife."})
    g = graph()
    wife = node(g, "Wife")
    assert linked(g, wish["id"], account["id"], "about")
    assert linked(g, wish["id"], wife["id"], "mentions")
    assert not any(n["group"] == "mentioned" for n in g["nodes"])  # "my wife" is the kept wife
    assert {"node": account["id"], "missing": "nominee"} in g["gaps"]  # a wish isn't a nominee


def test_a_story_links_to_people_its_text_names_even_without_a_people_list(client):
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Animesh Anjana", "relationship": "friend"}))
    story = _keep("moments", {**STORY, "title": "College days", "people": [],
                              "summary": "He and Animesh Anjana ran the college magazine together.",
                              "quote": "Animesh and I ran the magazine."})
    g = graph()
    assert linked(g, story["id"], node(g, "Animesh Anjana")["id"], "mentions")


def test_only_the_owners_own_relatives_are_his(client):
    for name, rel in (("Wife", "wife"), ("Amma", "mother")):
        proposals.approve("people", proposals.save_person(OWNER, {"name": name, "relationship": rel}))
    story = _keep("moments", {**STORY, "title": "Ajay's wedding", "people": ["Ajay's wife"],
                              "summary": "Ajay's wife came with my wife's mother to the wedding.",
                              "quote": "Ajay's wife came with my wife's mother."})
    g = graph()
    assert node(g, "Ajay's wife")["group"] == "mentioned"
    assert not linked(g, story["id"], node(g, "Amma")["id"], "mentions")  # not the owner's mother


def test_names_with_punctuation_match_in_text(client):
    proposals.approve("people", proposals.save_person(OWNER, {"name": "Dr. Mehta", "relationship": "doctor"}))
    acct = _keep("inventory", {**POLICY, "institution": "HDFC Bank Ltd.", "category": "bank_account",
                               "product": "Savings account", "account_hint": "••7777"})
    story = _keep("moments", {**STORY, "title": "Hospital visit", "people": [],
                              "summary": "Dr Mehta said the HDFC Bank Ltd account pays the hospital bills.",
                              "quote": "Dr Mehta told me."})
    g = graph()
    assert linked(g, story["id"], node(g, "Dr. Mehta")["id"], "mentions")
    assert linked(g, story["id"], acct["id"], "about")
