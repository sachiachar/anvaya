"""Never pursue a next step that could be part of a fraud (PR-13.3).
Checked in code: unknown means no; never a secret (except into the owner's own vault); an approved
tactic's tags block matching steps; flags come from the step's words too; if the guides can't be
read nothing is pursued; a dropped step leaves a count with no words or names."""

import pytest
from fastapi.testclient import TestClient

from app import fraud_guides, loops, main, step_guard, tasks
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


def _with(*tactics):
    """The built-in guides plus these approved tactics."""
    return lambda: [g.model_copy(update={"tactics": [*g.tactics, *tactics]}) for g in fraud_guides.BUILT_IN.values()]


FEE = Tactic(id="t_fee", name="Release fee", steps=[Step(text="Agent asks for a fee", caught_by="gap")],
             label="validated", trust="human-reviewed",
             exploits=Exploits(types=["record_with_institution"], flags=["payment"]))


@pytest.fixture()
def fee_tactic(monkeypatch):
    """A reviewer approved: 'pay a fee through an agent to release a claim'."""
    monkeypatch.setattr(fraud_guides, "current_all", _with(FEE))


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


def test_plurals_and_the_vault_exception_cant_be_used_to_slip_a_secret_through():
    for text in ("Share your PINs with Ajay", "Write your passwords down for the agent", "Send the OTPs to the caller"):
        assert step_guard.check({**WILL, "title": text}, []) == "secret", text
    vault = {"type": "add_to_vault", "channel": "anvaya_app", "flags": []}
    assert step_guard.check({**vault, "title": "Give the agent your OTP"}, []) == "secret"
    assert step_guard.check({**vault, "title": "Add your net banking password to the vault"}, []) is None
    for text in ("Update the PIN-code on the address", "पता और पिन कोड बदलें"):
        assert step_guard.check({**WILL, "title": text}, []) is None, text


def test_an_approved_tactic_blocks_matching_steps_only(fee_tactic):
    tactics = step_guard.approved_tactics()
    paying = {"type": "record_with_institution", "channel": "institution_official", "flags": ["payment"],
              "title": "Clear the claim with the insurer"}
    assert step_guard.check(paying, tactics) == "tactic:IN.death_certificate:t_fee"
    assert step_guard.check({**paying, "flags": []}, tactics) is None  # no payment: the tactic doesn't apply
    assert step_guard.check(WILL, tactics) is None


def test_flags_come_from_the_words_too(fee_tactic):
    tactics = step_guard.approved_tactics()
    # A real readiness step has no flags; its words still show the payment.
    assert step_guard.check(step_guard.typed(_gap(title="Pay the release fee to the agent")), tactics) \
        == "tactic:IN.death_certificate:t_fee"
    assert step_guard.flags_of({"title": "Do it today, the man who called said"}) >= {"deadline", "started_by_other"}


def test_an_unapproved_or_untagged_tactic_blocks_nothing(monkeypatch):
    imagined = Tactic(id="t_x", name="Imagined", steps=[], label="simulated", trust="unverified",
                      exploits=Exploits(flags=["payment"]))
    untagged = Tactic(id="t_y", name="Untagged", steps=[], label="validated", trust="human-reviewed")
    monkeypatch.setattr(fraud_guides, "current_all", _with(imagined, untagged))
    assert step_guard.approved_tactics() == []


def test_a_typo_in_a_tactics_tags_is_refused():
    with pytest.raises(ValueError, match="unknown flags: payments"):
        Exploits(flags=["payments"])


def test_a_dropped_step_never_reaches_the_owner_and_leaves_only_a_count(fee_tactic):
    tasks.refresh(OWNER, [_gap(), _gap(title="Pay the release fee to the agent", action="institution",
                                       why_it_matters="The money is held until a charge is paid.",
                                       question_for_owner="Have you paid the fee?"),
                          _gap(title="Share the OTP with the Paytm agent", why_it_matters="He will finish it today.",
                               question_for_owner="What's the OTP?")])
    assert [t["title"] for t in tasks.open_tasks(OWNER)] == ["Record the nominee for the LIC policy"]
    records = store.list("steps_not_pursued")
    assert sorted(r["rule"] for r in records) == ["secret", "tactic:IN.death_certificate:t_fee"]
    for r in records:
        assert set(r) == {"id", "at", "rule", "user_key"} and "OTP" not in str(r) and "LIC" not in str(r)
    assert portal.overview()["steps_not_pursued_30d"] == 2


def test_a_step_stored_before_a_tactic_was_approved_is_hidden_on_the_next_read(monkeypatch):
    tasks.refresh(OWNER, [_gap(title="Pay the processing charge")])
    assert len(tasks.open_tasks(OWNER)) == 1
    monkeypatch.setattr(fraud_guides, "current_all", _with(FEE))
    assert tasks.open_tasks(OWNER) == []


def test_if_the_guides_cant_be_read_nothing_is_pursued_and_nothing_is_lost(monkeypatch):
    tasks.refresh(OWNER, [_gap()])

    def broken():
        raise RuntimeError("store down")

    monkeypatch.setattr(fraud_guides, "current_all", broken)
    assert tasks.open_tasks(OWNER) == []  # fails closed
    tasks.refresh(OWNER, [_gap(title="Something new")])  # a refresh while down changes nothing
    monkeypatch.undo()
    assert [t["title"] for t in tasks.open_tasks(OWNER)] == ["Record the nominee for the LIC policy"]


def test_a_vault_step_can_hold_a_secret_only_if_it_just_saves_it_in_the_vault():
    vault = {"type": "add_to_vault", "channel": "anvaya_app", "flags": []}
    for text in ("Keep your UPI PIN ready for the bank officer who will call", "Provide your OTP when asked",
                 "Note your password for the agent", "Email your password to Ajay"):
        assert step_guard.check({**vault, "title": text}, []) == "secret", text
    assert step_guard.check({**vault, "title": "Save your email password in your vault"}, []) is None


def test_secrets_are_caught_however_they_are_written():
    for text in ("Tell him the O T P", "Read out the o.t.p", "Give the ＯＴＰ", "Give the O\u200bTP",
                 "Share the verification code", "Send the code sent to your phone", "Give your card PIN code",
                 "Share your login details", "पासकोड बताएं"):
        assert step_guard.check({**WILL, "title": text}, []) == "secret", text
    assert step_guard.check({**WILL, "title": "Update the address: PIN code 570001"}, []) is None


def test_fixed_rules_hold_without_any_tagged_tactic():
    institution = {"type": "record_with_institution", "channel": "institution_official", "flags": []}
    assert step_guard.check({**institution, "title": "Pay the processing fee the caller asked for"}, []) \
        == "rule:pay_on_request"
    assert step_guard.check({**institution, "title": "Change the nominee as the man who called you said"}, []) \
        == "rule:change_on_request"
    assert step_guard.check({**WILL, "title": "Sign today, the broker says"}, []) == "rule:rushed_by_someone"
    assert step_guard.check({**institution, "title": "Pay the LIC premium at the branch"}, []) is None


def test_a_step_dropped_on_every_run_is_counted_once():
    for _ in range(3):
        tasks.refresh(OWNER, [_gap(title="Share the OTP with the Paytm agent")])
    assert len(store.list("steps_not_pursued")) == 1


def test_saathis_replies_and_topics_are_checked_too():
    assert loops.reply_problems("I got a call", "Please tell me the OTP he sent, Vikram ji.")
    assert loops.reply_problems("He wants a fee", "You can pay the fee the caller asked for.")
    assert not loops.reply_problems("Someone asked for my OTP", "Never share your OTP with anyone, Vikram ji.")
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    owner = "owner_demo_vikram"
    assert c.post(f"/api/owners/{owner}/conversation/prompt", json={"text": "What's your UPI PIN, so I can give it "
                                                                     "to the agent?"}).status_code == 422
    assert c.post(f"/api/owners/{owner}/conversation/prompt",
                  json={"text": "Who should look after the Mysore flat?"}).status_code == 200


def test_saathi_is_told_to_point_to_the_safer_way():
    rules = loops.persona({"name": "Vikram Rao", "family": []})
    assert "safer way" in rules and "Never name a fraud" in rules and "OTP" in rules
