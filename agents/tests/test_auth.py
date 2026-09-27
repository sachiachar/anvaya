"""Sign-in and the demo allowlist, with Google's token check stubbed out."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, main
from app.config import settings

TOKENS = {
    "sachi": {"sub": "u1", "email": "sachi@example.com", "email_verified": True},
    "judge": {"sub": "u2", "email": "judge@hack2skill.com", "email_verified": True},
    "stranger": {"sub": "u3", "email": "someone@gmail.com", "email_verified": True},
    "unverified": {"sub": "u4", "email": "sachi@example.com", "email_verified": False},
}


@pytest.fixture()
def client(monkeypatch):
    def fake_verify(token, request, audience=None):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    monkeypatch.setattr(auth.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["sachi@example.com", "@hack2skill.com"])
    return TestClient(main.app)


def get(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/api/me", headers=headers)


def test_signed_out_is_refused(client):
    assert get(client).status_code == 401
    assert get(client, "garbage").status_code == 401


def test_listed_email_and_listed_domain_get_in(client):
    assert get(client, "sachi").json()["email"] == "sachi@example.com"
    assert get(client, "judge").status_code == 200


def test_everyone_else_is_refused_with_a_clear_message(client):
    r = get(client, "stranger")
    assert r.status_code == 403 and "isn't on the list" in r.json()["detail"]
    assert get(client, "unverified").status_code == 403


def test_every_api_route_is_protected_but_health_stays_open(client):
    assert client.post("/api/demo/seed").status_code == 401
    assert client.get("/api/admin/cases").status_code == 401
    assert client.get("/api/healthz").status_code == 200


def test_live_call_needs_an_allowed_token(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/owners/owner_demo_vikram/live?token=stranger") as ws:
            ws.receive_json()
    assert closed.value.code == 4403
