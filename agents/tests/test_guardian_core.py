"""The guardian core (PR-2.7, part 1): each action that asks it is written down first, with no
text; detectors share one format; the policy file turns risk into one response, and changing the
file changes the behaviour; a rule fixed in code can only be made stricter; and the guardian never
touches the vault (PR-2.7.5)."""

import ast
import pathlib
import re

import pytest
import yaml

from app import main, step_guard, tasks
from app.governance import core, guardian
from app.store import store

ME = "u_guardcore1"
ACTION_FIELDS = {"id", "at", "day", "expire_at", "user_key", "action", "agent", "autonomy", "channel", "refs",
                 "risk", "response", "detectors"}


@pytest.fixture(autouse=True)
def fresh():
    for collection in ("guardian_actions", "guardian_events", "steps_not_pursued", "tasks", "user_usage"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    core.set_current_user(ME)
    yield
    core.set_current_user(None)
    guardian.policy.cache_clear()


def with_policy(monkeypatch, **actions):
    """The policy file as it is, with these actions' levels changed."""
    data = yaml.safe_load(guardian.POLICY_PATH.read_text())
    for action, levels in actions.items():
        data["actions"][action] = levels
    monkeypatch.setattr(guardian, "policy", lambda: guardian.Policy.model_validate(data))


def actions() -> list[dict]:
    return store.list("guardian_actions")


def test_each_action_is_written_down_with_ids_only_never_the_facts():
    decision = guardian.decide("certificate_check", "activation.checker",
                               {"signs_found": [], "what": "certificate", "document_text": "Vikram Rao, died 2026"},
                               refs={"case": "case_1"})
    [record] = actions()
    assert set(record) == ACTION_FIELDS and decision.action_id == record["id"]
    assert (record["user_key"], record["refs"], record["autonomy"], record["channel"]) == \
        (ME, {"case": "case_1"}, "assist", "app")
    assert (record["risk"], record["response"]) == ("none", "continue")
    assert "Vikram" not in str(record)
    assert store.list("guardian_events") == []  # continue: nothing for a reviewer


def test_the_policy_file_turns_risk_into_a_response(monkeypatch):
    found = {"signs_found": ["s_qr"], "what": "certificate"}
    assert guardian.decide("certificate_check", "activation.checker", found).response == "add_check"
    with_policy(monkeypatch, certificate_check={"medium": "pause"})
    decision = guardian.decide("certificate_check", "activation.checker", found)
    assert (decision.response, decision.goes_ahead) == ("pause", False)
    assert [e["response"] for e in store.list("guardian_events")] == ["add_check", "pause"]


def test_a_rule_fixed_in_code_cant_be_loosened_by_the_file(monkeypatch):
    with_policy(monkeypatch, next_step={"high": "continue"})
    assert guardian.decide("next_step", "readiness.planner", {"rule": "secret"}).response == "block"


def test_a_new_detector_plugs_in_with_the_same_format(monkeypatch):
    """A Gemini signal would be written like this: facts in, a risk and fixed words out."""
    def pressure(facts):
        return guardian.Finding("pressure_signal", facts["level"], "Signs of pressure in the conversation")

    monkeypatch.setitem(guardian.DETECTORS, "certificate_check", [*guardian.DETECTORS["certificate_check"], pressure])
    assert guardian.decide("certificate_check", "activation.checker", {"level": "low"}).response == "continue"
    decision = guardian.decide("certificate_check", "activation.checker", {"level": "high"})
    assert decision.response == "pause" and [f.detector for f in decision.findings] == ["pressure_signal"]
    assert store.list("guardian_events")[0]["reason"] == "Signs of pressure in the conversation"


@pytest.mark.parametrize("change", [
    {"actions": {"next_step": {}, "model_call": {}, "review_confirmation": {}, "certificate_check": {}, "unknown": {}}},
    {"actions": {"next_step": {}}},                                  # an action left out
    {"default": {"none": "continue", "low": "continue", "medium": "add_check"}},  # a level left out
    {"default": {"none": "continue", "low": "continue", "medium": "add_check", "high": "explode"}},
    {"version": 2},
])
def test_a_wrong_policy_file_stops_start_up(change):
    data = {**yaml.safe_load(guardian.POLICY_PATH.read_text()), **change}
    with pytest.raises(ValueError):
        guardian.Policy.model_validate(data)


def test_every_next_step_asks_the_guardian_and_only_a_dropped_one_is_an_event():
    keep = {"title": "Record the nominee for the LIC policy", "why_it_matters": "So the claim goes smoothly.",
            "action": "institution", "question_for_owner": "Who is the nominee?", "steps": ["Ask at the branch"],
            "severity": "high"}
    risky = {**keep, "title": "Share the OTP with the Paytm agent", "question_for_owner": "What's the OTP?"}
    tasks.refresh("owner_guardcore", [keep, risky])
    assert sorted(a["response"] for a in actions() if a["action"] == "next_step") == ["block", "continue"]
    [event] = store.list("guardian_events")
    assert event["response"] == "block" and "OTP" not in str(actions()) + str(event)
    assert [t["title"] for t in tasks.open_tasks("owner_guardcore")] == [keep["title"]]


def test_a_ceiling_refusal_is_the_askers_action_and_the_services_event():
    store.put("service_usage", {"id": core._today(), "day": core._today(), "tokens": core.service_ceiling()["tokens_per_day"]})
    with pytest.raises(core.Refused):
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    [record] = actions()
    assert (record["action"], record["user_key"], record["response"]) == ("model_call", ME, "pause")
    assert store.list("guardian_events")[0]["user_key"] == core.SERVICE
    store.delete("service_usage", core._today())


def test_the_guardian_never_reaches_the_vault():
    """PR-2.7.5: it imports only the store, the governance core and libraries, and names no vault data."""
    tree = ast.parse(pathlib.Path(guardian.__file__).read_text())
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "pathlib", "time", "collections.abc", "dataclasses", "datetime", "functools",
                        "typing", "yaml", "pydantic", "store", ""}, imported
    tree.body = tree.body[1:]  # the code only, without the module's explanation (comments are dropped too)
    code = ast.unparse(tree)
    for forbidden in ("vault", "escrow", "media", "knowledge"):
        assert forbidden not in code, forbidden


def test_the_container_has_every_policy_file_the_api_reads_at_start_up():
    """The Dockerfile copies files by name; a policy file left out stops the API from starting."""
    agents = pathlib.Path(__file__).resolve().parents[1]
    copied = re.search(r"^COPY ((?:\S+\.yaml ?)+) \./$", (agents / "Dockerfile").read_text(), re.M).group(1).split()
    assert {p.name for p in agents.glob("*.yaml")} <= set(copied)


def test_the_release_expires_the_guardians_records_too():
    repo = pathlib.Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "deploy.yml").read_text())
    run = next(s["run"] for s in workflow["jobs"]["api"]["steps"] if "ttls update" in s.get("run", ""))
    assert "guardian_actions" in re.search(r"for group in ([\w ]+); do", run).group(1).split()
    guardian.decide("certificate_check", "activation.checker", {"signs_found": [], "what": "certificate"})
    assert "expire_at" in actions()[0]


STEP = {"title": "Record the nominee", "why_it_matters": "w", "action": "institution",
        "question_for_owner": "q", "steps": ["s"], "severity": "high"}


def test_the_step_guard_obeys_the_guardian_and_counts_what_it_holds_back(monkeypatch):
    """A step the step guard allows still doesn't reach the owner if the guardian says no."""
    monkeypatch.setitem(guardian.DETECTORS, "next_step",
                        [lambda facts: guardian.Finding("test_rule", "medium", "Held for a test", floor="pause")])
    assert step_guard.pursue("owner_guardcore", [STEP]) == []
    assert [s["rule"] for s in store.list("steps_not_pursued")] == ["guardian:pause"]


def test_repeats_replace_their_record_instead_of_adding_one():
    for _ in range(3):
        step_guard.pursue("owner_guardcore", [STEP])
    assert len([a for a in actions() if a["action"] == "next_step"]) == 1
    store.put("user_usage", {"id": f"{core._today()}:{ME}", "day": core._today(), "user_key": ME,
                             "owner": {"tokens": core.allowances()["owner"]["tokens_per_day"]}})
    for _ in range(3):
        with pytest.raises(core.Refused):
            core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    assert len([a for a in actions() if a["action"] == "model_call"]) == 1


def test_a_live_call_is_recorded_as_one():
    core.set_channel("live_call")
    try:
        store.put("user_usage", {"id": f"{core._today()}:{ME}", "day": core._today(), "user_key": ME,
                                 "owner": {"voice_minutes": core.allowances()["owner"]["voice_minutes_per_day"]}})
        with pytest.raises(core.Refused):
            core.check_call("saathi.voice", "gemini-3.8-live")  # refused as the call starts
        assert actions()[0]["channel"] == "live_call"
    finally:
        core.set_channel("app")


def test_a_failed_record_never_turns_a_saved_confirmation_into_an_error(monkeypatch):
    def broken(*_, **__):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(guardian, "decide", broken)
    main._record_override({"id": "case_1", "certificate_check": {"verdict": "not_valid"}}, "certificate_check",
                          "death certificate", "Checked with the registrar")  # logged, not raised
    main._record_signs({"id": "case_1"}, {"signs": [{"id": "s_qr", "result": "found"}]}, "certificate")
