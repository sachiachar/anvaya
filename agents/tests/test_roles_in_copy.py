"""Inside one demo copy, each person sees only their own part (SEC-2, SEC-9), and the server never
runs open on Google Cloud (SEC-8)."""

import pytest
from fastapi.testclient import TestClient

from app import escrow, main, release
from app.config import Settings, production_problems
from app.store import store
from tests.people import acting

OWNER = "owner_demo_vikram"
EVERYONE = ("vikram", "priya", "anjali", "rahul", "admin")
OWNER_ONLY = ("/conversation", "/review", "/tasks", "/vault/items", "/access-requests")
TEAM_ONLY = ("/api/admin/cases", "/api/admin/access-requests")


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def test_the_owners_own_records_are_for_the_owner_only(client):
    for path in OWNER_ONLY:
        for person in EVERYONE:
            status = client.get(f"/api/owners/{OWNER}{path}", headers=acting(person)).status_code
            assert status == (200 if person == "vikram" else 403), (path, person, status)


def test_the_review_lists_are_for_reviewers_only(client):
    for path in TEAM_ONLY:
        for person in EVERYONE:
            status = client.get(path, headers=acting(person)).status_code
            assert status == (200 if person in ("anjali", "rahul") else 403), (path, person, status)


def test_the_family_reads_the_archive_only_after_the_handover(client, monkeypatch):
    archive = f"/api/owners/{OWNER}/archive"
    assert client.get(archive, headers=acting("vikram")).status_code == 200
    assert client.get(archive, headers=acting("priya")).status_code == 403
    assert client.get(archive, headers=acting("anjali")).status_code == 403
    monkeypatch.setattr(release, "is_released", lambda case: True)
    assert client.get(archive, headers=acting("priya")).status_code == 200


def test_no_one_gets_the_vault_escrow_or_key_backup_from_the_owner_record(client):
    store.update("owners", OWNER, {"vault_escrow": "dev:secret", "contacts": [{"name": "Arjun"}],
                                   "vault_recipient": {"key_backup": {"wrapped": "x"}}})
    for person in EVERYONE:
        owner = client.get(f"/api/owners/{OWNER}", headers=acting(person)).json()
        assert set(owner) <= set(main.OWNER_FIELDS) and owner["name"], person
        assert "vault_escrow" not in owner and "vault_recipient" not in owner and "contacts" not in owner


def test_on_google_cloud_the_server_refuses_to_run_open():
    local = Settings(store="memory", auth_required=False, kms_key_name="")
    assert production_problems(local, environ={}) == []  # local development needs nothing
    open_ = production_problems(local, environ={"K_SERVICE": "life-after-agents"})
    assert len(open_) == 4 and "AUTH_REQUIRED must be true" in open_
    ready = Settings(store="firestore", auth_required=True, firebase_project_id="p", allowed_emails=[],
                     open_sign_in=True, kms_key_name="projects/p/keys/k")
    assert production_problems(ready, environ={"K_SERVICE": "life-after-agents"}) == []
    no_one = Settings(store="firestore", auth_required=True, firebase_project_id="p", allowed_emails=[],
                      open_sign_in=False, kms_key_name="projects/p/keys/k")
    assert production_problems(no_one, environ={}) == ["set ALLOWED_EMAILS, or OPEN_SIGN_IN=true"]


def test_dev_mode_escrow_never_reaches_firestore(monkeypatch):
    monkeypatch.setattr(escrow.settings, "kms_key_name", "")
    monkeypatch.setattr(escrow.settings, "store", "firestore")
    with pytest.raises(RuntimeError):
        escrow.wrap("a2V5")
    monkeypatch.setattr(escrow.settings, "store", "memory")
    assert escrow.wrap("a2V5") == "dev:a2V5"
