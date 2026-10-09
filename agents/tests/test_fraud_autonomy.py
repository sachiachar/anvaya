"""The fraud pipeline on its own (PR-2.7.y): research subjects rotate, gaps first; dreaming takes
each tactic in turn; what 2 independent websites confirm is added to the guide on its own, add-only;
one source makes a draft; a person can remove anything added, and 2 removals in a week pause adding;
1 in 10 added items is marked for a spot check; a gap is reported to the team, never fed back."""

import asyncio
import time

import pytest
from app import fraud_guides, fraud_pipeline
from app.store import store
from app.web_search import Answer as Grounded
from tests.test_fraud_pipeline import BACKED, COLLECTIONS, KEY, Stub, scenario, tactic
from tests.test_governance import client, h  # noqa: F401  (the portal fixture, signed-in roles)

EXTRA = ("fraud_subjects", "fraud_auto", "fraud_autonomy", "fraud_dreamed")


def two_sites(text=BACKED):
    segment = text.splitlines()[-1]
    return Grounded(text=text, sources=[("tribuneindia.com", "https://www.tribuneindia.com/a"),
                                        ("freepressjournal.in", "https://freepressjournal.in/b")],
                    supports=[(segment, [0, 1])])


@pytest.fixture(autouse=True)
def clean():
    for collection in (*COLLECTIONS, *EXTRA):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    fraud_guides.ensure_saved(KEY)


@pytest.fixture()
def stub(monkeypatch):
    s = Stub()
    s.sites = 2  # how many different websites back a claim

    async def answer(*, question, query, agent, **kw):
        s.prompts.append(question)
        s.prompts.append(query)
        return two_sites() if s.sites == 2 else Grounded(
            text=BACKED, sources=[("one", "https://example.test/a"), ("one", "https://example.test/b")],
            supports=[(BACKED.splitlines()[-1], [0, 1])])

    monkeypatch.setattr(fraud_pipeline, "generate_json", s.generate_json)
    monkeypatch.setattr(fraud_pipeline, "answer_from_web", answer)
    return s


def research():
    return asyncio.run(fraud_pipeline.research(fraud_pipeline.start_run("research", KEY, "test")))


def dream():
    return asyncio.run(fraud_pipeline.dream(fraud_pipeline.start_run("dream", KEY, "test")))


# ---- Subjects rotate, gaps first --------------------------------------------------------------


def test_subjects_cover_signs_safeguards_open_questions_and_tactics():
    kinds = {s["kind"] for s in fraud_pipeline.subjects(fraud_guides.current(KEY))}
    assert {"sign", "safeguard", "open", "tactic"} <= kinds


def test_a_new_gap_is_researched_first_then_the_least_recently_researched():
    guide = fraud_guides.current(KEY)
    first_two = fraud_pipeline.pick_subjects(guide, 2)
    for s in first_two:
        fraud_pipeline.subject_done(KEY, s, found_new=False)
    next_two = fraud_pipeline.pick_subjects(guide, 2)
    assert not {s["id"] for s in first_two} & {s["id"] for s in next_two}  # each comes round in turn
    store.put("fraud_scenarios", {"id": "scn_gap1", "at": time.time(), "guide_key": KEY, "coverage": "gap",
                                  "claim": "Someone posed as the nominee at the bank", "review": "unconfirmed"})
    assert fraud_pipeline.pick_subjects(guide, 1)[0]["id"] == "gap:scn_gap1"


def test_a_subject_that_finds_nothing_three_times_rests_for_a_week():
    guide = fraud_guides.current(KEY)
    subject = fraud_pipeline.pick_subjects(guide, 1)[0]
    for _ in range(3):
        fraud_pipeline.subject_done(KEY, subject, found_new=False)
    assert subject["id"] not in {s["id"] for s in fraud_pipeline.pick_subjects(guide, 100)}
    later = time.time() + fraud_pipeline.SUBJECT_REST_SECONDS + 1
    assert subject["id"] in {s["id"] for s in fraud_pipeline.pick_subjects(guide, 100, now=later)}


def test_each_research_run_covers_two_different_subjects(stub):
    run = research()
    assert run["counts"]["subjects"] == 2
    queries = [p for p in stub.prompts if p.startswith(f"{fraud_guides.current(KEY).document}")]
    assert len(set(queries)) == 2


# ---- Added on its own with two independent sources ------------------------------------------------


def test_two_independent_sites_add_a_tactic_and_its_sign_on_their_own(stub):
    stub.tactics = [tactic("Registrar office insider kilo")]
    version = fraud_guides.current(KEY).version
    run = research()
    guide = fraud_guides.current(KEY)
    added = next(t for t in guide.tactics if t.name == "Registrar office insider kilo")
    assert added.auto and added.trust == "machine-confirmed" and guide.version > version
    assert next(s for s in guide.signs if s.id == f"{added.id}_sign").auto
    assert run["counts"]["added"] == 1 and store.list("fraud_drafts", review="pending") == []
    [record] = store.list("fraud_auto")
    assert record["kind"] == "tactic" and record["sources"] == 2 and record["spot_check"]  # the first is spot-checked


def test_one_website_makes_a_draft_not_an_addition(stub):
    stub.sites, stub.tactics = 1, [tactic("Registrar office insider kilo")]
    research()
    assert [d["review"] for d in store.list("fraud_drafts")] == ["pending"]
    assert all(not t.auto for t in fraud_guides.current(KEY).tactics) and store.list("fraud_auto") == []


def test_independent_sources_count_websites_not_pages():
    same = [{"url": "https://www.example.com/a"}, {"url": "https://example.com/b"}]
    assert fraud_pipeline.independent_sources(same) == 1
    assert fraud_pipeline.independent_sources([*same, {"url": "https://other.org/x"}]) == 2


def test_a_scenario_two_sites_report_is_validated_on_its_own(stub):
    stub.imagined = [scenario("A copied QR code oscar", sign=True)]
    dream()
    [saved] = store.list("fraud_scenarios")
    assert saved["review"] == "added" and saved["label"] == "validated"
    guide = fraud_guides.current(KEY)
    assert next(s for s in guide.scenarios if s["id"] == saved["id"])["auto"]
    sign = next(s for s in guide.signs if s.id == saved["id"])
    assert sign.projection and sign.auto and sign.trust == "machine-confirmed"


# ---- People can step in, without a queue ------------------------------------------------------


def test_removing_an_added_item_retires_it_and_two_removals_pause_adding(stub):
    stub.tactics = [tactic("Registrar office insider kilo"), tactic("Forged hospital record lima")]
    research()
    first, second = store.list("fraud_auto")
    fraud_pipeline.remove_added(first["id"])
    guide = fraud_guides.current(KEY)
    assert next(t for t in guide.tactics if t.id == first["id"]).status == "retired"
    assert not fraud_pipeline.adding_paused(KEY)
    fraud_pipeline.remove_added(second["id"])
    assert fraud_pipeline.adding_paused(KEY)
    stub.tactics = [tactic("Third tactic mike")]
    research()  # paused: confirmed by two sites, but it waits for a reviewer
    assert [d["tactic"] for d in store.list("fraud_drafts", review="pending")] == ["Third tactic mike"]
    fraud_pipeline.resume_adding(KEY)
    assert not fraud_pipeline.adding_paused(KEY)


def test_a_retired_tactic_seeds_no_dreaming_and_its_sign_reaches_no_check(stub):
    stub.tactics = [tactic("Registrar office insider kilo")]
    research()
    [item] = store.list("fraud_auto")
    fraud_pipeline.remove_added(item["id"])
    guide = fraud_guides.current(KEY)
    assert item["id"] not in {t.id for t in fraud_pipeline._seeds(guide)}
    assert f"{item['id']}_sign" not in {s.id for s in fraud_guides.signs_for_check(guide)}


def test_the_portal_lists_added_items_and_only_reviewers_can_remove_or_resume(client, stub):  # noqa: F811
    stub.tactics = [tactic("Registrar office insider kilo")]
    research()
    [item] = store.list("fraud_auto")
    page = client.get("/api/platform/fraud", headers=h("judge")).json()
    assert [a["id"] for a in page["added"]] == [item["id"]] and page["adding_paused"] == []
    body = {"reason": "Looked at the sources"}
    assert client.post(f"/api/platform/fraud/added/{item['id']}/remove", json=body, headers=h("judge")).status_code == 403
    assert client.post(f"/api/platform/fraud/added/{item['id']}/keep", json=body, headers=h("risk")).status_code == 200
    assert not store.get("fraud_auto", item["id"])["spot_check"]
    assert client.post(f"/api/platform/fraud/added/{item['id']}/remove", json=body, headers=h("risk")).status_code == 200
    assert client.post(f"/api/platform/fraud/guides/{KEY}/resume-adding", json=body, headers=h("risk")).status_code == 200


# ---- Dreaming takes each tactic in turn; gaps go to the team -----------------------------------------


def test_dreaming_takes_tactics_never_dreamed_on_first_in_the_order_they_joined(stub):
    guide = fraud_guides.current(KEY)
    first = [t.id for t in fraud_pipeline._seeds(guide)]
    dream()
    second = [t.id for t in fraud_pipeline._seeds(fraud_guides.current(KEY))]
    assert first and not set(first) & set(second)  # the next run takes the next tactics


def test_a_gap_is_reported_to_the_team_and_never_fed_back_to_the_dreamer(stub):
    stub.coverage = "nothing on the list"
    stub.imagined = [scenario("An impostor recipient papa")]
    dream()
    gaps = [e for e in store.list("guardian_events") if e["id"].startswith("gev_gap_")]
    assert len(gaps) == 1 and gaps[0]["user_key"] == "service" and "certificate checks" in gaps[0]["reason"]
    assert not any("gap" in str(e["reason"]).lower() for e in fraud_pipeline._recent_events())


def test_the_cycle_endpoint_still_runs_research_then_dreaming(monkeypatch):
    ran = []

    async def fake(run):
        ran.append(run["kind"])
        return fraud_pipeline._finish(run, {})

    monkeypatch.setattr(fraud_pipeline, "research", fake)
    monkeypatch.setattr(fraud_pipeline, "dream", fake)
    asyncio.run(fraud_pipeline.cycle())
    assert ran == ["research", "dream"]
