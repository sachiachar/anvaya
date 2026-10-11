"""Cues for the fraud pipeline from kept knowledge, without private information (fraud-foresight
§5): fixed code over the graph part only, a closed list of patterns, counts of 5 owners or more,
and the fraud steps never read a bundle."""

import asyncio
import json

import pytest

from app import fraud_cues, fraud_pipeline, knowledge, media
from app.fraud_pipeline import Imagined
from app.fraud_bundle import records
from app.store import new_id, store
from tests.test_fraud_pipeline import KEY, Stub, clean, scenario  # noqa: F401  (the pipeline's fixtures)
from tests.test_governance import client, h  # noqa: F401  (the portal fixture, signed-in roles)

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


def _real_owners_without_relationship(n: int) -> None:
    for i in range(n):
        store.put("owners", {"id": f"owner_extra_{i}", "name": "x"})
        knowledge.commit("people", {"id": new_id("p"), "name": f"Kin {i}", "relationship": ""}, f"owner_extra_{i}")


def test_a_few_real_owners_are_never_added_to_the_synthetic_counts():
    fraud_cues.ensure_synthetic_owners()
    _real_owners_without_relationship(4)  # 3 synthetic + 4 real would be 7: but 4 real is too few to add
    assert "person.relationship_missing" not in {c["pattern"] for c in fraud_cues.cues()}
    _real_owners_without_relationship(5)  # 5 real owners: now they count, with the synthetic ones
    assert {c["pattern"]: c["owners"] for c in fraud_cues.cues()}["person.relationship_missing"] == 8
    _real_owners_without_relationship(9)  # 9 real owners count as 5: one more or less never shows
    assert {c["pattern"]: c["owners"] for c in fraud_cues.cues()}["person.relationship_missing"] == 8


def test_unknown_answers_and_old_catalogs_count_as_missing():
    from app import gaps

    assert knowledge.UNKNOWN == gaps.UNKNOWN  # the same "not known" answers as the gap check
    for i in range(5):
        oid = f"owner_unsure_{i}"
        store.put("owners", {"id": oid, "name": "x"})
        knowledge.commit("inventory", {"id": new_id("inv"), "category": "insurance", "institution": "LIC",
                                       "product": "Policy", "account_hint": "••1111",
                                       "nominee_or_beneficiary": "Not sure"}, oid)
        person = knowledge.commit("people", {"id": new_id("p"), "name": "Kin", "relationship": "unknown"}, oid)
        # A catalog written before `relationship` was a presence flag: has [] for the person.
        cat = knowledge.catalog(oid)
        cat["concepts"][person["id"]]["graph"]["has"] = []
        media.save(oid, knowledge.CATALOG, json.dumps(cat).encode(), "application/json")
        assert knowledge.catalog(oid)["concepts"][person["id"]]["graph"]["has"] == []  # stored the old way
    counts = {c["pattern"]: c["owners"] for c in fraud_cues.cues()}
    assert counts["policy.nominee_missing"] == 5 and "nominee.not_kept" not in counts  # 5 real, no synthetic
    assert counts["person.relationship_missing"] == 5


def test_a_half_made_synthetic_owner_is_made_again():
    fraud_cues.ensure_synthetic_owners()
    media.delete_all(fraud_cues.synthetic_id(3))  # its items are lost; its owner record remains
    assert fraud_cues.ensure_synthetic_owners() == 1
    assert len(knowledge.graph_parts(fraud_cues.synthetic_id(3))) == 3


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
    saved = {s["scenario"]: s.get("cue") for s in records.list("fraud_scenarios")}
    assert saved["Claim as the nominee of a policy with none recorded"]["owners"] == 7
    assert saved["Something else entirely"] is None
    Imagined(scenarios=[real])  # the schema accepts a cue


def test_the_minimum_is_a_portal_setting_with_a_floor(client):  # noqa: F811
    from app.governance.seed import clear_samples

    fraud_cues.ensure_synthetic_owners()
    url = "/api/platform/privacy/cue-min-owners"
    assert client.get("/api/platform/fraud", headers=h("admin")).json()["privacy"]["min_owners"] == 5
    # Only a platform admin, with a reason, and never below the floor.
    assert client.post(url, headers=h("risk"), json={"min_owners": 10, "reason": "tighter"}).status_code == 403
    assert client.post(url, headers=h("admin"), json={"min_owners": 3, "reason": "looser"}).status_code == 422
    assert client.post(url, headers=h("admin"), json={"min_owners": 10, "reason": ""}).status_code == 422
    r = client.post(url, headers=h("admin"), json={"min_owners": 10, "reason": "Fewer, larger groups"})
    assert r.status_code == 200 and r.json()["min_owners"] == 10
    assert fraud_cues.cues() == []  # 7, 6 and 6 synthetic owners: all below 10 now
    assert any(e["action"] == "privacy.cue_min_owners" and e["target"] == "10"
               for e in client.get("/api/platform/audit", headers=h("admin")).json()["entries"])
    clear_samples()  # a data-version clean-up keeps settings people chose
    assert fraud_cues.min_owners() == 10
    store.update("platform_settings", "settings", {"cue_min_owners": 2})  # even if stored below the floor
    assert fraud_cues.min_owners() == 5
    store.update("platform_settings", "settings", {"cue_min_owners": None})  # back to the default for other tests
