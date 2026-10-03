"""Switching people inside a demo copy (PR-1.3, PR-1.4): the server knows who is acting, each
person can only do their own part, and the two-reviewer rule compares real identities even
though one judge plays every reviewer."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import loops, main, release
from app.schemas import DocumentCheck
from app.store import store
from tests.people import ANJALI, PRIYA, RAHUL, VIKRAM, acting

OWNER = "owner_demo_vikram"
IMAGE = {"certificate": ("c.jpg", b"img", "image/jpeg")}
PROOF = {"proof": ("p.jpg", b"img", "image/jpeg")}


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


def _report(client) -> dict:
    return client.post(f"/api/owners/{OWNER}/death-report", files=IMAGE, headers=PRIYA).json()["case"]


def test_the_switcher_lists_the_people_and_the_server_knows_who_acts(client):
    me = client.get("/api/me", headers=PRIYA).json()
    assert [p["id"] for p in me["people"]] == ["vikram", "priya", "anjali", "rahul", "admin"]
    assert me["acting_as"] == "priya"
    assert client.get("/api/me").json()["acting_as"] == "vikram"  # the owner by default
    assert client.get("/api/me", headers=acting("mallory")).status_code == 400


def test_only_vikram_changes_his_vault(client):
    invite = {"name": "Priya", "relationship": "daughter"}
    for person in (PRIYA, ANJALI, RAHUL, acting("admin")):
        assert client.post(f"/api/owners/{OWNER}/vault/invite", json=invite, headers=person).status_code == 403
        assert client.post(f"/api/owners/{OWNER}/vault/items", headers=person,
                           json={"category": "note", "iv": "aXY=", "ciphertext": "Y3Q="}).status_code == 403
        assert client.post(f"/api/owners/{OWNER}/alive", headers=person).status_code == 403
    assert client.post(f"/api/owners/{OWNER}/vault/invite", json=invite, headers=VIKRAM).status_code == 200


def test_only_priya_reports_and_only_reviewers_confirm(client):
    assert client.post(f"/api/owners/{OWNER}/death-report", files=IMAGE, headers=VIKRAM).status_code == 403
    case = _report(client)
    assert case["reported_by"] == "Priya"
    for person in (VIKRAM, PRIYA, acting("admin")):
        r = client.post(f"/api/admin/cases/{case['id']}/confirm-death", json={}, headers=person)
        assert r.status_code == 403, r.json()
    assert store.get("cases", case["id"])["state"] == "reported"


def test_one_reviewer_cannot_approve_both_steps(client):
    case = _report(client)
    # A typed name is ignored: the reviewer is the person acting.
    r = client.post(f"/api/admin/cases/{case['id']}/confirm-death", json={"reviewer": "Rahul"}, headers=ANJALI)
    assert r.status_code == 200 and r.json()["case"]["death_confirmed_by"] == "Anjali"
    client.post(f"/api/owners/{OWNER}/release/authority", files=PROOF, headers=PRIYA)
    same = client.post(f"/api/admin/cases/{case['id']}/confirm-authority", json={"reviewer": "Someone else"},
                       headers=ANJALI)
    assert same.status_code == 409 and "different reviewer" in same.json()["detail"]
    other = client.post(f"/api/admin/cases/{case['id']}/confirm-authority", json={}, headers=RAHUL)
    assert other.status_code == 200


def test_only_priya_opens_the_vault_and_asks_after_release(client):
    for person in (VIKRAM, ANJALI):
        assert client.get(f"/api/owners/{OWNER}/vault/release-bundle", headers=person).status_code == 403
        assert client.post(f"/api/owners/{OWNER}/ask", json={"question": "?"}, headers=person).status_code == 403


def test_access_decisions_follow_the_person(client):
    arjun = {"name": "Arjun Rao", "relationship": "son", "contact": "+91 90000 00000", "note": ""}
    client.post(f"/api/owners/{OWNER}/access-requests", json=arjun, headers=PRIYA)
    req = client.get(f"/api/owners/{OWNER}/access-requests").json()["requests"][0]
    as_team = client.post(f"/api/access-requests/{req['id']}/decision", headers=VIKRAM,
                          json={"decision": "approve", "decided_by": "team"})
    assert as_team.status_code == 403  # Vikram can't decide as the team
    as_owner = client.post(f"/api/access-requests/{req['id']}/decision", headers=ANJALI,
                           json={"decision": "approve", "decided_by": "owner"})
    assert as_owner.status_code == 403  # nor a reviewer as Vikram


def test_only_vikram_calls_saathi(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/api/owners/{OWNER}/live?acting_as=priya") as ws:
            ws.receive_text()
    assert closed.value.code == 4403


def test_the_two_reviewer_rule_ignores_capitals():
    # A case confirmed before this change could hold a typed "anjali".
    case = {"id": "c", "owner_id": OWNER, "state": "death_confirmed", "death_confirmed_by": "anjali ",
            "authority_check": {"verdict": "likely_valid"}, "history": []}
    with pytest.raises(release.ReleaseError):
        release.confirm_authority(case, "Anjali")
