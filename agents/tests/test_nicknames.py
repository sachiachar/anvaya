"""Optional nicknames for demo copies, and the portal's list of real copies (PR-1.2, PR-12.6):
a visitor may name their own copy once; only platform admins see the nickname, and it's
never written to the audit log."""

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, demo, main
from app.config import settings
from app.governance import core
from app.store import store

TOKENS = {
    "admin": {"sub": "u-nick-admin", "email": "admin@anvaya.test", "email_verified": True},
    "judge": {"sub": "u-nick-judge", "email": "judge@hack2skill.com", "email_verified": True},
    "other": {"sub": "u-nick-other", "email": "other@hack2skill.com", "email_verified": True},
}


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
    monkeypatch.setattr(settings, "allowed_emails", ["@anvaya.test", "@hack2skill.com"])
    monkeypatch.setattr(settings, "platform_roles", {"admin@anvaya.test": "platform_admin"})
    monkeypatch.setattr(settings, "platform_default_role", "viewer")
    for who in TOKENS.values():
        store.delete("demo_copies", core.user_key(who["sub"]))
    return TestClient(main.app)


def test_asked_once_and_skipping_counts_as_an_answer(client):
    assert client.get("/api/me", headers=h("judge")).json()["ask_nickname"] is True
    assert client.post("/api/me/nickname", headers=h("judge"), json={"nickname": None}).json() == {"nickname": None}
    me = client.get("/api/me", headers=h("judge")).json()
    assert me["ask_nickname"] is False and me["nickname"] is None


def test_a_nickname_is_one_short_line(client):
    client.get("/api/me", headers=h("judge"))
    r = client.post("/api/me/nickname", headers=h("judge"), json={"nickname": "  Judge \n  M\x00ehta  "})
    assert r.json()["nickname"] == "Judge Mehta"
    long = client.post("/api/me/nickname", headers=h("judge"), json={"nickname": "x" * 80}).json()["nickname"]
    assert len(long) == demo.NICKNAME_MAX


def test_only_platform_admins_see_nicknames_and_everyone_sees_their_own_copy(client):
    client.get("/api/me", headers=h("judge"))
    client.post("/api/me/nickname", headers=h("judge"), json={"nickname": "Judge M"})
    judge_key = core.user_key("u-nick-judge")

    admin_view = {c["id"]: c for c in client.get("/api/platform/demos", headers=h("admin")).json()["copies"]}
    assert admin_view[judge_key]["nickname"] == "Judge M" and admin_view[judge_key]["is_you"] is False

    judge_view = client.get("/api/platform/demos", headers=h("judge")).json()
    assert judge_view["nicknames_visible"] is False
    assert all("nickname" not in c for c in judge_view["copies"])
    assert "Judge M" not in str(judge_view)
    assert [c["is_you"] for c in judge_view["copies"] if c["id"] == judge_key] == [True]


def test_the_nickname_never_reaches_the_audit_log(client):
    client.get("/api/me", headers=h("judge"))
    client.post("/api/me/nickname", headers=h("judge"), json={"nickname": "Judge Secret"})
    judge_key = core.user_key("u-nick-judge")
    client.post(f"/api/platform/demos/{judge_key}/reset", headers=h("admin"), json={"reason": "fresh start"})
    assert "Judge Secret" not in str(client.get("/api/platform/audit", headers=h("admin")).json())


def test_allowance_used_is_todays_real_use(client):
    client.get("/api/me", headers=h("judge"))
    judge_key = core.user_key("u-nick-judge")
    limit = core.allowances()["owner"]["tokens_per_day"]
    store.put("user_usage", {"id": f"{core._today()}:{judge_key}", "owner": {"tokens": limit // 4}})
    copies = {c["id"]: c for c in client.get("/api/platform/demos", headers=h("admin")).json()["copies"]}
    assert copies[judge_key]["allowance_used"] == 0.25
    # An approved request for more counts, as it does for the gateway: doubling the allowance halves the share.
    store.put("budget_requests", {"id": "req_nick", "user_key": judge_key, "kind": "daily_allowance", "amount": limit // 1000,
                                  "status": "approved", "expires_at": time.time() + 3600})
    copies = {c["id"]: c for c in client.get("/api/platform/demos", headers=h("admin")).json()["copies"]}
    assert copies[judge_key]["allowance_used"] == 0.125
    store.delete("budget_requests", "req_nick")


def test_a_portal_reset_restores_that_copy_only(client):
    judge_owner = client.get("/api/me", headers=h("judge")).json()["owner_id"]
    other_owner = client.get("/api/me", headers=h("other")).json()["owner_id"]
    for oid in (judge_owner, other_owner):
        store.put("turns", {"id": f"turn_{oid}", "owner_id": oid, "at": time.time(), "role": "owner", "text": "hi"})
    judge_key = core.user_key("u-nick-judge")
    viewer = client.post(f"/api/platform/demos/{judge_key}/reset", headers=h("judge"), json={"reason": "please"})
    assert viewer.status_code == 403  # only a platform admin manages copies
    assert client.post(f"/api/platform/demos/{judge_key}/reset", headers=h("admin"),
                       json={"reason": "fresh start"}).status_code == 200
    assert store.get("turns", f"turn_{judge_owner}") is None
    assert store.get("turns", f"turn_{other_owner}") is not None
    assert store.get("owners", judge_owner) is not None  # back to the starting persona
