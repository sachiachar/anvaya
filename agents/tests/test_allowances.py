"""Daily allowances per person (PR-10.4): counting, refusing at the limit, the protected
safety pool, approved grants, the daily reset, and the allowance endpoint."""

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.config import settings
from app.governance import core
from app.governance.seed import clear_samples as seed
from app.store import new_id, store

PERSON = "u_person0001"


@pytest.fixture(autouse=True)
def fresh():
    seed()
    core.set_current_user(PERSON)
    for doc in store.list("user_usage"):
        store.delete("user_usage", doc["id"])
    yield
    core.set_current_user(None)


def tokens_limit() -> int:
    return core.allowances()["owner"]["tokens_per_day"]


def use(pool: str, metric: str, amount: float, day: str | None = None) -> None:
    day = day or core._today()
    store.put("user_usage", {"id": f"{day}:{PERSON}", "day": day, "user_key": PERSON, pool: {metric: amount}})


def grant(kind: str, amount: int, expires_in: float) -> None:
    store.put("budget_requests", {"id": new_id("req"), "at": time.time(), "tenant": "direct", "user_key": PERSON,
                                  "kind": kind, "amount": amount, "hours": 24, "reason": "test",
                                  "status": "approved", "expires_at": time.time() + expires_in})


def test_each_call_counts_against_the_persons_allowance():
    core.record_usage("saathi.chat", "gemini-3.1-flash-lite", "direct", tokens_in=900, tokens_out=100)
    core.record_usage("saathi.voice", "gemini-3.8-live", "direct", minutes=2.5)
    core.record_usage("saathi.speech", "gemini-3.8-flash-lite-tts", "direct", tokens_in=10, tokens_out=200)
    owner = core.allowance_status()["owner"]
    assert owner["tokens"]["used"] == 1000
    assert owner["voice_minutes"]["used"] == 2.5
    assert owner["spoken_replies"]["used"] == 1


def test_safety_work_is_never_counted_or_limited():
    core.record_usage("activation.checker", "gemini-3.1-flash-lite", "direct", tokens_in=5000)
    assert store.get("user_usage", f"{core._today()}:{PERSON}") is None
    use("owner", "tokens", tokens_limit())  # the person's conversation allowance is used up
    core.check_call("activation.checker", "gemini-3.1-flash-lite")  # certificate checks still run


def test_calls_are_refused_once_the_allowance_is_used_and_it_is_recorded():
    use("owner", "tokens", tokens_limit())
    with pytest.raises(core.Refused, match="today's allowance"):
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    assert any(b["kind"] == "allowance" and b["user_key"] == PERSON for b in store.list("gateway_blocked"))


def test_an_approved_request_raises_the_allowance_until_it_expires():
    use("owner", "tokens", tokens_limit())
    grant("daily_allowance", 50, expires_in=3600)  # +50,000 tokens
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    for doc in store.list("budget_requests", user_key=PERSON):
        store.update("budget_requests", doc["id"], {"expires_at": time.time() - 1})
    with pytest.raises(core.Refused):
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")


def test_the_allowance_resets_each_day():
    use("owner", "tokens", tokens_limit(), day="2000-01-01")  # yesterday, long ago
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")


def test_voice_minutes_left_today_cap_the_next_call():
    use("owner", "voice_minutes", 28)
    assert core.remaining("saathi.voice") == pytest.approx(2)
    use("owner", "voice_minutes", 30)
    with pytest.raises(core.Refused):
        core.check_call("saathi.voice", core.primary_model("saathi.voice"))


def test_no_person_means_no_personal_allowance():
    core.set_current_user(None)  # scheduled jobs, for example
    assert core.remaining("saathi.chat") is None
    core.check_call("saathi.chat", "gemini-3.1-flash-lite")


def test_the_app_reads_the_signed_in_persons_allowance(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_firebase_token",
                        lambda token, request, audience=None: {"sub": "uid-1", "email": "a@anvaya.test",
                                                               "email_verified": True})
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "allowed_emails", ["@anvaya.test"])
    key = core.user_key("uid-1")
    store.put("user_usage", {"id": f"{core._today()}:{key}", "day": core._today(), "user_key": key,
                             "owner": {"tokens": tokens_limit() * 0.85}})
    body = TestClient(main.app).get("/api/allowance", headers={"Authorization": "Bearer t"}).json()
    assert body["pools"]["owner"]["tokens"]["state"] == "warning"
    assert body["pools"]["owner"]["voice_minutes"]["state"] == "ok"


def test_a_running_call_holds_its_minutes_so_a_second_call_cannot_double_them():
    left = core.remaining("saathi.voice")
    core.reserve("saathi.voice", left)  # first call holds all 30 minutes
    with pytest.raises(core.Refused):  # a second call at the same time is refused
        core.check_call("saathi.voice", core.primary_model("saathi.voice"))
    core.release("saathi.voice", left)  # first call ends after 4 minutes
    core.count_for_user("saathi.voice", minutes=4)
    assert core.remaining("saathi.voice") == pytest.approx(left - 4)


def test_counts_from_overlapping_calls_are_never_lost():
    import threading

    def one_call():
        core.set_current_user(PERSON)  # each thread is its own request
        core.count_for_user("saathi.chat", tokens=100)

    threads = [threading.Thread(target=one_call) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert core.allowance_status()["owner"]["tokens"]["used"] == 5000
