"""Cues for the fraud pipeline from kept knowledge, without private information (fraud-foresight
§5): fixed code over the graph part only, a closed list of patterns, counts of 5 owners or more,
and the fraud steps never read a bundle."""

import asyncio
import json

import pytest

from app import fraud_cues, fraud_pipeline, knowledge
from app.fraud_pipeline import Imagined
from app.store import new_id, store
from tests.test_fraud_pipeline import KEY, Stub, clean, scenario  # noqa: F401  (the pipeline's fixtures)

SECRETS = ("Ravi Shankar", "123456789012", "my brother cheated me", "Canara", "gold in the locker", "Synthetic Insurer",
           "Relative 0", "cupboard", "owner_synthetic")


@pytest.fixture(autouse=True)
def only_these_owners():
    for owner in store.list("owners"):
        store.delete("owners", owner["id"])
    yield


def _owner_with_secrets(i: int) -> str:
    """A real-looking owner whose kept items are full of private details."""
    oid = f"owner_secret_{i}"
    store.put("owners", {"id": oid, "name": "Ravi Shankar"})
    knowledge.commit("inventory", {"id": new_id("inv"), "category": "insurance", "institution": "Canara",
                                   "product": "Policy 123456789012", "account_hint": "••9012",
                                   "nominee_or_beneficiary": None}, oid)
    knowledge.commit("moments", {"id": new_id("m"), "kind": "story", "title": "my brother cheated me",
                                 "summary": "There is gold in the locker.", "quote": "my brother cheated me",
                                 "people": ["Ravi Shankar"], "places": []}, oid)
    return oid


def test_the_graph_part_is_all_the_extractor_sees():
    oid = _owner_with_secrets(1)
    parts = knowledge.graph_parts(oid)
    assert parts and all(set(p) == {"path", "type", "status", "links", "has"} for p in parts)
    text = json.dumps(parts)
    assert not any(s in text for s in SECRETS if s != "owner_synthetic")


def test_patterns_come_from_a_closed_list():
    graphs = [{"type": "Insurance Policy", "has": [], "links": {}},
              {"type": "Bank Account", "has": ["nominee_or_beneficiary"], "links": {}},
              {"type": "Property", "has": ["papers_at"], "links": {}},
              {"type": "Person", "has": [], "links": {}},
              {"type": "Story", "has": [], "links": {}}]
    found = fraud_cues.patterns_of(graphs)
    assert found == {("policy.nominee_missing", "insurance_policy"), ("nominee.not_kept", "bank_account"),
                     ("property.heir_missing", "property"), ("person.relationship_missing", "family")}
    assert {p for p, _ in found} <= set(fraud_cues.PATTERNS)


def test_only_counts_of_five_owners_or_more_are_reported():
    assert fraud_cues.ensure_synthetic_owners() == 12
    assert fraud_cues.ensure_synthetic_owners() == 0  # once
    counts = {c["pattern"]: c["owners"] for c in fraud_cues.cues()}
    assert counts == {"policy.nominee_missing": 7, "property.heir_missing": 6, "property.papers_missing": 6}
    # person.relationship_missing has 3 synthetic owners: below the minimum, so dropped.
    for i in range(2):
        store.put("owners", {"id": f"owner_extra_{i}", "name": "x"})
        knowledge.commit("people", {"id": new_id("p"), "name": f"Kin {i}", "relationship": ""}, f"owner_extra_{i}")
    assert {c["pattern"]: c["owners"] for c in fraud_cues.cues()}["person.relationship_missing"] == 5


def test_cues_carry_no_owner_names_ids_or_details():
    fraud_cues.ensure_synthetic_owners()
    for i in range(5):
        _owner_with_secrets(i)
    found = fraud_cues.cues()
    assert found and all(set(c) == {"pattern", "asset_class", "owners", "means"} for c in found)
    text = json.dumps(found) + fraud_cues.describe(found)
    assert not any(s in text for s in SECRETS)


def _dream_with(monkeypatch, imagined):
    stub = Stub(imagined=imagined)
    monkeypatch.setattr(fraud_pipeline, "generate_json", stub.generate_json)
    monkeypatch.setattr(fraud_pipeline, "answer_from_web", stub.answer_from_web)
    run = asyncio.run(fraud_pipeline.dream(fraud_pipeline.start_run("dream", KEY, "test")))
    return stub, run


def test_the_dreamer_gets_counts_and_never_reads_a_bundle(monkeypatch):
    for i in range(5):
        _owner_with_secrets(i)

    def never(*a, **kw):
        raise AssertionError("the fraud steps must not read a bundle")

    for reader in ("read", "items", "for_reading", "get", "current"):
        monkeypatch.setattr(knowledge, reader, never)
    stub, run = _dream_with(monkeypatch, None)
    assert run["status"] == "done", run.get("error")
    prompts = "\n".join(stub.prompts)
    assert "policy.nominee_missing: 12 owners have an insurance policy with no nominee recorded" in prompts
    assert not any(s in prompts for s in SECRETS)
    assert any("Starting points from kept knowledge" in step["line"] for step in run["steps"])


def test_a_scenario_keeps_only_a_real_cue(monkeypatch):
    fraud_cues.ensure_synthetic_owners()
    real = scenario("Claim as the nominee of a policy with none recorded")
    real.cue = "policy.nominee_missing"
    invented = scenario("Something else entirely")
    invented.cue = "anything the model likes"
    _dream_with(monkeypatch, [real, invented, scenario("A third, far one", "far")])
    saved = {s["scenario"]: s.get("cue") for s in store.list("fraud_scenarios")}
    assert saved["Claim as the nominee of a policy with none recorded"]["owners"] == 7
    assert saved["Something else entirely"] is None
    Imagined(scenarios=[real])  # the schema accepts a cue
