"""Private demo copies (PR-1.2, PR-1.5, PR-1.6): two judges signed in at the same time never
see or change each other's data, however they shape the request."""

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, demo, main
from app.config import settings
from app.store import store

TOKENS = {
    "judge-a": {"sub": "uid-judge-a", "email": "a@hack2skill.com", "email_verified": True},
    "judge-b": {"sub": "uid-judge-b", "email": "b@hack2skill.com", "email_verified": True},
}


def h(who, person="vikram"):
    return {"Authorization": f"Bearer {who}", demo.ACTING_HEADER: person}


@pytest.fixture()
def client(monkeypatch):
    def fake_verify(token, request, audience=None):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    monkeypatch.setattr(auth.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["@hack2skill.com"])
    return TestClient(main.app)


@pytest.fixture()
def copies(client):
    a = client.get("/api/me", headers=h("judge-a")).json()["owner_id"]
    b = client.get("/api/me", headers=h("judge-b")).json()["owner_id"]
    return a, b


def _a_data(a: str) -> dict:
    """A few of judge A's documents, one of each kind a route looks up by ID."""
    now = time.time()
    docs = {
        "turns": {"id": "turn_a", "owner_id": a, "at": now, "role": "assistant", "text": "Hello Vikram"},
        "moments": {"id": "m_a", "owner_id": a, "status": "pending", "kind": "story", "title": "t",
                    "summary": "s", "quote": "q", "people": [], "places": [], "year": None},
        "tasks": {"id": "task_a", "owner_id": a, "status": "open", "title": "t"},
        "cases": {"id": "case_a", "owner_id": a, "state": "reported", "history": [{"at": now, "event": "x"}],
                  "certificate_check": None, "authority_check": None, "wait_until": None},
        "access_requests": {"id": "req_a", "owner_id": a, "at": now, "name": "Arjun", "relationship": "cousin",
                            "contact": "arjun@example.com", "note": "", "status": "pending_review"},
    }
    for collection, doc in docs.items():
        store.put(collection, doc)
    return docs


def test_each_judge_gets_their_own_copy(client, copies):
    a, b = copies
    assert a != b
    assert client.get(f"/api/owners/{a}", headers=h("judge-a")).json()["id"] == a
    assert client.get(f"/api/owners/{b}", headers=h("judge-b")).json()["id"] == b
    assert store.get("demo_copies", a.removeprefix("owner_")) is not None


def test_no_route_with_an_owner_reaches_another_copy(client, copies):
    a, _ = copies
    checked = 0
    for route in main.api.routes:  # the /api router (the app wraps it on include)
        path = getattr(route, "path", "")
        if "{owner_id}" not in path or not hasattr(route, "methods"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            for person in demo.PEOPLE:  # whoever B is acting as
                r = client.request(method, path.replace("{owner_id}", a), headers=h("judge-b", person))
                if route.name == "request_access":  # neutral reply (or not Priya's to send), nothing stored
                    assert r.status_code in (200, 403, 422), path
                else:
                    assert r.status_code == 404, f"{method} {path} as {person} gave {r.status_code}"
            checked += 1
    assert checked >= 25  # every owner route, not a handful
    assert not store.list("access_requests", owner_id=a)


def test_no_route_by_id_reaches_another_copy(client, copies):
    a, _ = copies
    _a_data(a)
    # B acts as the person each action needs, so only the copy check stands in the way.
    b, b_reviewer = h("judge-b"), h("judge-b", "anjali")
    assert client.post("/api/speech", headers=b, json={"turn_id": "turn_a"}).status_code == 404
    assert client.post("/api/proposals/m_a/decision", headers=b,
                       json={"collection": "moments", "decision": "approve"}).status_code == 404
    assert client.post("/api/tasks/task_a", headers=b, json={"status": "done"}).status_code == 404
    assert client.post("/api/admin/cases/case_a/confirm-death", headers=b_reviewer, json={}).status_code == 404
    assert client.post("/api/access-requests/req_a/decision", headers=b_reviewer,
                       json={"decision": "approve", "decided_by": "team"}).status_code == 404
    assert client.get("/api/admin/cases", headers=b).json()["cases"] == []
    assert client.get("/api/admin/access-requests", headers=b).json()["requests"] == []
    # A still sees their own.
    assert [c["id"] for c in client.get("/api/admin/cases", headers=h("judge-a")).json()["cases"]] == ["case_a"]
    assert store.get("moments", "m_a")["status"] == "pending" and store.get("tasks", "task_a")["status"] == "open"


def test_the_live_call_only_opens_your_own_copy(client, copies):
    a, _ = copies
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/api/owners/{a}/live") as ws:
            ws.send_json({"type": "auth", "token": "judge-b"})
            ws.receive_text()
    assert closed.value.code == 4404


def test_resetting_one_copy_leaves_the_other_alone(client, copies):
    a, b = copies
    _a_data(a)
    store.put("turns", {"id": "turn_b", "owner_id": b, "at": time.time(), "role": "owner", "text": "hi"})
    assert client.post("/api/demo/seed", headers=h("judge-b")).status_code == 200
    assert store.get("turns", "turn_b") is None  # B's copy is back to the start
    assert store.get("turns", "turn_a") is not None and store.get("cases", "case_a") is not None
    assert store.get("owners", a) is not None


def test_signing_in_twice_keeps_the_same_copy(client, copies):
    a, _ = copies
    store.put("turns", {"id": "turn_a2", "owner_id": a, "at": time.time(), "role": "owner", "text": "hi"})
    assert client.get("/api/me", headers=h("judge-a")).json()["owner_id"] == a
    assert store.get("turns", "turn_a2") is not None  # not reset on the next sign-in


def test_the_admin_portal_never_creates_a_demo_copy(client):
    TOKENS["portal-only"] = {"sub": "uid-portal-only", "email": "ops@hack2skill.com", "email_verified": True}
    try:
        assert client.get("/api/platform/me", headers=h("portal-only")).status_code == 200
        key = client.get("/api/platform/me", headers=h("portal-only")).json()["key"]
        assert store.get("demo_copies", key) is None
        assert store.get("owners", f"owner_{key}") is None
    finally:
        TOKENS.pop("portal-only")


def test_a_handover_conversation_never_continues_in_another_copy():
    import asyncio

    from app import executor_agent

    # Copy A's conversation with owner A, in the store (PR-15.8).
    executor_agent.record_turn("hs_copya", "owner_a", "family:copy-a", "What did A leave?",
                               model_answer="A note.", answer="A note.", sources=[])
    for owner, user in (({"id": "owner_b", "name": "B"}, "family:copy-b"),   # another copy
                        ({"id": "owner_b", "name": "B"}, "family:copy-a")):  # same person, other owner
        with pytest.raises(executor_agent.UnknownSession):
            asyncio.run(executor_agent.ask(owner, user, "What did A leave?", "hs_copya"))
