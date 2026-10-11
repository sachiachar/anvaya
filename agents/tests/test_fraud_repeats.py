"""The same fraud is never added twice (the owner's rule): research and dreaming share one list of
what's known for a guide, and a reworded repeat of a tactic, scenario or draft isn't added again; a
subject that only brings up repeats counts as finding nothing new, and a gap the guide's tactics
already cover stops being a research subject. All data here is synthetic."""

import time

from app import fraud_guides, fraud_pipeline
from app.store import new_id, store
from tests.test_fraud_autonomy import clean, dream, research, stub  # noqa: F401  (fixtures and run helpers)
from tests.test_fraud_pipeline import KEY, scenario, tactic

same = fraud_pipeline.same_topic


def test_a_reworded_repeat_is_the_same_topic_and_a_different_fraud_is_not():
    assert same({"title": "Forged registrar seal on certificates"},
                {"title": "Fake registrar stamp used on a death certificate"})
    assert same({"title": "Impostor poses as the nominee at the bank"},
                {"title": "Someone impersonating the beneficiary at the bank"})
    assert not same({"title": "Forged registrar seal on certificates"},
                    {"title": "An impostor claims the insurance payout by phone"})


def test_descriptions_count_only_when_the_titles_share_something():
    text = "fraudsters copied the qr code from a genuine certificate and changed the printed name"
    assert not same({"title": "Registrar office insider", "text": text}, {"title": "Hospital record lima", "text": text})
    assert same({"title": "Registrar office insider", "text": text}, {"title": "A clerk at the office", "text": text})


def put_scenario(title: str, claim: str = "") -> None:
    store.put("fraud_scenarios", {"id": new_id("scn"), "at": time.time(), "guide_key": KEY, "scenario": title,
                                  "claim": claim, "coverage": "covered", "review": "unconfirmed"})


def test_research_does_not_add_a_tactic_dreaming_already_found(stub):  # noqa: F811
    put_scenario("Fake registrar stamp used on a death certificate")
    stub.tactics = [tactic("Forged registrar seal on certificates")]
    run = research()
    assert run["counts"]["already_known"] >= 1 and run["counts"]["found"] == 0  # each subject that brings it up skips it
    assert store.list("fraud_drafts") == [] and store.list("fraud_auto") == []
    assert not any(t.name == "Forged registrar seal on certificates" for t in fraud_guides.current(KEY).tactics)


def test_dreaming_does_not_add_a_scenario_research_already_found(stub):  # noqa: F811
    store.put("fraud_drafts", {"id": new_id("drf"), "at": time.time(), "guide_key": KEY, "kind": "tactic",
                               "tactic": "Impostor poses as the nominee at the bank", "claim": "", "review": "pending"})
    stub.imagined = [scenario("Someone impersonating the beneficiary at the bank")]
    run = dream()
    assert run["counts"]["already_known"] >= 1
    assert not any(s["scenario"] == "Someone impersonating the beneficiary at the bank"
                   for s in store.list("fraud_scenarios", guide_key=KEY))


def test_a_gap_the_guides_tactics_already_cover_is_no_longer_a_research_subject():
    guide = fraud_guides.current(KEY)
    covered = guide.tactics[0].name
    store.put("fraud_scenarios", {"id": "scn_known", "at": time.time(), "guide_key": KEY, "coverage": "gap",
                                  "scenario": covered, "claim": "", "review": "unconfirmed"})
    store.put("fraud_scenarios", {"id": "scn_new", "at": time.time(), "guide_key": KEY, "coverage": "gap",
                                  "scenario": "Voice clone calls the insurer to change the payout account",
                                  "claim": "", "review": "unconfirmed"})
    ids = {s["id"] for s in fraud_pipeline.subjects(guide)}
    assert "gap:scn_known" not in ids and "gap:scn_new" in ids
