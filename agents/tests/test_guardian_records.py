"""The guardian records what it does (PR-15.6): a dropped next step, a reviewer's override of a
"not valid" check, a call refused by an allowance or the daily ceiling, and warning signs a
certificate check found. Each writes one open event, with fixed text from code only: no
conversation text, no names, no reviewer's reason. A repeat writes nothing more. The portal lists
them, and a demo admin sees only their own copy's and the whole service's."""

import pytest

from app import main, release, tasks
from app.governance import core, portal
from app.store import store

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


def test_an_allowance_refusal_is_recorded_once_a_day():
    store.put("user_usage", {"id": f"{core._today()}:{PERSON}", "day": core._today(), "user_key": PERSON,
                             "owner": {"tokens": core.allowances()["owner"]["tokens_per_day"]}})
    refused("saathi.chat")
    refused("saathi.chat")
    event = only_event()
    assert (event["agent"], event["response"], event["user_key"]) == ("saathi.chat", "pause", PERSON)
    assert event["reason"] == "Daily allowance reached: owner.tokens"


def test_a_ceiling_refusal_is_recorded_once_a_day_for_the_whole_service():
    cap = core.service_ceiling()["tokens_per_day"]
    store.put("service_usage", {"id": core._today(), "day": core._today(), "tokens": cap})
    refused("saathi.chat")
    core.set_current_user(OTHER)
    refused("saathi.chat")
    event = only_event()
    assert event["response"] == "pause" and event["user_key"] is None
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


def test_an_override_is_recorded_without_the_reviewer_or_their_reason():
    case = release.confirm_death(_case(certificate_check={"verdict": "not_valid"}), "Anjali",
                                 "Seal and signature checked with the registrar's office")
    event = only_event()
    assert (event["agent"], event["response"], event["case_key"]) == ("activation.checker", "add_check", case["id"])
    assert event["reason"] == "A reviewer went against the AI's 'not valid' on the death certificate"
    assert "Anjali" not in str(event) and "registrar" not in str(event)

    release.attach_authority_check(case, {"verdict": "not_valid"})
    release.confirm_authority(case, "Rahul", "Succession certificate seen in original at the bank")
    assert len(events()) == 2 and "Rahul" not in str(events())


def test_no_override_means_no_event():
    release.confirm_death(_case(certificate_check={"verdict": "likely_valid"}), "Anjali")
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


def test_the_portal_lists_events_and_a_demo_admin_sees_only_their_own_and_the_services():
    mine = core.record_intervention("saathi.chat", "pause", "Daily allowance reached: owner.tokens")
    theirs = core.record_intervention("saathi.chat", "pause", "Daily allowance reached: owner.tokens", user_key=OTHER)
    service = core.record_intervention("saathi.voice", "pause", "Daily service ceiling reached", user_key=None)

    team = core.Actor(email="team@example.com", key="u_team", role="risk_reviewer", permissions=[])
    judge = core.Actor(email="judge@example.com", key=PERSON, role="demo_admin", permissions=["approve_budget"])
    assert {e["id"] for e in portal.get_guardian(team)["events"]} == {mine["id"], theirs["id"], service["id"]}
    assert {e["id"] for e in portal.get_guardian(judge)["events"]} == {mine["id"], service["id"]}
    assert portal.overview(who=judge)["open_interventions"] == 2
