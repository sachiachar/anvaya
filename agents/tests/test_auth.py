"""Sign-in, the demo allowlist and open sign-in, with Google's token check stubbed out."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, main
from app.config import Settings, settings
from app.governance import core as governance
from app.store import store

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


# ---- Open sign-in for judging (PR-15.2) --------------------------------------------


def test_open_sign_in_is_off_unless_it_is_set(monkeypatch):
    monkeypatch.delenv("OPEN_SIGN_IN", raising=False)
    assert Settings().open_sign_in is False


def test_with_open_sign_in_any_verified_google_account_gets_its_own_copy(client, monkeypatch):
    monkeypatch.setattr(settings, "open_sign_in", True)
    monkeypatch.setattr(settings, "allowed_emails", [])
    stranger = get(client, "stranger")
    assert stranger.status_code == 200 and stranger.json()["email"] == "someone@gmail.com"
    copy_id = stranger.json()["owner_id"].removeprefix("owner_")
    assert store.get("demo_copies", copy_id) is not None
    assert get(client, "unverified").status_code == 403  # a verified email is still needed


def test_with_open_sign_in_a_judge_sees_only_their_own_copy(client, monkeypatch):
    monkeypatch.setattr(settings, "open_sign_in", True)
    monkeypatch.setattr(settings, "allowed_emails", [])
    mine = get(client, "stranger").json()["owner_id"]
    theirs = get(client, "judge").json()["owner_id"]
    assert mine != theirs
    as_stranger = {"Authorization": "Bearer stranger"}
    assert client.get(f"/api/owners/{mine}", headers=as_stranger).status_code == 200
    assert client.get(f"/api/owners/{theirs}", headers=as_stranger).status_code == 404
    # In the platform admin portal, they decide for their own copy only.
    assert client.get("/api/platform/me", headers=as_stranger).json()["role"] == "demo_admin"


def test_with_open_sign_in_the_portal_shows_a_new_account_only_its_own_records(client, monkeypatch):
    monkeypatch.setattr(settings, "open_sign_in", True)
    monkeypatch.setattr(settings, "allowed_emails", [])
    store.put("platform_audit", {"id": "aud_team", "at": 1.0, "actor": "team@example.com", "actor_key": "u_team",
                                 "role": "platform_admin", "action": "kill_switch.set", "target": "off",
                                 "reason": "a team reason", "detail": {}})
    store.put("guardian_events", {"id": "gev_team", "at": 1.0, "agent": "saathi.chat", "response": "add_check",
                                  "reason": "r", "case_key": None, "status": "confirmed",
                                  "user_key": governance.user_key(TOKENS["stranger"]["sub"]),  # their own copy's
                                  "decided_by": "team@example.com", "decision_reason": "a team reason"})
    store.put("gateway_blocked", {"id": "blk_other", "at": 1.0, "agent": "saathi.chat", "kind": "allowance",
                                  "attempted": "owner.tokens", "reason": "r", "user_key": "u_someoneelse"})
    as_new = {"Authorization": "Bearer stranger"}  # straight to the portal: no demo copy yet
    assert client.get("/api/platform/me", headers=as_new).json()["role"] == "demo_admin"
    assert client.get("/api/platform/audit", headers=as_new).json()["entries"] == []
    events = client.get("/api/platform/guardian", headers=as_new).json()["events"]
    team_event = next(e for e in events if e["id"] == "gev_team")
    assert team_event["decided_by"] == "Anvaya team" and "decision_reason" not in team_event
    assert client.get("/api/platform/agents", headers=as_new).json()["blocked"] == []


def test_a_demo_admin_sees_who_decided_their_request_only_as_the_team(client, monkeypatch):
    monkeypatch.setattr(settings, "open_sign_in", True)
    monkeypatch.setattr(settings, "allowed_emails", [])
    mine = governance.user_key(TOKENS["stranger"]["sub"])
    for rid, by in (("req_team", "team@example.com"), ("req_self", "someone@gmail.com")):
        store.put("budget_requests", {"id": rid, "at": 1.0, "tenant": "direct", "user_key": mine, "copy_id": mine,
                                      "kind": "voice_minutes", "amount": 10, "hours": 24, "reason": "test",
                                      "status": "rejected", "decided_by": by, "decision_reason": f"{rid} reason"})
    history = client.get("/api/platform/approvals", headers={"Authorization": "Bearer stranger"}).json()["history"]
    by_id = {r["id"]: r for r in history}
    assert by_id["req_team"]["decided_by"] == "Anvaya team" and "decision_reason" not in by_id["req_team"]
    assert by_id["req_self"]["decided_by"] == "someone@gmail.com"  # their own decision stays as it was
    assert by_id["req_self"]["decision_reason"] == "req_self reason"
