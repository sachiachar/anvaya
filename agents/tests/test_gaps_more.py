"""More gaps (PR 2 of the memory plan): what families most often can't find (a will, papers,
loans, who to call first, online accounts), and making sure: something kept with doubt comes back
to be checked, and facts that change come back once a year."""

import time

import pytest
from fastapi.testclient import TestClient

from app import fraud_cues, gaps, main, proposals
from app.store import new_id, store
from tests.test_knowledge import OWNER, POLICY, STORY, _keep

DAY = gaps.DAY
FLAT = {**POLICY, "category": "property", "institution": "Pune flat", "product": "2BHK flat", "account_hint": "",
        "nominee_or_beneficiary": None, "papers_at": None}


@pytest.fixture(autouse=True)
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def kinds(now: float | None = None) -> set[str]:
    return {g["kind"] for g in gaps.find(OWNER, now=now)}


def said(text: str, kind: str = "practical") -> dict:
    return _keep("moments", {**STORY, "kind": kind, "title": text[:40], "summary": text, "quote": text})


# ---- What families most often can't find ----------------------------------------


def test_with_nothing_kept_the_common_gaps_are_open_and_the_will_comes_first(client):
    assert {"will", "loans", "first_call", "digital"} <= kinds()
    assert gaps.find(OWNER)[0]["kind"] == "will"


@pytest.mark.parametrize("kind,text", [
    ("will", "My will is with Advocate Sharma in Pune."),
    ("will", "I have no will yet."),
    ("will", "मेरी वसीयत अलमारी में है।"),
    ("loans", "There are no loans, only the car EMI, which ends next year."),
    ("first_call", "Call my brother Ajay first; he knows everything."),
    ("digital", "My email password is in the vault."),
])
def test_once_anything_kept_mentions_it_the_common_gap_closes(kind, text):
    assert kind in kinds()
    said(text)
    assert kind not in kinds()


def test_will_in_a_sentence_like_i_will_go_is_not_a_will():
    said("I will go to Mysore in December.")
    assert "will" in kinds()


def test_a_kept_loan_or_online_account_closes_its_gap():
    _keep("inventory", {**POLICY, "category": "loan", "institution": "HDFC", "product": "Car loan"})
    _keep("inventory", {**POLICY, "category": "digital_account", "institution": "Google", "product": "Gmail"})
    assert not {"loans", "digital"} & kinds()


def test_property_and_insurance_need_where_the_papers_are():
    _keep("inventory", FLAT)
    _keep("inventory", {**POLICY, "papers_at": "blue folder, bedroom cupboard"})
    papers = [g for g in gaps.find(OWNER) if g["kind"] == "papers"]
    assert len(papers) == 1 and "Pune flat" in papers[0]["text"]


def test_fraud_cues_and_every_gap_use_the_known_kinds():
    _keep("inventory", FLAT)
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": None, "unsure": True})
    assert {kind for _, kind in fraud_cues.PATTERNS.values() if kind} <= set(gaps.KINDS)
    assert kinds(now=time.time() + 2 * gaps.RECHECK_EVERY_DAYS * DAY) <= set(gaps.KINDS)


# ---- Making sure ------------------------------------------------------------------


def test_something_kept_with_doubt_comes_back_to_be_checked_after_two_weeks():
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": True})
    assert "confirm" not in kinds(now=time.time() + 13 * DAY)
    confirm = next(g for g in gaps.find(OWNER, now=time.time() + 15 * DAY) if g["kind"] == "confirm")
    assert "nominee: Meena" in confirm["text"] and "doubt" in confirm["text"]


def test_keeping_it_again_without_doubt_closes_the_check():
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": True})
    # As Gemini sends it: every card says whether the owner was unsure.
    sure = {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": False}
    proposals.approve("inventory", proposals.save_inventory(OWNER, sure, {}))
    assert "confirm" not in kinds(now=time.time() + 15 * DAY)


def test_once_they_say_it_is_right_saathi_stops_checking():
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": True})
    later = time.time() + 15 * DAY
    key = next(g["key"] for g in gaps.find(OWNER, now=later) if g["kind"] == "confirm")
    assert key in [g["key"] for g in gaps.due(OWNER, n=20, now=later)]
    store.put("gap_asks", {"id": f"{OWNER}:{key}", "owner_id": OWNER, "key": key,
                           "events": [{"turn_id": new_id("turn"), "at": later, "outcome": "answered"}]})
    assert key not in [g["key"] for g in gaps.due(OWNER, n=20, now=later + 60 * DAY)]


def test_things_that_change_come_back_once_a_year_with_a_new_key_each_year():
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "papers_at": "blue folder"})
    year = gaps.RECHECK_EVERY_DAYS * DAY
    assert "recheck" not in kinds(now=time.time() + year - DAY)
    first = next(g for g in gaps.find(OWNER, now=time.time() + year + DAY) if g["kind"] == "recheck")
    second = next(g for g in gaps.find(OWNER, now=time.time() + 2 * year + DAY) if g["kind"] == "recheck")
    assert first["key"] != second["key"] and "still the same" in first["text"]


# ---- From the code review ---------------------------------------------------------


def _answer(key: str, at: float) -> None:
    store.put("gap_asks", {"id": f"{OWNER}:{key}", "owner_id": OWNER, "key": key,
                           "events": [{"turn_id": new_id("turn"), "at": at, "outcome": "answered"}]})


def test_a_check_they_said_is_right_leaves_their_list_too(client):
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": True})
    later = time.time() + 15 * DAY
    key = next(g["key"] for g in gaps.find(OWNER, now=later) if g["kind"] == "confirm")
    _answer(key, later)
    assert "confirm" not in kinds(now=later)
    assert key not in [g["key"] for g in client.get(f"/api/owners/{OWNER}/gaps").json()["gaps"]]


def test_a_newer_version_kept_with_doubt_is_checked_again():
    _keep("inventory", {**POLICY, "nominee_or_beneficiary": "Meena", "unsure": True})
    later = time.time() + 15 * DAY
    _answer(next(g["key"] for g in gaps.find(OWNER, now=later) if g["kind"] == "confirm"), later)
    doubt = {**POLICY, "nominee_or_beneficiary": "Ravi", "unsure": True}
    proposals.approve("inventory", proposals.save_inventory(OWNER, doubt, {}))
    confirm = [g for g in gaps.find(OWNER, now=later) if g["kind"] == "confirm"]
    assert len(confirm) == 1 and "Ravi" in confirm[0]["text"]


def test_a_waiting_story_card_pauses_a_common_gap():
    assert "will" in [g["kind"] for g in gaps.due(OWNER, n=20)]
    proposals.save_moment(OWNER, {**STORY, "kind": "practical", "title": "The will", "summary": "My will is with "
                                  "Advocate Sharma.", "quote": "My will is with Advocate Sharma."}, {})
    assert "will" not in [g["kind"] for g in gaps.due(OWNER, n=20)]
    assert "will" in kinds()  # still open until it's kept


def test_phone_someone_first_is_not_an_online_account():
    said("Phone Meena first, then the bank.")
    assert "first_call" not in kinds() and "digital" in kinds()
