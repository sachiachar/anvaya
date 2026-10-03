import pytest
from fastapi.testclient import TestClient

from app import loops, main, release
from app.schemas import DocumentCheck
from tests.people import ANJALI, PRIYA

OWNER = "owner_demo_vikram"
ARJUN = {"name": "Arjun Rao", "relationship": "son", "contact": "+91 90000 00000", "note": "I'm his son"}


@pytest.fixture()
def client(monkeypatch):
    async def fake_check(image, mime, expected, person):
        return DocumentCheck(document_type=expected, is_expected_type=True, person_named=person,
                             matches_expected_person=True, confidence=0.9)

    monkeypatch.setattr(loops, "check_document", fake_check)
    monkeypatch.setattr(release.settings, "release_wait_seconds", 0)
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    return c


def test_reply_never_reveals_whether_the_owner_exists(client):
    known = client.post(f"/api/owners/{OWNER}/access-requests", json=ARJUN, headers=PRIYA).json()
    unknown = client.post("/api/owners/owner_nobody/access-requests", json=ARJUN, headers=PRIYA).json()
    assert known == unknown


def test_while_alive_only_the_owner_decides(client):
    client.post(f"/api/owners/{OWNER}/access-requests", json=ARJUN, headers=PRIYA)
    req = client.get(f"/api/owners/{OWNER}/access-requests").json()["requests"][0]
    assert client.get("/api/admin/access-requests").json()["requests"] == []
    # The team cannot approve a request that belongs to a living owner.
    assert client.post(f"/api/access-requests/{req['id']}/decision",
                       json={"decision": "approve", "decided_by": "team"}, headers=ANJALI).status_code == 404
    client.post(f"/api/access-requests/{req['id']}/decision", json={"decision": "approve", "decided_by": "owner"})
    contacts = client.get(f"/api/owners/{OWNER}/access-requests").json()["contacts"]
    assert contacts[0]["name"] == "Arjun Rao" and contacts[0]["approved_by"] == "owner"


def test_after_a_death_the_team_reviews(client):
    client.post(f"/api/owners/{OWNER}/death-report", files={"certificate": ("c.jpg", b"img", "image/jpeg")}, headers=PRIYA)
    client.post(f"/api/owners/{OWNER}/access-requests", json=ARJUN, headers=PRIYA)
    assert client.get(f"/api/owners/{OWNER}/access-requests").json()["requests"] == []
    req = client.get("/api/admin/access-requests").json()["requests"][0]
    r = client.post(f"/api/access-requests/{req['id']}/decision",
                    json={"decision": "decline", "decided_by": "team"}, headers=ANJALI)
    assert r.json()["status"] == "declined"
