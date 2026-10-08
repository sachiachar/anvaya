"""The guardian records what it does (PR-15.6): a dropped next step, a reviewer's override of a
"not valid" check, a call refused by an allowance or the daily ceiling, and warning signs a
certificate check found. Each writes one open event, with fixed text from code only: no
conversation text, no names, no reviewer's reason. A repeat writes nothing more. The portal lists
them, and a demo admin sees only their own copy's and the whole service's."""

import time

import pytest
from fastapi.testclient import TestClient

from app import demo, fraud_pipeline, main, tasks
from app.governance import core, portal
from app.store import new_id, store

PERSON = "u_guardian01"
OTHER = "u_guardian02"
FIELDS = {"id", "at", "agent", "response", "reason", "case_key", "user_key", "status"}


@pytest.fixture(autouse=True)
def fresh():
    for collection in ("guardian_events", "user_usage", "service_usage", "steps_not_pursued", "tasks"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    core.set_current_user(PERSON)
    yield
    core.set_current_user(None)


def events() -> list[dict]:
    return store.list("guardian_events")


def only_event() -> dict:
    [event] = events()
    assert set(event) == FIELDS and event["status"] == "open"
    return event


def refused(agent: str) -> None:
    with pytest.raises(core.Refused):
        core.check_call(agent, "gemini-3.1-flash-lite")


def use_tokens(amount: float) -> None:
    store.put("user_usage", {"id": f"{core._today()}:{PERSON}", "day": core._today(), "user_key": PERSON,
                             "owner": {"tokens": amount}})


def test_an_allowance_refusal_is_recorded_once_a_day():
    use_tokens(core.allowances()["owner"]["tokens_per_day"])
    refused("saathi.chat")
    refused("saathi.chat")
    event = only_event()
    assert (event["agent"], event["response"], event["user_key"]) == ("saathi.chat", "pause", PERSON)
    assert event["reason"] == "Daily allowance reached: owner.tokens"


def test_a_raised_allowance_that_runs_out_too_is_a_new_event():
    base = core.allowances()["owner"]["tokens_per_day"]
    use_tokens(base)
    refused("saathi.chat")
    store.put("budget_requests", {"id": new_id("req"), "at": time.time(), "tenant": "direct", "user_key": PERSON,
                                  "kind": "daily_allowance", "amount": 50, "hours": 24, "reason": "test",
                                  "status": "approved", "expires_at": time.time() + 3600})
    use_tokens(base + 50_000)
    refused("saathi.chat")
    assert len(events()) == 2
    for r in store.list("budget_requests", user_key=PERSON):
        store.delete("budget_requests", r["id"])


def test_a_ceiling_refusal_is_recorded_once_a_day_for_the_whole_service():
    cap = core.service_ceiling()["tokens_per_day"]
    store.put("service_usage", {"id": core._today(), "day": core._today(), "tokens": cap})
    refused("saathi.chat")
    core.set_current_user(OTHER)
    refused("saathi.chat")
    event = only_event()
    assert event["response"] == "pause" and event["user_key"] == core.SERVICE
    assert event["reason"] == "Daily service ceiling reached: service.tokens, all but safety-critical agents paused"


def test_a_dropped_step_is_recorded_once_without_its_words():
    risky = {"title": "Share the OTP with the Paytm agent", "why_it_matters": "He will finish it today.",
             "action": "institution", "question_for_owner": "What's the OTP?",
             "steps": ["Ask LIC for the form"], "severity": "high"}
    for _ in range(2):
        tasks.refresh("owner_guardian_test", [risky])
    event = only_event()
    assert (event["agent"], event["response"]) == ("readiness.planner", "block")
    assert event["reason"] == "A next step was not pursued: rule secret"
    assert "OTP" not in str(event) and "Paytm" not in str(event)


def _case(**checks) -> dict:
    return {"id": "case_guardian", "owner_id": "owner_guardian_test", "state": "reported", "history": [],
            "certificate_check": None, "authority_check": None, "wait_until": None, **checks}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    return c


def _confirm(client, step: str, reviewer: str, reason: str | None = None):
    case = store.list("cases", owner_id=demo.owner_id_for(core.user_key(demo.LOCAL)))[0]
    return client.post(f"/api/admin/cases/{case['id']}/{step}", json={"override_reason": reason},
                       headers={demo.ACTING_HEADER: reviewer})


def _open_case(certificate: dict, authority: dict | None = None) -> None:
    owner_id = demo.owner_id_for(core.user_key(demo.LOCAL))
    for c in store.list("cases", owner_id=owner_id):
        store.delete("cases", c["id"])
    store.put("cases", {**_case(certificate_check=certificate, authority_check=authority), "owner_id": owner_id})


def test_a_saved_override_is_recorded_without_the_reviewer_or_their_reason(client):
    _open_case({"verdict": "not_valid"})
    assert _confirm(client, "confirm-death", "anjali", "Seal and signature checked with the registrar's office").is_success
    event = only_event()
    assert (event["agent"], event["response"], event["case_key"]) == ("activation.checker", "add_check", "case_guardian")
    assert event["reason"] == "A reviewer went against the AI's 'not valid' on the death certificate"
    assert "Anjali" not in str(event) and "registrar" not in str(event)

    store.update("cases", "case_guardian", {"authority_check": {"verdict": "not_valid"}})
    assert _confirm(client, "confirm-authority", "rahul", "Succession certificate seen in original").is_success
    assert len(events()) == 2 and "Rahul" not in str(events())


def test_a_refused_or_ordinary_confirmation_records_nothing(client):
    _open_case({"verdict": "not_valid"})
    assert _confirm(client, "confirm-death", "anjali").status_code == 409  # no reason: refused, not saved
    _open_case({"verdict": "likely_valid"})
    assert _confirm(client, "confirm-death", "anjali", "No reason needed here at all").is_success
    assert events() == []


def test_warning_signs_found_are_recorded_by_their_ids_only():
    check = {"verdict": "needs_attention", "signs": [
        {"id": "s_font", "look_for": "Mixed fonts in the name", "result": "found", "note": "Name in a second font"},
        {"id": "s_seal", "look_for": "Seal missing", "result": "not_found", "note": ""},
        {"id": "s_qr", "look_for": "QR code doesn't match", "result": "found", "note": "Vikram Rao, 1956"}]}
    main._record_signs(_case(), check, "certificate")
    event = only_event()
    assert event["reason"] == "The certificate check found 2 warning signs: s_font, s_qr"
    assert event["case_key"] == "case_guardian" and "Vikram" not in str(event)

    main._record_signs(_case(), {"signs": [{"id": "s_seal", "result": "not_found"}]}, "certificate")
    assert len(events()) == 1  # nothing found, nothing recorded


def test_a_long_list_of_signs_keeps_the_reason_short():
    main._record_signs(_case(), {"signs": [{"id": f"s_{i}", "result": "found"} for i in range(9)]}, "certificate")
    assert only_event()["reason"] == "The certificate check found 9 warning signs: s_0, s_1, s_2, s_3, s_4 and 4 more"


def test_the_portal_lists_events_and_a_demo_admin_sees_only_their_own_and_the_services():
    mine = core.record_intervention("saathi.chat", "pause", "Daily allowance reached: owner.tokens")
    theirs = core.record_intervention("saathi.chat", "pause", "Daily allowance reached: owner.tokens", user_key=OTHER)
    service = core.record_intervention("saathi.voice", "pause", "Daily service ceiling reached", user_key=core.SERVICE)
    core.set_current_user(None)
    nobody = core.record_intervention("activation.checker", "add_check", "Recorded with no one signed in")
    older = store.put("guardian_events", {"id": "gev_old", "at": 1.0, "agent": "activation.checker",
                                          "response": "pause", "reason": "From before PR-15.6", "case_key": None,
                                          "status": "open"})

    team = core.Actor(email="team@example.com", key="u_team", role="risk_reviewer", permissions=[])
    judge = core.Actor(email="judge@example.com", key=PERSON, role="demo_admin", permissions=["approve_budget"])
    assert {e["id"] for e in portal.get_guardian(team)["events"]} == \
        {mine["id"], theirs["id"], service["id"], nobody["id"], older["id"]}
    assert {e["id"] for e in portal.get_guardian(judge)["events"]} == {mine["id"], service["id"]}
    assert portal.overview(who=judge)["open_interventions"] == 2


def test_the_fraud_dreamer_doesnt_see_the_daily_limit_refusals():
    use_tokens(core.allowances()["owner"]["tokens_per_day"])
    refused("saathi.chat")
    core.record_intervention("activation.checker", "add_check", "The certificate check found 1 warning sign: s_qr")
    assert [e["reason"] for e in fraud_pipeline._recent_events()] == ["The certificate check found 1 warning sign: s_qr"]
