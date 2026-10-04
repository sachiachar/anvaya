"""Asking for more (PR-10.6) and judges as the platform admin of their own demo (PR-12.9): a
person asks in the app; the copy's demo admin decides in the portal, for that copy only; the
allowance rises until the grant expires; real roles keep "nobody approves their own request"."""

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.config import settings
from app.governance import core
from app.store import store

TOKENS = {
    "judge-a": {"sub": "u-ask-a", "email": "a@hack2skill.com", "email_verified": True},
    "judge-b": {"sub": "u-ask-b", "email": "b@hack2skill.com", "email_verified": True},
    "approver": {"sub": "u-ask-approver", "email": "money@anvaya.test", "email_verified": True},
    "admin": {"sub": "u-ask-admin", "email": "admin@anvaya.test", "email_verified": True},
}
MORE = {"kind": "daily_allowance", "amount": 100, "reason": "Recording a long family story"}


def h(who):
    return {"Authorization": f"Bearer {who}"}


@pytest.fixture()
def client(monkeypatch):
    def fake_verify(token, request, audience=None):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    monkeypatch.setattr(auth.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["@hack2skill.com", "@anvaya.test"])
    monkeypatch.setattr(settings, "platform_roles", {"money@anvaya.test": "budget_approver",
                                                     "admin@anvaya.test": "platform_admin"})
    monkeypatch.setattr(settings, "platform_default_role", "viewer")
    for doc in store.list("budget_requests"):
        store.delete("budget_requests", doc["id"])
    c = TestClient(main.app)
    for who in ("judge-a", "judge-b", "approver"):
        c.get("/api/me", headers=h(who))  # opening the app creates each one's demo copy
    return c


def _ask(client, who="judge-a", **changes):
    return client.post("/api/allowance/request", headers=h(who), json={**MORE, **changes})


def test_a_person_asks_for_more_within_limits(client):
    r = _ask(client)
    assert r.status_code == 200 and r.json()["request"]["status"] == "pending"
    assert _ask(client).status_code == 409  # one waiting request per kind
    assert _ask(client, kind="voice_minutes", amount=500).status_code == 409  # more than one request may ask for
    assert _ask(client, kind="spoken_replies", reason="").status_code == 422
    mine = client.get("/api/allowance", headers=h("judge-a")).json()
    assert [(q["kind"], q["status"]) for q in mine["requests"]] == [("daily_allowance", "pending")]
    assert store.list("budget_requests")[0]["requested_by"] == "Vikram"


def test_a_judge_is_the_platform_admin_of_their_own_copy_only(client):
    a_req = _ask(client).json()["request"]["id"]
    b_req = _ask(client, who="judge-b").json()["request"]["id"]
    me = client.get("/api/platform/me", headers=h("judge-a")).json()
    assert me["role"] == "demo_admin" and me["permissions"] == ["approve_budget"]
    pending = client.get("/api/platform/approvals", headers=h("judge-a")).json()["pending"]
    assert [r["id"] for r in pending] == [a_req]  # judge B's request isn't shown
    other = client.post(f"/api/platform/approvals/{b_req}/decision", headers=h("judge-a"),
                        json={"decision": "approve", "reason": "looks fine", "hours": 24})
    assert other.status_code == 404  # nor can it be decided
    assert [d["id"] for d in client.get("/api/platform/demos", headers=h("judge-a")).json()["copies"]] == [me["key"]]
    assert client.post("/api/platform/kill-switch", headers=h("judge-a"),
                       json={"state": "all_paused", "reason": "try it"}).status_code == 403


def test_approving_raises_the_allowance_until_it_expires(client):
    req = _ask(client).json()["request"]["id"]
    key = core.user_key("u-ask-a")
    before = core.allowance_status(key)["owner"]["tokens"]["limit"]
    r = client.post(f"/api/platform/approvals/{req}/decision", headers=h("judge-a"),
                    json={"decision": "approve", "reason": "A long story is worth it", "hours": 24})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert core.allowance_status(key)["owner"]["tokens"]["limit"] == before + 100_000
    mine = client.get("/api/allowance", headers=h("judge-a")).json()["requests"]
    assert mine[0]["status"] == "approved" and mine[0]["expires_at"]
    audit = client.get("/api/platform/audit", headers=h("judge-a")).json()["entries"]
    assert [e["action"] for e in audit] == ["budget.approve"]  # a demo admin sees only their own decisions


def test_grants_never_stack_and_never_run_longer_than_asked(client):
    req = _ask(client, hours=24).json()["request"]["id"]
    r = client.post(f"/api/platform/approvals/{req}/decision", headers=h("judge-a"),
                    json={"decision": "approve", "reason": "Fine for a day", "hours": 168})
    assert r.json()["expires_at"] - r.json()["decided_at"] <= 24 * 3600  # never longer than asked
    again = _ask(client)
    assert again.status_code == 409 and "until it expires" in again.json()["detail"]  # no stacking
    assert _ask(client, kind="voice_minutes", amount=30).status_code == 200  # another kind is fine


def test_the_overview_counts_only_their_own_waiting_requests(client):
    _ask(client, who="judge-b")
    assert client.get("/api/platform/overview", headers=h("judge-a")).json()["pending_approvals"] == 0
    _ask(client)
    assert client.get("/api/platform/overview", headers=h("judge-a")).json()["pending_approvals"] == 1


def test_real_roles_still_cannot_approve_their_own_request(client):
    req = _ask(client, who="approver").json()["request"]["id"]
    assert client.get("/api/platform/me", headers=h("approver")).json()["role"] == "budget_approver"
    r = client.post(f"/api/platform/approvals/{req}/decision", headers=h("approver"),
                    json={"decision": "approve", "reason": "mine", "hours": 24})
    assert r.status_code == 403
    assert client.post(f"/api/platform/approvals/{req}/decision", headers=h("admin"),
                       json={"decision": "approve", "reason": "fine", "hours": 24}).status_code == 200


def test_someone_without_a_demo_copy_stays_a_viewer(client):
    TOKENS["browser"] = {"sub": "u-ask-browser", "email": "c@hack2skill.com", "email_verified": True}
    try:
        assert client.get("/api/platform/me", headers=h("browser")).json()["role"] == "viewer"
    finally:
        TOKENS.pop("browser")
