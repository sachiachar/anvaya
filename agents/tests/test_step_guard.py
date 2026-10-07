"""Never pursue a next step that could be part of a fraud (PR-13.3, docs/proposals/listening.md §6).
Checked in code: unknown means no; never a secret (except into the owner's own vault); an approved
tactic's tags block matching steps; a dropped step leaves a count with no words or names."""

import pytest

from app import fraud_guides, loops, step_guard, tasks
from app.fraud_guides import Exploits, Step, Tactic
from app.governance import portal
from app.store import store

OWNER = "owner_guard_test"
WILL = {"type": "legal_document", "channel": "registered_professional", "flags": [],
        "title": "Make a will for the Mysore flat", "why_it_matters": "So Sunita can have the flat without a dispute."}


def _gap(**changes):
    return {"title": "Record the nominee for the LIC policy", "why_it_matters": "So the claim goes to Sunita.",
            "action": "institution", "question_for_owner": "Who is the nominee on your LIC policy?",
            "steps": ["Ask LIC for the nominee change form at the branch"], "severity": "high", **changes}


@pytest.fixture()
def fee_tactic(monkeypatch):
    """A reviewer approved: 'pay a fee through an agent to release a claim'."""
    real = fraud_guides.current

    def current(key):
        guide = real(key)
        tactic = Tactic(id="t_fee", name="Release fee", steps=[Step(text="Agent asks for a fee", caught_by="gap")],
                        label="validated", trust="human-reviewed",
                        exploits=Exploits(types=["record_with_institution"], flags=["payment"]))
        return guide.model_copy(update={"tactics": [*guide.tactics, tactic]}) if guide else guide

    monkeypatch.setattr(fraud_guides, "current", current)


@pytest.fixture(autouse=True)
def clean():
    for collection in ("steps_not_pursued", "tasks"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])


def test_unknown_means_no():
    assert step_guard.check(WILL, []) is None
    assert step_guard.check({**WILL, "type": "send_money"}, []) == "unknown"
    assert step_guard.check({**WILL, "channel": "a man who called"}, []) == "unknown"
    assert step_guard.check({**WILL, "flags": ["urgent_please"]}, []) == "unknown"
    assert step_guard.check({"title": "Do something"}, []) == "unknown"


def test_never_a_secret_except_into_the_owners_own_vault():
    assert step_guard.check({**WILL, "flags": ["secret"]}, []) == "secret"
    assert step_guard.check({**WILL, "title": "Give the agent the OTP"}, []) == "secret"
    assert step_guard.check({**WILL, "title": "Tell Ajay your UPI PIN"}, []) == "secret"
    assert step_guard.check({**WILL, "title": "बैंक को ओटीपी बताएं"}, []) == "secret"
    vault = {"type": "add_to_vault", "channel": "anvaya_app", "flags": ["secret"],
             "title": "Add your email password to the vault"}
    assert step_guard.check(vault, []) is None  # encrypted on the device: where secrets belong
    assert step_guard.check({**vault, "channel": "family_named_by_owner"}, []) == "secret"
    assert step_guard.check({**WILL, "title": "Update the flat's address and PIN code"}, []) is None  # a postal code


def test_an_approved_tactic_blocks_matching_steps_only(fee_tactic):
    tactics = step_guard.approved_tactics()
    paying = {"type": "record_with_institution", "channel": "institution_official", "flags": ["payment"],
              "title": "Pay the processing charge to release the claim"}
    assert step_guard.check(paying, tactics) == "tactic:IN.death_certificate:t_fee"
    assert step_guard.check({**paying, "flags": []}, tactics) is None  # no payment: the tactic doesn't apply
    assert step_guard.check(WILL, tactics) is None


def test_an_unapproved_or_untagged_tactic_blocks_nothing(monkeypatch):
    def current(key):
        guide = fraud_guides.BUILT_IN.get(key)
        imagined = Tactic(id="t_x", name="Imagined", steps=[], label="simulated", trust="unverified",
                          exploits=Exploits(flags=["payment"]))
        untagged = Tactic(id="t_y", name="Untagged", steps=[], label="validated", trust="human-reviewed")
        return guide.model_copy(update={"tactics": [imagined, untagged]}) if guide else guide

    monkeypatch.setattr(fraud_guides, "current", current)
    assert step_guard.approved_tactics() == []


def test_a_dropped_step_never_reaches_the_owner_and_leaves_only_a_count(fee_tactic):
    tasks.refresh(OWNER, [_gap(), _gap(title="Pay the release fee to the agent", action="institution",
                                       why_it_matters="The money is held until a charge is paid.",
                                       flags=["payment"], question_for_owner="Have you paid the fee?"),
                          _gap(title="Share the OTP with the Paytm agent", why_it_matters="He will finish it today.",
                               question_for_owner="What's the OTP?")])
    assert [t["title"] for t in tasks.open_tasks(OWNER)] == ["Record the nominee for the LIC policy"]
    records = store.list("steps_not_pursued")
    assert sorted(r["rule"] for r in records) == ["secret", "tactic:IN.death_certificate:t_fee"]
    for r in records:
        assert set(r) == {"id", "at", "rule", "user_key"} and "OTP" not in str(r) and "LIC" not in str(r)
    assert portal.overview()["steps_not_pursued_30d"] == 2


def test_a_step_stored_before_a_tactic_was_approved_is_hidden_on_the_next_read(monkeypatch):
    tasks.refresh(OWNER, [_gap(title="Pay the processing charge", flags=["payment"])])
    assert len(tasks.open_tasks(OWNER)) == 1
    real = fraud_guides.current
    monkeypatch.setattr(fraud_guides, "current", lambda key: (g := real(key)) and g.model_copy(update={"tactics": [
        Tactic(id="t_fee", name="Fee", steps=[], label="validated", trust="human-reviewed",
               exploits=Exploits(flags=["payment"]))]}))
    assert tasks.open_tasks(OWNER) == []


def test_saathi_is_told_to_point_to_the_safer_way():
    rules = loops.persona({"name": "Vikram Rao", "family": []})
    assert "safer way" in rules and "Never name a fraud" in rules and "OTP" in rules
