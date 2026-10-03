"""Removing something wrongly recorded: it and every earlier version leave the bundle, with their
history; the log keeps one line without content; links to it go; only the owner, and not during
a handover review or for the vault recipient."""

import pytest
from fastapi.testclient import TestClient

from app import knowledge, loops, main, media, proposals, release
from app.schemas import DocumentCheck
from app.store import store
from tests.people import PRIYA
from tests.test_knowledge import OWNER, POLICY, STORY, _keep, assert_conformant


@pytest.fixture()
def client(monkeypatch):
    async def fake_check(image, mime, expected, person, guide=None):
        return DocumentCheck(document_type=expected, is_expected_type=True, person_named=person,
                             matches_expected_person=True, confidence=0.9)

    monkeypatch.setattr(loops, "check_document", fake_check)
    monkeypatch.setattr(release.settings, "release_wait_seconds", 0)
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def bundle_text() -> str:
    return "\n".join((media.load(OWNER, n) or b"").decode() for n in media.names(OWNER, "kept/"))


def links(cid: str) -> dict:
    return knowledge.catalog(OWNER)["concepts"][cid]["graph"]["links"]


def update(fields: dict) -> dict:
    """Keep a new version of a kept account."""
    return proposals.approve("inventory", proposals.save_inventory(OWNER, fields, {}))


def test_a_removed_item_and_its_earlier_versions_leave_no_trace_but_one_line(client):
    first = _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Wrongname Person"})
    second = update({**POLICY, "nominee_or_beneficiary": "Another Wrongname"})
    assert set(knowledge.versions(OWNER, second["id"])) == {first["id"], second["id"]}
    r = client.delete(f"/api/owners/{OWNER}/kept/{second['id']}")
    assert r.status_code == 200 and r.json()["removed"] == 2
    assert "Wrongname" not in bundle_text() and "Term life" not in bundle_text()
    assert knowledge.items(OWNER, "inventory") == []
    assert "An item was removed by the owner" in (media.load(OWNER, "kept/log.md") or b"").decode()
    assert_conformant()
    assert knowledge.rebuild(OWNER)["concepts"].keys().isdisjoint({first["id"], second["id"]})


def test_removing_by_an_earlier_versions_id_removes_the_current_one(client):
    first = _keep("inventory", POLICY)
    second = update({**POLICY, "approximate_value": "10 lakh"})
    assert client.delete(f"/api/owners/{OWNER}/kept/{first['id']}").json()["removed"] == 2
    assert knowledge.current(OWNER, second["id"]) is None


def test_links_to_a_removed_person_go(client):
    ajay = proposals.approve("people", proposals.save_person(OWNER, {"name": "Ajay", "relationship": "son"}))
    policy = _keep("inventory", POLICY)  # its nominee is Ajay
    assert ajay["id"] in str(links(policy["id"]))
    client.delete(f"/api/owners/{OWNER}/kept/{ajay['id']}")
    assert ajay["id"] not in str(links(policy["id"]))


def test_only_the_owner_removes_and_never_during_a_review(client):
    story = _keep("moments", STORY)
    url = f"/api/owners/{OWNER}/kept/{story['id']}"
    assert client.delete(url, headers=PRIYA).status_code == 403
    files = {"certificate": ("c.jpg", b"img", "image/jpeg")}
    assert client.post(f"/api/owners/{OWNER}/death-report", files=files, headers=PRIYA).status_code == 200
    assert client.delete(url).status_code == 409
    assert knowledge.get(OWNER, story["id"])


def test_the_vault_recipient_cant_be_removed(client):
    priya = next(p for p in knowledge.items(OWNER, "people") if p["name"].startswith("Priya"))
    store.update("owners", OWNER, {"vault_recipient_name": "Priya"})
    assert client.delete(f"/api/owners/{OWNER}/kept/{priya['id']}").status_code == 409


def test_the_kept_list_shows_current_items_to_the_owner_only(client):
    _keep("moments", STORY)
    items = client.get(f"/api/owners/{OWNER}/kept").json()["items"]
    assert any(i["title"] == STORY["title"] for i in items)
    assert client.get(f"/api/owners/{OWNER}/kept", headers=PRIYA).status_code == 403
    assert client.delete(f"/api/owners/{OWNER}/kept/m_nothing").status_code == 404
