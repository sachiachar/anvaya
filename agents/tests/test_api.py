"""API tests with Gemini stubbed out, so they run without cloud credentials."""

import pytest
from fastapi.testclient import TestClient

from app import loops, main, release
from app.schemas import DocumentCheck
from tests.people import ANJALI, PRIYA, RAHUL

OWNER = "owner_demo_vikram"
BACKUP = {"kdf": "PBKDF2-SHA256", "iterations": 600000, "salt": "c2FsdA==", "iv": "aXY=", "ciphertext": "Y3Q="}
RECIPIENT = {"name": "Priya", "relationship": "daughter", "public_key_jwk": {"kty": "RSA"}, "key_backup": BACKUP}


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


def test_seed_starts_with_an_empty_archive(client):
    archive = client.get(f"/api/owners/{OWNER}/archive").json()
    assert archive["moments"] == [] and archive["inventory"] == []
    assert client.get(f"/api/owners/{OWNER}").json()["name"] == "Vikram Rao"


def test_handover_is_locked_before_release(client):
    assert client.post(f"/api/owners/{OWNER}/ask", json={"question": "hi"}, headers=PRIYA).status_code == 403
    assert client.post(f"/api/owners/{OWNER}/settlement", headers=PRIYA).status_code == 403
    assert client.get(f"/api/owners/{OWNER}/vault/release-bundle", headers=PRIYA).status_code == 403


def test_legal_handoff_requires_consent(client):
    body = {"partner": "Demo Legal", "owner_consent": False, "needs": []}
    assert client.post(f"/api/owners/{OWNER}/legal-handoff", json=body).status_code == 400
    body["owner_consent"] = True
    assert client.post(f"/api/owners/{OWNER}/legal-handoff", json=body).status_code == 200


def test_vault_release_flow_returns_only_ciphertext(client):
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient",
                json=RECIPIENT, headers=PRIYA)
    client.post(f"/api/owners/{OWNER}/vault/recipient/decision", json={"decision": "approve"})
    client.post(f"/api/owners/{OWNER}/vault/escrow", json={"recipient_wrapped_key": "d3JhcHBlZA=="})
    client.post(f"/api/owners/{OWNER}/vault/items",
                json={"category": "password", "iv": "aXY=", "ciphertext": "Y3Q=", "ai_label": "Email login"})

    files = {"certificate": ("c.jpg", b"img", "image/jpeg")}
    case = client.post(f"/api/owners/{OWNER}/death-report", files=files, headers=PRIYA).json()["case"]
    client.post(f"/api/admin/cases/{case['id']}/confirm-death", json={"reviewer": "team"}, headers=ANJALI)
    client.post(f"/api/owners/{OWNER}/release/authority", files={"proof": ("p.jpg", b"img", "image/jpeg")}, headers=PRIYA)
    client.post(f"/api/admin/cases/{case['id']}/confirm-authority", json={"reviewer": "team-2"}, headers=RAHUL)

    bundle = client.get(f"/api/owners/{OWNER}/vault/release-bundle", headers=PRIYA)
    assert bundle.status_code == 200
    data = bundle.json()
    assert data["recipient_wrapped_key"] == "d3JhcHBlZA=="
    assert data["items"][0]["ciphertext"] == "Y3Q="


def test_owner_alive_cancels_release(client):
    files = {"certificate": ("c.jpg", b"img", "image/jpeg")}
    case = client.post(f"/api/owners/{OWNER}/death-report", files=files, headers=PRIYA).json()["case"]
    client.post(f"/api/admin/cases/{case['id']}/confirm-death", json={"reviewer": "team"}, headers=ANJALI)
    assert client.post(f"/api/owners/{OWNER}/alive").json()["case"]["state"] == "cancelled"
    assert client.get(f"/api/owners/{OWNER}/vault/release-bundle", headers=PRIYA).status_code == 403


def test_no_answer_is_kept_by_a_cache(client):
    """Firebase Hosting kept a 404 for 10 minutes, so Priya's app saw "no recipient yet" after she
    accepted and offered Accept again. Every answer, found or not, says not to keep it."""
    missing = client.get(f"/api/owners/{OWNER}/vault/recipient", headers=PRIYA)
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA)
    found = client.get(f"/api/owners/{OWNER}/vault/recipient", headers=PRIYA)
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store"
    assert client.get("/api/no-such-route").headers["cache-control"] == "no-store"
    assert client.get("/api/healthz").headers["cache-control"] == "no-store"


def test_vault_cannot_be_sealed_for_an_unapproved_recipient(client):
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA)
    assert client.get(f"/api/owners/{OWNER}/vault/recipient").json()["status"] == "pending_owner"
    assert client.post(f"/api/owners/{OWNER}/vault/escrow",
                       json={"recipient_wrapped_key": "d3JhcHBlZA=="}).status_code == 409


def test_new_recipient_key_requires_reseal_and_backup_is_downloadable(client):
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA)
    client.post(f"/api/owners/{OWNER}/vault/recipient/decision", json={"decision": "approve"})
    client.post(f"/api/owners/{OWNER}/vault/escrow", json={"recipient_wrapped_key": "d3JhcHBlZA=="})
    got = client.get(f"/api/owners/{OWNER}/vault/recipient").json()
    assert got["vault_sealed"] is True and got["key_backup"] == BACKUP

    # Recovery code lost while the owner is alive: a new key clears the old seal.
    assert client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA).status_code == 200
    assert client.get(f"/api/owners/{OWNER}/vault/recipient").json()["vault_sealed"] is False


def test_recipient_key_cannot_change_once_a_case_is_open(client):
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA)
    client.post(f"/api/owners/{OWNER}/death-report", files={"certificate": ("c.jpg", b"img", "image/jpeg")}, headers=PRIYA)
    assert client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA).status_code == 409


def test_nobody_becomes_recipient_without_the_owners_invitation(client):
    assert client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA).status_code == 409
    # Vikram chose someone else: Priya can't register in their place.
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Meena Rao", "relationship": "wife"})
    r = client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA)
    assert r.status_code == 409 and "not you" in r.json()["detail"]
    # Vikram chose Priya: she registers under the invited name, whatever name is sent.
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Priya Rao", "relationship": "daughter"})
    client.post(f"/api/owners/{OWNER}/vault/recipient", json={**RECIPIENT, "name": "Someone else"}, headers=PRIYA)
    assert client.get(f"/api/owners/{OWNER}/vault/recipient").json()["name"] == "Priya Rao"


def test_an_empty_invitation_is_refused_not_a_crash(client):
    client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "  ", "relationship": "daughter"})
    assert client.post(f"/api/owners/{OWNER}/vault/recipient", json=RECIPIENT, headers=PRIYA).status_code == 409


def test_vikram_starts_with_priya_in_his_family(client):
    assert "Priya Rao (daughter)" in client.get(f"/api/owners/{OWNER}").json()["family"]
