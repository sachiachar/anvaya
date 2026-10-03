"""Fraud reference guides (PR-2.3): labels, versions, the built-in fallback, and named findings."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import fraud_guides, loops, main, release
from app.fraud_guides import BUILT_IN, Guide, Sign
from app.loops import review_summary
from app.schemas import DocumentCheck, SignResult
from app.store import store
from tests.people import PRIYA

INDIA = BUILT_IN["IN.death_certificate"]
SAFEGUARDS = {"owner alert and cancel link", "waiting period", "two reviewers", "key code",
              "recipient invitation", "certificate checks"}


@pytest.fixture(autouse=True)
def no_saved_guides():
    for collection in ("fraud_guides", "fraud_guide_versions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])


def sign(id, label="derived", trust="machine-confirmed", **kw):
    return Sign(id=id, look_for=f"{id} look", genuine=f"{id} genuine", label=label, trust=trust, **kw)


def guide(*signs):
    return INDIA.model_copy(update={"signs": list(signs)})


def check(**overrides):
    base = dict(document_type="Death certificate", is_expected_type=True, person_named="Vikram Rao",
                matches_expected_person=True, concerns=[], confidence=0.95)
    return DocumentCheck(**{**base, **overrides})


# ---- The built-in India guide (PR-2.3.1, PR-2.3.2) ----------------------------------


def test_india_guide_cites_a_source_for_every_item():
    source_ids = {s.id for s in INDIA.sources}
    for item in [*INDIA.signs, *INDIA.tactics]:
        assert item.label == "derived" and item.sources, item.id
        assert set(item.sources) <= source_ids, item.id


def test_every_tactic_step_names_what_catches_it():
    sign_ids = {s.id for s in INDIA.signs}
    for tactic in INDIA.tactics:
        for step in tactic.steps:
            assert step.caught_by in sign_ids | SAFEGUARDS, (tactic.id, step.caught_by)


def test_death_certificates_use_the_india_guide_and_other_documents_none_yet():
    assert fraud_guides.key_for("death certificate", "India") == "IN.death_certificate"
    assert fraud_guides.key_for("death certificate", "IN") == "IN.death_certificate"
    assert fraud_guides.current(fraud_guides.key_for("death certificate", "Singapore")) is None
    assert fraud_guides.key_for("proof of legal authority (e.g. probate)") is None


# ---- Only checked knowledge reaches the check (rule 2) ------------------------------


def test_imagined_rejected_and_retired_signs_never_reach_the_check():
    g = guide(sign("a"), sign("b", label="simulated", trust="unverified"), sign("c", label="invalidated"),
              sign("d", status="retired"), sign("e", label="observed"), sign("f", label="validated"))
    assert [s.id for s in fraud_guides.signs_for_check(g)] == ["a", "e", "f"]


def test_projections_are_at_most_a_third_of_the_signs():
    known = [sign(f"k{i}") for i in range(4)]
    projections = [sign(f"p{i}", label="validated", projection=True) for i in range(5)]
    usable = fraud_guides.signs_for_check(guide(*known, *projections))
    assert [s.id for s in usable] == ["k0", "k1", "k2", "k3", "p0", "p1"]
    assert sum(s.projection for s in usable) * 3 <= len(usable)


# ---- Versions and the fallback (PR-2.3.3) -------------------------------------------


def test_every_version_is_kept():
    first = fraud_guides.save_version(INDIA)
    second = fraud_guides.save_version(INDIA.model_copy(update={"signs": INDIA.signs[:3]}))
    assert (first.version, second.version) == (1, 2)
    assert len(store.list("fraud_guide_versions", key=INDIA.key)) == 2
    assert fraud_guides.current(INDIA.key).version == 2
    row = store.get("fraud_guides", INDIA.key)
    assert row["version"] == 2 and row["signals"] == 3


def test_with_nothing_saved_the_built_in_copy_is_used():
    assert fraud_guides.current(INDIA.key) == INDIA


def test_when_the_store_is_down_the_built_in_copy_is_used(monkeypatch):
    fraud_guides.save_version(INDIA.model_copy(update={"signs": INDIA.signs[:2]}))

    def down(*a, **k):
        raise RuntimeError("Firestore unavailable")

    monkeypatch.setattr(store, "list", down)
    assert fraud_guides.current(INDIA.key) == INDIA


# ---- The check receives the guide (PR-2.3.4) ----------------------------------------


def test_the_check_instructions_include_every_usable_sign(monkeypatch):
    seen = {}

    async def fake_generate(**kw):
        seen.update(kw)
        return check()

    monkeypatch.setattr(loops, "generate_json", fake_generate)
    g = guide(sign("qr"), sign("dreamt", label="simulated", trust="unverified"),
              sign("seen", label="validated", projection=True))
    asyncio.run(loops.check_document(b"img", "image/jpeg", "death certificate", "Vikram Rao", g))
    assert "- qr: look for: qr look" in seen["prompt"]
    assert "dreamt" not in seen["prompt"]
    # one known sign allows no projection (at most a third)
    assert "seen" not in seen["prompt"]


def test_a_death_report_is_checked_with_the_india_guide(monkeypatch):
    used = {}

    async def fake_check(image, mime, expected, person, guide=None):
        used["guide"] = guide
        return check(signs=[SignResult(sign_id="qr_missing", result="found", note="No QR code")])

    monkeypatch.setattr(loops, "check_document", fake_check)
    monkeypatch.setattr(release.settings, "release_wait_seconds", 0)
    client = TestClient(main.app)
    assert client.post("/api/demo/seed").status_code == 200
    files = {"certificate": ("c.jpg", b"img", "image/jpeg")}
    case = client.post("/api/owners/owner_demo_vikram/death-report", files=files, headers=PRIYA).json()["case"]
    assert used["guide"].key == "IN.death_certificate"
    death_check = case["certificate_check"]
    assert death_check["guide"]["key"] == "IN.death_certificate"
    assert death_check["verdict"] == "needs_attention"


# ---- Named findings (PR-2.3.5) ------------------------------------------------------


def test_every_guide_sign_gets_a_result_in_the_guides_order():
    g = guide(sign("a"), sign("b"), sign("c"))
    s = review_summary(check(signs=[SignResult(sign_id="c", result="ruled_out"),
                                    SignResult(sign_id="made_up", result="found")]), g)
    assert [(r["id"], r["result"]) for r in s["signs"]] == [("a", "cant_tell"), ("b", "cant_tell"), ("c", "ruled_out")]
    assert s["verdict"] == "likely_valid"  # an invented sign is dropped, so nothing was found


def test_a_machine_confirmed_sign_found_needs_a_closer_look_without_lowering_confidence():
    s = review_summary(check(signs=[SignResult(sign_id="a", result="found")]), guide(sign("a")))
    assert s["verdict"] == "needs_attention" and s["confidence"] == 0.95


def test_a_human_reviewed_sign_found_counts_more():
    s = review_summary(check(signs=[SignResult(sign_id="a", result="found")]),
                       guide(sign("a", trust="human-reviewed")))
    assert s["verdict"] == "needs_attention" and s["confidence"] == 0.5


def test_the_wrong_document_still_scores_zero_with_a_guide():
    s = review_summary(check(is_expected_type=False, confidence=1.0,
                             signs=[SignResult(sign_id="a", result="found")]), guide(sign("a")))
    assert s["verdict"] == "not_valid" and s["confidence"] == 0.0


def test_without_a_guide_the_summary_has_no_signs():
    s = review_summary(check())
    assert s["signs"] == [] and s["guide"] is None and s["verdict"] == "likely_valid"


def test_guide_round_trips_through_the_store():
    saved = fraud_guides.save_version(INDIA)
    stored = store.get("fraud_guide_versions", f"{INDIA.key}.v1")
    assert Guide.model_validate({k: v for k, v in stored.items() if k != "id"}) == saved
