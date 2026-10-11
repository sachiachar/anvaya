"""The fraud guides live in one platform-level OKF bundle (PR-2.14.1): the certificate check gets the
same signs before and after the move, the bundle follows OKF v0.2, every change to an item is an
event, and guides saved in Firestore before the move are copied in once with their versions. All
data here is synthetic."""

import json

import pytest

from app import fraud_bundle, fraud_guides, media
from app.fraud_guides import BUILT_IN, signs_for_check
from app.knowledge import parse
from app.store import store

KEY = "IN.death_certificate"
INDIA = BUILT_IN[KEY]


@pytest.fixture(autouse=True)
def clean():
    for collection in ("fraud_guides", "fraud_guide_versions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    fraud_guides.clear_saved()


def old_version(guide, version):
    """A guide version as Firestore held it before the move."""
    saved = guide.model_copy(update={"version": version, "updated_at": 1_700_000_000.0 + version})
    store.put("fraud_guide_versions", {"id": f"{guide.key}.v{version}", **saved.model_dump()})


def test_the_check_gets_exactly_the_same_signs_after_the_move():
    old_version(INDIA.model_copy(update={"signs": INDIA.signs[:4]}), 1)
    old_version(INDIA, 2)
    before = signs_for_check(fraud_guides._with_built_in_dates(fraud_guides.Guide.model_validate(
        {k: v for k, v in store.get("fraud_guide_versions", f"{KEY}.v2").items() if k != "id"})))
    fraud_bundle.forget_cache()
    after = fraud_guides.current(KEY)
    assert after.version == 2 and signs_for_check(after) == before


def test_old_versions_are_copied_once_with_their_history():
    old_version(INDIA.model_copy(update={"signs": INDIA.signs[:4]}), 1)
    old_version(INDIA, 2)
    assert [g.key for g in fraud_guides.current_all()] == sorted(BUILT_IN)
    events = fraud_guides.history(KEY)
    assert {e["version"] for e in events} == {1, 2}
    added_later = [e for e in events if e["version"] == 2]
    assert len(added_later) == len(INDIA.signs) - 4 and all(e["action"] == "Creation" for e in added_later)
    fraud_guides._migrated.clear()  # another instance starts: nothing is copied twice
    assert fraud_guides.current(KEY).version == 2 and len(fraud_guides.history(KEY)) == len(events)


def test_the_bundle_follows_okf():
    fraud_guides.save_version(INDIA)
    names = media.names(fraud_bundle.OWNER, "fraud/")
    index, _ = parse(media.load(fraud_bundle.OWNER, "fraud/index.md").decode())
    assert index["okf_version"] == "0.2"
    assert f"fraud/{KEY}/index.md" in names and "fraud/log.md" in names
    sign = INDIA.signs[0]
    front, body = parse(media.load(fraud_bundle.OWNER, f"fraud/{KEY}/signs/{sign.id}.md").decode())
    assert front["type"] == "Warning Sign" and front["status"] == "stable" and front["id"] == f"{KEY}/signs/{sign.id}"
    assert {"title", "description", "tags", "sources", "generated"} <= set(front)
    assert front["record"] == sign.model_dump() and sign.look_for in body
    tactic = INDIA.tactics[0]
    front, _ = parse(media.load(fraud_bundle.OWNER, f"fraud/{KEY}/tactics/{tactic.id}.md").decode())
    assert front["links"]["caught_by"] and all(p.startswith(f"{KEY}/signs/") for p in front["links"]["caught_by"])
    catalog = json.loads(media.load(fraud_bundle.OWNER, fraud_bundle.CATALOG))
    assert catalog["okf_version"] == "0.2" and catalog["guides"][KEY]["version"] == 1
    assert any(n.startswith("fraud/.events/") for n in names)


def test_a_retired_item_stays_with_its_history():
    fraud_guides.save_version(INDIA)
    sign = INDIA.signs[0].id
    fraud_guides.retire_items(KEY, {sign})
    front, _ = parse(media.load(fraud_bundle.OWNER, f"fraud/{KEY}/signs/{sign}.md").decode())
    assert front["status"] == "deprecated" and front["guide_version"] == 2
    assert [e["action"] for e in fraud_guides.history(KEY, f"{KEY}/signs/{sign}")] == ["Creation", "Retirement"]
    assert sign not in {s.id for s in signs_for_check(fraud_guides.current(KEY))}
    assert sign in {s.id for s in fraud_guides.current(KEY).signs}  # kept, marked retired


def test_a_write_built_on_an_old_version_is_refused():
    fraud_guides.save_version(INDIA)
    fraud_guides.save_version(INDIA.model_copy(update={"title": "Second"}))
    with pytest.raises(fraud_bundle.Stale):
        fraud_bundle.save_guide(INDIA.model_copy(update={"version": 2}).model_dump(), built_on=1)
    assert fraud_guides.current(KEY).title == "Second"


def test_unchanged_items_are_not_rewritten():
    fraud_guides.save_version(INDIA)
    count = len(fraud_guides.history(KEY))
    fraud_guides.save_version(INDIA.model_copy(update={"title": "Renamed guide"}))
    assert len(fraud_guides.history(KEY)) == count and fraud_guides.current(KEY).version == 2


# ---- Research records (PR-2.14.2) ---------------------------------------------------------------

records = fraud_bundle.records


def test_old_records_are_copied_once_and_seeded_examples_are_not():
    store.put("fraud_scenarios", {"id": "scn_old", "at": 1.0, "guide_key": KEY, "scenario": "Synthetic old scenario",
                                  "review": "unconfirmed"})
    store.put("fraud_scenarios", {"id": "scn_seed", "at": 1.0, "guide_key": KEY, "scenario": "Example", "seeded": True})
    store.put("fraud_decisions", {"id": "scn_done", "at": 1.0})
    assert [s["id"] for s in records.list("fraud_scenarios")] == ["scn_old"]
    assert records.get("fraud_decisions", "scn_done")
    store.put("fraud_scenarios", {"id": "scn_late", "at": 2.0, "guide_key": KEY, "scenario": "Written after the copy"})
    records.forget_cache()
    assert not records.get("fraud_scenarios", "scn_late")  # copied once only
    assert store.get("fraud_scenarios", "scn_old")  # the Firestore copy is left for a person to remove
    for collection in ("fraud_scenarios", "fraud_decisions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])


def test_each_record_is_a_concept_with_its_history():
    records.put("fraud_drafts", {"id": "drf_1", "at": 1.0, "guide_key": KEY, "kind": "tactic",
                                 "tactic": "Synthetic forged stamp", "review": "pending"})
    records.update("fraud_drafts", "drf_1", {"review": "added"})
    front, body = parse(media.load(fraud_bundle.OWNER, f"fraud/{KEY}/research/drafts/drf_1.md").decode())
    assert front["type"] == "Fraud Draft" and front["record"]["review"] == "added" and "Synthetic forged stamp" in body
    events = [e for e in fraud_bundle.records._catalog(fresh=True)["events"] if e["item"] == "drf_1"]
    assert [e["action"] for e in events] == ["Creation", "Update"]
    records.delete("fraud_drafts", "drf_1")
    assert records.get("fraud_drafts", "drf_1") is None
    assert not media.load(fraud_bundle.OWNER, f"fraud/{KEY}/research/drafts/drf_1.md")


def test_a_decision_is_recorded_only_once():
    assert records.create("fraud_decisions", {"id": "scn_x", "at": 1.0})
    assert not records.create("fraud_decisions", {"id": "scn_x", "at": 2.0})
    assert records.get("fraud_decisions", "scn_x")["at"] == 1.0


def test_updating_a_missing_record_fails():
    with pytest.raises(KeyError):
        records.update("fraud_scenarios", "scn_missing", {"review": "added"})
