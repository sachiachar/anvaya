"""Bring your own model key (PR-10.10): each person's own key first, ours only as failover.

A key is used only for the calls its owner causes, is stored encrypted, is never sent back to
anyone (its owner included), and never reaches a log, the ledger or the audit log. Platform
admins see everyone's key usage, never a key. All keys here are synthetic.
"""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google import genai
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types

from app import auth, demo, executor_agent, fraud_guides, fraud_pipeline, gemini, live, main, model_access
from app.config import settings
from app.governance import core, keys
from app.schemas import DocumentCheck
from app.store import store

KEY_A = "AIzaSyTESTONLYaaaaaaaaaaaaaaaaaaaaaaaaa1"
KEY_B = "AIzaSyTESTONLYbbbbbbbbbbbbbbbbbbbbbbbbb2"
KEY_BAD = "AIzaSyTESTONLYccccccccccccccccccccccccc3"
PLATFORM_KEY = "AIzaSyTESTONLYpppppppppppppppppppppppppp"
NAMES = {KEY_A: "a", KEY_B: "b", KEY_BAD: "bad"}
TOKENS = {
    "judge-a": {"sub": "uid-judge-a", "email": "a@hack2skill.com", "email_verified": True},
    "judge-b": {"sub": "uid-judge-b", "email": "b@hack2skill.com", "email_verified": True},
    "admin": {"sub": "uid-admin", "email": "admin@anvaya.test", "email_verified": True},
    "browser": {"sub": "uid-browser", "email": "browser@hack2skill.com", "email_verified": True},
}
A, B, ADMIN = core.user_key("uid-judge-a"), core.user_key("uid-judge-b"), core.user_key("uid-admin")
DOC_JSON = '{"document_type": "x", "is_expected_type": true, "matches_expected_person": true, "confidence": 0.9}'
COLLECTIONS = (keys.ACCESS, keys.POLICY, "key_usage", "model_access_tests", keys.PLATFORM_STATUS[0])


def err(code: int, message: str = "error") -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": message, "status": "X"}})


class Fake:
    """Stand-in Gemini clients, one per key, recording which key each call used."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, Exception] = {}

    def client(self, name: str):
        fake = self

        class Models:
            async def generate_content(self, *, model, contents, config=None):
                fake.calls.append((name, model))
                if name in fake.fail:
                    raise fake.fail[name]
                return SimpleNamespace(parsed=None, text=DOC_JSON, usage_metadata=SimpleNamespace(
                    prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0))

        @contextlib.asynccontextmanager
        async def connect(**_):
            fake.calls.append((name, "live"))
            if name in fake.fail:
                raise fake.fail[name]
            yield SimpleNamespace(receive=self._never)

        return SimpleNamespace(name=name, aio=SimpleNamespace(models=Models(), live=SimpleNamespace(connect=connect)))

    @staticmethod
    async def _never():
        await asyncio.sleep(3600)
        yield

    def used(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        for c in COLLECTIONS:
            for d in store.list(c):
                store.delete(c, d["id"])
        current = store.get("platform_settings", "settings") or {"id": "settings"}
        store.put("platform_settings", {**current, "kill_switch": "off", keys.DEFAULT_SETTING: None})
        with keys._cache_lock:
            keys._cache.clear()
            keys._status.clear()
        gemini._own_clients.clear()
        core.set_current_user(None)
    wipe()
    yield
    wipe()


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(gemini, "client", lambda: f.client("platform"))
    monkeypatch.setattr(gemini, "make_client", lambda kind, key: f.client(NAMES.get(key, "other")))
    monkeypatch.setattr(model_access, "make_client", lambda kind, key: f.client(NAMES.get(key, "other")))
    return f


@pytest.fixture()
def client(monkeypatch):
    def fake_verify(token, request, audience=None):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    monkeypatch.setattr(auth.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["@hack2skill.com", "@anvaya.test"])
    monkeypatch.setattr(settings, "platform_roles", {"admin@anvaya.test": "platform_admin"})
    monkeypatch.setattr(settings, "platform_default_role", "viewer")
    return TestClient(main.app)


def h(who):
    return {"Authorization": f"Bearer {who}", demo.ACTING_HEADER: "vikram"}


def ask(user: str | None, agent: str = "saathi.chat"):
    """One structured model call caused by `user`."""
    core.set_current_user(user)
    return asyncio.run(gemini.generate_json(schema=DocumentCheck, prompt="p", agent=agent))


def everything_stored() -> str:
    return json.dumps(store._data, default=str)


# ---- Testing and saving a key -------------------------------------------------------------


def test_testing_a_key_stores_nothing_and_shows_what_works(client, fake):
    client.get("/api/me", headers=h("judge-a"))
    r = client.post("/api/model-access/test", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usable"] and {f["id"] for f in body["features"]} == {"chat", "handover", "voice", "speech", "fraud"}
    assert all(f["result"] == "works" for f in body["features"])
    assert set(fake.used()) == {"a"}  # test calls use only the key being tested, never ours
    assert store.get(keys.ACCESS, A) is None and KEY_A not in r.text and KEY_A not in everything_stored()


def test_each_feature_reports_why_it_doesnt_work(fake):
    for code, result in ((401, "refused"), (403, "refused"), (404, "not_available"), (429, "out_of_quota")):
        fake.fail["a"] = err(code)
        found, usable = asyncio.run(model_access.test_key("studio", KEY_A))
        assert not usable and {f["result"] for f in found} == {result}, code
    fake.fail["a"] = err(400, "API key not valid. Please pass a valid API key.")
    assert {f["result"] for f in asyncio.run(model_access.test_key("studio", KEY_A))[0]} == {"refused"}
    # The live API closes the connection with a WebSocket code and the same words.
    fake.fail["a"] = errors.APIError(1007, {"message": None, "status": "None. API key not valid. Please pass a valid API key."})
    assert {f["result"] for f in asyncio.run(model_access.test_key("studio", KEY_A))[0]} == {"refused"}
    assert keys.fails_over(err(1011, "You exceeded your current quota"))
    assert not keys.fails_over(err(500, "internal"))


def test_saving_tests_again_and_stores_the_key_encrypted(client, fake):
    client.get("/api/me", headers=h("judge-a"))
    r = client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert r.status_code == 200, r.text
    assert r.json()["access"]["last4"] == "••••aaa1" and KEY_A not in r.text
    doc = store.get(keys.ACCESS, A)
    assert doc["secret"] != KEY_A and KEY_A not in json.dumps(doc)
    got = client.get("/api/model-access", headers=h("judge-a")).json()
    assert got["access"]["status"] == "working" and "secret" not in json.dumps(got) and KEY_A not in json.dumps(got)


def test_a_key_that_reaches_nothing_is_not_saved(client, fake):
    fake.fail["bad"] = err(400, "API key not valid. Please pass a valid API key.")
    r = client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_BAD})
    assert r.status_code == 422 and store.get(keys.ACCESS, A) is None


def test_a_bad_key_is_never_repeated_back(client, fake):
    short = "AIzashort"
    r = client.post("/api/model-access/test", headers=h("judge-a"), json={"kind": "studio", "key": short})
    assert r.status_code == 400 and short not in r.text


def test_tests_are_limited_per_minute(client, fake):
    for _ in range(keys.TESTS_PER_MINUTE):
        assert client.post("/api/model-access/test", headers=h("judge-a"),
                           json={"kind": "studio", "key": KEY_A}).status_code == 200
    r = client.post("/api/model-access/test", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert r.status_code == 400 and "Too many" in r.text
    # Someone else isn't affected.
    assert client.post("/api/model-access/test", headers=h("judge-b"),
                       json={"kind": "studio", "key": KEY_B}).status_code == 200


def test_saving_is_refused_when_the_server_has_no_kms_key_in_production(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "store", "firestore")
    monkeypatch.setattr(settings, "model_keys_kms_key_name", "")
    assert client.get("/api/model-access", headers=h("judge-a")).json()["ready"] is False
    r = client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert r.status_code == 400 and "set up" in r.text and store.get(keys.ACCESS, A) is None


def test_a_person_removes_their_own_key(client, fake):
    client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert client.delete("/api/model-access", headers=h("judge-a")).json() == {"access": None}
    assert store.get(keys.ACCESS, A) is None


def test_resetting_the_demo_keeps_the_key_until_it_is_removed(client, fake):
    """Reset clears only the demo copy's data; the key belongs to the account."""
    client.get("/api/me", headers=h("judge-a"))
    client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert client.post("/api/demo/seed", headers=h("judge-a")).status_code == 200
    assert client.get("/api/model-access", headers=h("judge-a")).json()["access"]["last4"] == "••••aaa1"


# ---- Each person's key is theirs alone ------------------------------------------------------


def test_two_people_at_once_each_use_only_their_own_key(fake):
    keys.save(A, "studio", KEY_A, [])
    keys.save(B, "studio", KEY_B, [])

    async def as_person(user):
        core.set_current_user(user)  # each task has its own context, like each request
        await asyncio.sleep(0)
        await gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt="p", agent="saathi.chat")
        return keys.chain()[0][2]

    async def both():
        return await asyncio.gather(*[as_person(u) for u in (A, B, A, B)])

    assert asyncio.run(both()) == [KEY_A, KEY_B, KEY_A, KEY_B]
    assert sorted(fake.used()) == ["a", "a", "b", "b"]


def test_a_person_without_a_key_uses_the_platform_key(fake):
    keys.save(A, "studio", KEY_A, [])
    ask(B)
    assert fake.used() == ["platform"]
    assert [s for s, _, _ in keys.chain()] == ["platform"]


def test_a_stored_key_copied_to_someone_else_never_decrypts():
    keys.save(A, "studio", KEY_A, [])
    with pytest.raises(ValueError):
        keys._decrypt(B, store.get(keys.ACCESS, A)["secret"])
    store.put(keys.ACCESS, {**store.get(keys.ACCESS, A), "id": B, "user_key": B})
    assert keys.own_key(B) is None  # B's calls use our key, never A's


def test_an_unencrypted_record_is_never_trusted_in_production(monkeypatch):
    keys.save(A, "studio", KEY_A, [])
    monkeypatch.setattr(settings, "store", "firestore")
    with pytest.raises(ValueError):
        keys._decrypt(A, store.get(keys.ACCESS, A)["secret"])


def test_a_vertex_express_key_builds_a_vertex_client_and_only_then():
    assert gemini.make_client("vertex_express", KEY_A)._api_client.vertexai is True
    assert gemini.make_client("studio", KEY_A)._api_client.vertexai is False
    assert core.on_vertex() is False  # the platform itself stays on the Gemini API key path


# ---- Ours only as failover -----------------------------------------------------------------


def test_a_key_out_of_quota_fails_over_to_ours_and_is_marked(fake):
    keys.save(A, "studio", KEY_A, [])
    fake.fail["a"] = err(429)
    ask(A)
    assert fake.used() == ["a", "platform"]
    assert store.get(keys.ACCESS, A)["status"] == "failing"
    moved = [b for b in store.list("gateway_blocked") if b["kind"] == "key_fallback" and b["user_key"] == A]
    assert moved and KEY_A not in json.dumps(moved)
    day = store.get("usage_days", core._today())
    assert day["key_sources"]["platform"]["calls"] >= 1
    del fake.fail["a"]
    ask(A)
    assert fake.used()[-1] == "a" and store.get(keys.ACCESS, A)["status"] == "working"


def test_ours_is_never_tried_before_a_working_key(fake):
    keys.save(A, "studio", KEY_A, [])
    ask(A)
    ask(A, agent="readiness.planner")
    assert fake.used() == ["a", "a"]


def test_a_server_error_does_not_move_to_our_key(fake, monkeypatch):
    keys.save(A, "studio", KEY_A, [])
    fake.fail["a"] = errors.ServerError(500, {"error": {"code": 500, "message": "boom", "status": "INTERNAL"}})
    with pytest.raises(errors.APIError):
        ask(A)
    assert "platform" not in fake.used()


def test_a_safety_check_still_runs_when_the_first_key_is_out_of_quota(fake):
    keys.save(A, "studio", KEY_A, [])
    fake.fail["a"] = err(429)
    assert ask(A, agent="activation.checker").is_expected_type
    assert fake.used() == ["a", "platform"]


def test_turning_a_person_off_stops_their_key_being_used(client, fake):
    keys.save(A, "studio", KEY_A, [])
    keys.set_policy(A, False, list(keys.KINDS))
    ask(A)
    assert fake.used() == ["platform"]
    r = client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    assert r.status_code == 400 and "turned off" in r.text


def test_calls_on_a_persons_own_key_are_not_counted_against_their_allowance(fake):
    keys.save(A, "studio", KEY_A, [])
    before = store.get("user_usage", f"{core._today()}:{A}")
    ask(A)
    assert store.get("user_usage", f"{core._today()}:{A}") == before
    assert store.get("key_usage", f"{core._today()}:{A}")["judge_studio"]["calls"] == 1
    store.update("platform_settings", "settings", {"kill_switch": "all_paused"})
    core.set_current_user(A)
    with pytest.raises(core.Refused):  # the kill switch still applies on their own key
        core.check_call("saathi.chat", core.primary_model("saathi.chat"))


def test_a_fraud_run_uses_its_starters_key_after_the_request_has_finished(monkeypatch):
    monkeypatch.setattr(fraud_guides, "current", lambda key: None)
    store.delete(*fraud_pipeline.LOCK)  # a run another test left behind
    run = fraud_pipeline.start_run("dream", "any-guide", ADMIN)
    core.set_current_user(None)  # the request that started it has finished

    async def go():
        await fraud_pipeline.dream(run)
        return core.current_user()

    assert asyncio.run(go()) == ADMIN


def test_the_handover_assistant_fails_over_too(monkeypatch):
    keys.save(A, "studio", KEY_A, [])
    monkeypatch.setattr(gemini, "client", lambda: genai.Client(api_key=PLATFORM_KEY))
    used = []

    seen = []

    async def fake_generate(self, llm_request, stream=False):
        name = "a" if self.client._api_client.api_key == KEY_A else "platform"
        used.append(name)
        seen.append(len(llm_request.contents))
        llm_request.contents.append(types.Content(role="user", parts=[types.Part(text="edited")]))  # as ADK does
        if name == "a":
            raise err(429)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))

    monkeypatch.setattr(Gemini, "generate_content_async", fake_generate)
    first = core.primary_model("handover.assistant")
    model = executor_agent.KeyedGemini(model=first, retry_options=gemini.retry_for("handover.assistant"))
    request = LlmRequest(model=first, config=types.GenerateContentConfig(),
                         contents=[types.Content(role="user", parts=[types.Part(text="Where are the papers?")])])

    async def run():
        core.set_current_user(A)
        return [r async for r in model.generate_content_async(request)]

    responses = asyncio.run(run())
    assert used == ["a", "platform"] and responses[0].custom_metadata["key_source"] == "platform"
    assert seen == [1, 1]  # the second key got the request as it was, not the first attempt's edits
    # The last key keeps the registry's retries; a key with another left after it doesn't wait out a 429.
    assert 429 in request.config.http_options.retry_options.http_status_codes


def test_the_live_call_opens_on_the_next_key_when_the_first_is_refused(fake, monkeypatch):
    keys.save(A, "studio", KEY_A, [])
    fake.fail["a"] = err(403)
    monkeypatch.setattr(live, "client_for", lambda kind, key: fake.client(NAMES.get(key, "platform")))
    original = core.limit
    monkeypatch.setattr(core, "limit", lambda a, k, d=None: 0.001 if k == "max_call_minutes" else original(a, k, d))
    monkeypatch.setattr(live.loops, "live_instructions", lambda *a, **k: "")
    sent = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, data): sent.append(data)
        async def send_bytes(self, data): pass
        async def receive(self):
            await asyncio.sleep(3600)
        async def close(self): pass

    core.set_current_user(A)
    asyncio.run(live.call(FakeWS(), {"id": "o1", "name": "Vikram"}, lambda t: t))
    assert fake.used() == ["a", "platform"] and {"type": "ready"} in sent


# ---- Nobody else sees a key -----------------------------------------------------------------


def test_a_key_never_reaches_a_response_log_record_or_the_audit_log(client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/api/model-access/test", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    fake.fail["a"] = err(429, f"quota exceeded for {KEY_A}")
    ask(A)
    logging.getLogger("anything").warning("a library logs the key %s", KEY_A)
    responses = [client.get("/api/model-access", headers=h("judge-a")).text,
                 client.get("/api/platform/model-keys", headers=h("admin")).text,
                 client.post(f"/api/platform/model-keys/{A}/test", headers=h("admin"), json={"reason": "check"}).text,
                 client.get("/api/platform/audit", headers=h("admin")).text,
                 client.get("/api/platform/agents", headers=h("admin")).text]
    detail = asyncio.run(main.gemini_error(None, err(400, f"API key not valid: {KEY_A}"))).body.decode()
    for text in [*responses, caplog.text, everything_stored(), detail]:
        assert KEY_A not in text


def test_platform_admins_see_everyones_usage_but_never_a_key(client, fake):
    for who, key in (("judge-a", KEY_A), ("judge-b", KEY_B)):
        client.get("/api/me", headers=h(who))
        client.put("/api/model-access", headers=h(who), json={"kind": "studio", "key": key})
    ask(A)
    r = client.get("/api/platform/model-keys", headers=h("admin"))
    rows = {p["user_key"]: p for p in r.json()["people"]}
    assert {A, B} <= set(rows) and rows[A]["access"]["last4"] == "••••aaa1"
    assert rows[A]["usage_30d"]["judge_studio"]["calls"] >= 1
    assert r.json()["totals"]["judge_studio"]["calls"] >= 1
    assert KEY_A not in r.text and KEY_B not in r.text and "secret" not in r.text


def test_the_services_own_work_counts_in_the_totals_but_is_not_a_person(client, fake):
    core.set_current_user("service:fraud-scheduler")  # how the hourly fraud cycle runs
    core.record_usage("fraud.researcher", "gemini-3.8-flash", "direct", tokens_in=50, tokens_out=5)
    core.set_current_user(None)
    page = client.get("/api/platform/model-keys", headers=h("admin")).json()
    assert "service:fraud-scheduler" not in {p["user_key"] for p in page["people"]}
    assert page["service"]["platform"]["calls"] >= 1 and page["totals"]["platform"]["calls"] >= 1
    assert core.is_service("service") and core.is_service("service:fraud-scheduler") and not core.is_service("u_1")


def test_a_demo_user_sees_only_their_own_row(client, fake):
    for who, key in (("judge-a", KEY_A), ("judge-b", KEY_B)):
        client.get("/api/me", headers=h(who))
        client.put("/api/model-access", headers=h(who), json={"kind": "studio", "key": key})
    r = client.get("/api/platform/model-keys", headers=h("judge-a"))
    assert [p["user_key"] for p in r.json()["people"]] == [A] and B not in r.text
    assert client.get("/api/platform/model-keys", headers=h("browser")).json()["people"] == []


def test_only_platform_admins_change_test_or_remove_a_key_and_each_change_is_audited(client, fake):
    client.get("/api/me", headers=h("judge-a"))
    client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    policy = {"enabled": False, "kinds": ["studio"], "reason": "testing"}
    for path, body in ((f"/model-keys/{A}/policy", policy), (f"/model-keys/{A}/test", {"reason": "check"}),
                       (f"/model-keys/{A}/remove", {"reason": "check"}), ("/model-keys/default", policy)):
        assert client.post(f"/api/platform{path}", headers=h("judge-b"), json=body).status_code == 403, path
    assert client.post(f"/api/platform/model-keys/{A}/test", headers=h("admin"),
                       json={"reason": "check"}).json()["usable"] is True
    assert client.post(f"/api/platform/model-keys/{A}/policy", headers=h("admin"), json=policy).json()["enabled"] is False
    assert client.post(f"/api/platform/model-keys/{A}/remove", headers=h("admin"),
                       json={"reason": "asked to"}).json() == {"access": None}
    entries = [e for e in store.list("platform_audit") if e["action"].startswith("model_keys.")]
    assert {e["action"] for e in entries} >= {"model_keys.test", "model_keys.policy", "model_keys.remove"}
    assert all(e["reason"] for e in entries) and KEY_A not in json.dumps(entries)


def test_the_default_policy_applies_to_everyone_without_their_own(client, fake):
    r = client.post("/api/platform/model-keys/default", headers=h("admin"),
                    json={"enabled": True, "kinds": ["studio"], "reason": "studio only"})
    assert r.json() == {"enabled": True, "kinds": ["studio"]}
    r = client.put("/api/model-access", headers=h("judge-a"), json={"kind": "vertex_express", "key": KEY_A})
    assert r.status_code == 400 and "isn't allowed" in r.text


# ---- Found in review --------------------------------------------------------------------------


def test_the_allowance_applies_when_a_call_falls_back_to_our_key(fake):
    keys.save(A, "studio", KEY_A, [])
    store.put("user_usage", {"id": f"{core._today()}:{A}", "day": core._today(), "user_key": A,
                             "owner": {"tokens": 10_000_000}})
    ask(A)  # their own key answers: the allowance doesn't apply
    fake.fail["a"] = err(429)
    with pytest.raises(core.Refused):  # their key is out of quota, and today's allowance on ours is used
        ask(A)
    assert "platform" not in fake.used()


def test_a_server_error_on_connect_does_not_move_a_live_call_to_our_key(fake, monkeypatch):
    keys.save(A, "studio", KEY_A, [])
    fake.fail["a"] = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    monkeypatch.setattr(live, "client_for", lambda kind, key: fake.client(NAMES.get(key, "platform")))
    monkeypatch.setattr(live.loops, "live_instructions", lambda *a, **k: "")
    sent = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, data): sent.append(data)
        async def close(self): pass

    core.set_current_user(A)
    asyncio.run(live.call(FakeWS(), {"id": "o1", "name": "Vikram"}, lambda t: t))
    assert fake.used() == ["a"] and any(m.get("type") == "error" for m in sent)
    assert store.get(keys.ACCESS, A)["status"] == "working"


def test_a_key_in_a_logged_traceback_is_removed(caplog):
    caplog.set_level(logging.ERROR)
    try:
        raise RuntimeError(f"connect failed: wss://example.test/live?key={KEY_A}")
    except RuntimeError:
        logging.getLogger("app.live").exception("live call failed")
    assert KEY_A not in caplog.text and "[key removed]" in caplog.text


def test_an_admins_test_of_a_key_counts_as_its_owners_use(client, fake):
    client.get("/api/me", headers=h("judge-a"))
    client.put("/api/model-access", headers=h("judge-a"), json={"kind": "studio", "key": KEY_A})
    before = (store.get("key_usage", f"{core._today()}:{A}") or {}).get("judge_studio", {}).get("calls", 0)
    client.post(f"/api/platform/model-keys/{A}/test", headers=h("admin"), json={"reason": "check"})
    assert store.get("key_usage", f"{core._today()}:{A}")["judge_studio"]["calls"] > before
    assert "judge_studio" not in (store.get("key_usage", f"{core._today()}:{ADMIN}") or {})
