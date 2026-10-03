"""Governance layer: roles, approvals, privacy, the kill switch, registry checks and the ledger."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, gemini, main
from app.config import settings
from app.governance import core
from app.governance.seed import seed
from app.store import store

TOKENS = {
    "admin": {"sub": "u-admin", "email": "admin@anvaya.test", "email_verified": True},
    "approver": {"sub": "u-approver", "email": "money@anvaya.test", "email_verified": True},
    "risk": {"sub": "u-risk", "email": "risk@anvaya.test", "email_verified": True},
    "judge": {"sub": "u-judge", "email": "judge@hack2skill.com", "email_verified": True},
}


@pytest.fixture()
def client(monkeypatch):
    def fake_verify(token, request, audience=None):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    monkeypatch.setattr(auth.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["@anvaya.test", "@hack2skill.com"])
    monkeypatch.setattr(settings, "platform_roles", {
        "admin@anvaya.test": "platform_admin", "money@anvaya.test": "budget_approver",
        "risk@anvaya.test": "risk_reviewer"})
    monkeypatch.setattr(settings, "platform_default_role", "viewer")
    seed()
    return TestClient(main.app)


def h(who):
    return {"Authorization": f"Bearer {who}"}


def test_roles_come_from_the_server(client):
    assert client.get("/api/platform/me", headers=h("judge")).json()["role"] == "viewer"
    assert client.get("/api/platform/me", headers=h("admin")).json()["role"] == "platform_admin"


def test_no_role_means_no_portal(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_default_role", "none")
    assert client.get("/api/platform/overview", headers=h("judge")).status_code == 403


def test_viewer_can_read_but_not_decide(client):
    pending = client.get("/api/platform/approvals", headers=h("judge")).json()["pending"]
    assert pending
    r = client.post(f"/api/platform/approvals/{pending[0]['id']}/decision", headers=h("judge"),
                    json={"decision": "approve", "reason": "looks fine", "hours": 24})
    assert r.status_code == 403


def test_approver_decides_once_with_a_reason_and_it_is_audited(client):
    req = client.get("/api/platform/approvals", headers=h("approver")).json()["pending"][0]
    url = f"/api/platform/approvals/{req['id']}/decision"
    assert client.post(url, headers=h("approver"), json={"decision": "approve", "reason": ""}).status_code == 422
    ok = client.post(url, headers=h("approver"), json={"decision": "approve", "reason": "Judge needs more", "hours": 24})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["expires_at"]
    again = client.post(url, headers=h("approver"), json={"decision": "reject", "reason": "changed mind"})
    assert again.status_code == 409
    audit = client.get("/api/platform/audit", headers=h("admin")).json()["entries"]
    assert any(e["action"] == "budget.approve" and e["target"] == req["id"] for e in audit)


def test_nobody_approves_their_own_request(client):
    req = client.get("/api/platform/approvals", headers=h("approver")).json()["pending"][0]
    store.update("budget_requests", req["id"], {"user_key": core.user_key("u-approver")})
    r = client.post(f"/api/platform/approvals/{req['id']}/decision", headers=h("approver"),
                    json={"decision": "approve", "reason": "mine", "hours": 24})
    assert r.status_code == 403


def test_risk_reviewer_reverses_an_intervention_and_false_alarms_are_counted(client):
    g = client.get("/api/platform/guardian", headers=h("risk")).json()
    open_event = next(e for e in g["events"] if e["status"] == "open")
    r = client.post(f"/api/platform/guardian/{open_event['id']}/decision", headers=h("risk"),
                    json={"decision": "reverse", "reason": "Checked with the owner"})
    assert r.json()["status"] == "reversed"
    assert client.get("/api/platform/guardian", headers=h("risk")).json()["false_alarm_rate"] > 0
    assert client.post(f"/api/platform/guardian/{open_event['id']}/decision", headers=h("approver"),
                       json={"decision": "confirm", "reason": "x" * 5}).status_code in (403, 409)


def _keys(value, found):
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys(v, found)
    return found


def test_portal_never_returns_conversation_text_or_names(client):
    keys = set()
    for path in ("overview", "agents", "approvals", "guardian", "fraud", "demos"):
        body = client.get(f"/api/platform/{path}", headers=h("admin")).json()
        _keys(body, keys)
        assert "@" not in str(body), path  # no emails, so no names
    assert not keys & {"text", "quote", "summary", "heard", "name", "email", "transcript"}


def test_small_counts_are_hidden(client):
    tenants = client.get("/api/platform/overview", headers=h("admin")).json()["usage"]["by_tenant"]
    for t in tenants.values():
        assert t["active_users_today"] is None or t["active_users_today"] >= core.SMALL_COUNT
    assert core.small(4) is None and core.small(5) == 5


def test_kill_switch_pauses_non_safety_agents_only(client):
    assert client.post("/api/platform/kill-switch", headers=h("risk"),
                       json={"state": "all_paused", "reason": "spend spike"}).status_code == 403
    assert client.post("/api/platform/kill-switch", headers=h("admin"),
                       json={"state": "all_paused", "reason": "spend spike"}).status_code == 200
    with pytest.raises(core.Refused):
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    core.check_call("activation.checker", "gemini-3.1-flash-lite")  # safety work continues
    client.post("/api/platform/kill-switch", headers=h("admin"), json={"state": "off", "reason": "resolved"})


def test_registry_refuses_unlisted_models_and_agents_and_records_it(client):
    before = len(store.list("gateway_blocked"))
    with pytest.raises(core.Refused):
        core.check_call("saathi.chat", "gemini-3.8-pro")
    with pytest.raises(core.Refused):
        core.check_call("rogue.agent", "gemini-3.1-flash-lite")
    assert len(store.list("gateway_blocked")) == before + 2


def test_generate_json_names_its_agent_and_records_usage(client, monkeypatch):
    from app.schemas import DocumentCheck

    parsed = DocumentCheck(document_type="x", is_expected_type=True, matches_expected_person=True, confidence=0.9)
    response = SimpleNamespace(parsed=parsed, text=None, usage_metadata=SimpleNamespace(
        prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=None))

    class FakeModels:
        async def generate_content(self, **_):
            return response

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    day = core._today()
    calls_before = (store.get("usage_days", day) or {"agents": {}})["agents"].get("activation.checker", {}).get("calls", 0)
    asyncio.run(gemini.generate_json(model="gemini-3.1-flash-lite", schema=DocumentCheck, prompt="p",
                                     agent="activation.checker"))
    row = store.get("usage_days", day)["agents"]["activation.checker"]
    assert row["calls"] == calls_before + 1 and row["tokens_in"] >= 1000 and "cost_usd" not in row


def test_portal_shows_usage_not_estimates_and_reseeds_old_cost_data(client):
    from app.governance import seed as seeding

    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=10, tokens_out=5)
    o = client.get("/api/platform/overview", headers=h("admin")).json()
    assert o["spend"]["available"] is False  # no billing export in tests: no guessing
    assert o["usage"]["by_agent"]["saathi.chat"]["calls"] > 0
    assert "usd" not in str(o["usage"]).lower()
    store.update("platform_settings", "settings", {"data_version": 2})  # data with estimated costs
    seeding.ensure_seeded()
    assert core.platform_settings()["data_version"] == seeding.DATA_VERSION


def test_seed_never_writes_usage_or_blocked_calls_and_clears_old_samples():
    store.put("usage_days", {"id": "2026-09-01", "day": "2026-09-01", "seeded": True, "agents": {}, "tenants": {}})
    store.put("usage_days", {"id": "2026-09-02", "day": "2026-09-02", "agents": {}, "tenants": {}})  # real use
    store.put("gateway_blocked", {"id": "blk_old", "seeded": True, "at": 1.0, "agent": "saathi.chat"})
    seed()
    assert store.get("usage_days", "2026-09-01") is None and store.get("gateway_blocked", "blk_old") is None
    assert store.get("usage_days", "2026-09-02") is not None
    assert not store.list("usage_days", seeded=True) and not store.list("gateway_blocked", seeded=True)
    assert store.list("budget_requests", seeded=True) and store.list("guardian_events", seeded=True)


def test_reseeding_keeps_the_kill_switch(client):
    assert client.post("/api/platform/kill-switch", headers=h("admin"),
                       json={"state": "optional_paused", "reason": "spend spike"}).status_code == 200
    seed()
    assert core.kill_switch() == "optional_paused"
    assert core.platform_settings()["kill_switch_reason"] == "spend spike"
    store.update("platform_settings", "settings", {"kill_switch": "off", "kill_switch_reason": None})
