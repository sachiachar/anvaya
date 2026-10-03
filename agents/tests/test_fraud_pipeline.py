"""Fraud foresight pipeline (PR-2.5, PR-2.6): imagine, check, map, drafts. Gemini and Google
Search are stubbed; the rules are checked in code (docs/proposals/fraud-foresight.md §4)."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app import fraud_guides, fraud_pipeline, gemini
from app.fraud_guides import BUILT_IN, Step, Tactic
from app.fraud_pipeline import (
    Coverage,
    DocumentSign,
    Imagined,
    ImaginedScenario,
    NewStep,
    NewTactic,
    NewTactics,
    judge,
)
from app.gemini import Grounded
from app.governance import core
from app.store import store
from tests.test_governance import client, h  # noqa: F401  (the portal fixture, signed-in roles)

KEY = "IN.death_certificate"
COLLECTIONS = ("fraud_guides", "fraud_guide_versions", "fraud_scenarios", "fraud_drafts", "fraud_runs",
               "fraud_run_lock", "fraud_decisions", "guardian_events")
CLAIM = "Fraudsters copied the QR code from a genuine death certificate and changed the printed name"
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo"]
BACKED = ("YES\nPolice in Gurugram found fraudsters copied the QR code from a genuine death certificate "
          "and changed the printed name.")


@pytest.fixture(autouse=True)
def clean():
    for collection in COLLECTIONS:
        for doc in store.list(collection):
            store.delete(collection, doc["id"])


def grounded(text=BACKED, supported=True):
    segment = text.splitlines()[-1]
    return Grounded(text=text, sources=[("tribuneindia.com", "https://example.test/a"),
                                        ("freepressjournal.in", "https://example.test/b")],
                    supports=[(segment, [0, 1])] if supported else [])


def scenario(title, variant="close", targets="certificate checks", sign=False):
    return ImaginedScenario(title=title, steps=["step one", "step two"], targets=targets, variant=variant,
                            claim=CLAIM, document_sign=DocumentSign(look_for="QR shows another name",
                                                                    genuine="QR matches") if sign else None)


class Stub:
    """Stands in for Gemini: records every prompt and answers by schema."""

    def __init__(self, imagined=None, coverage="two reviewers", confirm=True, tactics=None):
        self.prompts: list[str] = []
        self.imagined = imagined
        self.coverage = coverage
        self.confirm = confirm
        self.tactics = tactics or []
        self.made = 0

    def title(self) -> str:
        """A new title each time, with no words in common with earlier ones."""
        self.made += 1
        return f"Attack {WORDS[self.made % len(WORDS)]}{self.made} {WORDS[(self.made * 7) % len(WORDS)]}{self.made}"

    async def generate_json(self, *, schema, prompt, agent, **kw):
        self.prompts.append(prompt)
        if schema is Imagined:
            assert agent == "fraud.dreamer"
            if self.imagined is not None:
                return Imagined(scenarios=self.imagined)
            return Imagined(scenarios=[scenario(self.title()), scenario(self.title()), scenario(self.title()),
                                       scenario(self.title(), "far"), scenario(self.title(), "far"),
                                       scenario(self.title(), targets="retina scan")])
        if schema is Coverage:
            return Coverage(caught_by=self.coverage, partly=False, reason="because")
        if schema is NewTactics:
            assert agent == "fraud.researcher"
            return NewTactics(tactics=self.tactics)
        raise AssertionError(schema)

    async def grounded_text(self, *, prompt, agent, **kw):
        assert agent == "fraud.researcher"
        self.prompts.append(prompt)
        return grounded() if self.confirm else grounded("NO\nNothing reported.", supported=False)


@pytest.fixture()
def stub(monkeypatch):
    s = Stub()
    monkeypatch.setattr(fraud_pipeline, "generate_json", s.generate_json)
    monkeypatch.setattr(fraud_pipeline, "grounded_text", s.grounded_text)
    return s


def dream():
    return asyncio.run(fraud_pipeline.dream(fraud_pipeline.start_run("dream", KEY, "test")))


def research():
    return asyncio.run(fraud_pipeline.research(fraud_pipeline.start_run("research", KEY, "test")))


# ---- Rule 3: "confirmed" means a source supports the claim ---------------------------


def test_a_supported_yes_is_confirmed_with_its_sources():
    g = grounded()
    e = judge(CLAIM, g.text, g.sources, g.supports)
    assert e.confirmed and [s["url"] for s in e.sources] == ["https://example.test/a", "https://example.test/b"]


def test_no_is_never_confirmed_even_with_sources():
    g = grounded("NO\n" + BACKED.splitlines()[1])
    assert not judge(CLAIM, g.text, g.sources, g.supports).confirmed


def test_a_yes_without_grounding_support_is_not_confirmed():
    g = grounded(supported=False)
    assert not judge(CLAIM, g.text, g.sources, g.supports).confirmed


def test_a_source_that_only_mentions_a_word_is_not_enough():
    text = "YES\nA news site mentions certificates in an unrelated story about school admissions this year."
    assert not judge(CLAIM, text, grounded().sources, [(text.splitlines()[1], [0])]).confirmed
    assert not judge(CLAIM, "YES\nQR code.", grounded().sources, [("QR code.", [0])]).confirmed


def test_source_indexes_outside_the_list_are_ignored():
    g = grounded()
    e = judge(CLAIM, g.text, g.sources, [(g.supports[0][0], [7])])
    assert not e.confirmed


# ---- Dreaming ------------------------------------------------------------------------


def test_a_run_keeps_two_close_and_one_far_variant_per_sequence_within_limits(stub):
    run = dream()
    assert run["status"] == "done"
    scenarios = store.list("fraud_scenarios", guide_key=KEY)
    assert run["counts"]["sequences"] == 2 and len(scenarios) == 6
    for seq in {s["sequence_id"] for s in scenarios}:
        mine = [s for s in scenarios if s["sequence_id"] == seq]
        assert sorted(s["variant"] for s in mine) == ["close", "close", "far"]
    assert all(s["safeguard"] in fraud_pipeline.SAFEGUARDS for s in scenarios)
    assert run["limits"] == {"max_sequences_per_run": 2, "max_scenarios_per_sequence": 3,
                             "max_new_patterns_per_run": 2, "fraud.dreamer.max_model_calls_per_run": 8,
                             "fraud.researcher.max_model_calls_per_run": 6}
    assert run["calls"] == {"fraud.dreamer": 8, "fraud.researcher": 6}


def test_confirmed_scenarios_wait_for_review_and_unconfirmed_ones_dont(stub):
    dream()
    assert {s["review"] for s in store.list("fraud_scenarios")} == {"pending"}
    assert all(s["label"] == "simulated" and s["evidence"] == 2 for s in store.list("fraud_scenarios"))
    clean_all()
    stub.confirm = False
    dream()
    assert {s["review"] for s in store.list("fraud_scenarios")} == {"unconfirmed"}


def clean_all():
    for doc in store.list("fraud_scenarios"):
        store.delete("fraud_scenarios", doc["id"])


def test_a_safeguard_outside_the_fixed_list_counts_as_a_gap(stub):
    stub.coverage = "a guard dog"
    dream()
    assert {(s["caught_by"], s["coverage"]) for s in store.list("fraud_scenarios")} == {(None, "gap")}


def test_imagined_tactics_never_seed_new_scenarios(stub, monkeypatch):
    dreamt = Tactic(id="dreamt", name="An imagined tactic zulu", label="simulated", trust="unverified",
                    steps=[Step(text="x", caught_by="qr_mismatch")])
    guide = BUILT_IN[KEY].model_copy(update={"tactics": [dreamt, *BUILT_IN[KEY].tactics]})
    monkeypatch.setitem(BUILT_IN, KEY, guide)
    dream()
    assert not any("imagined tactic zulu" in p for p in stub.prompts)
    assert not any(s["sequence_id"] == "dreamt" for s in store.list("fraud_scenarios"))


def test_least_dreamed_sequences_go_first(stub):
    dream()
    first = {s["sequence_id"] for s in store.list("fraud_scenarios")}
    dream()
    second = {s["sequence_id"] for s in store.list("fraud_scenarios")} - first
    assert first and second and not first & second


def test_the_dreamer_sees_events_without_case_keys_or_owner_data(stub):
    core.record_intervention("activation.checker", "pause", "Same certificate used in another case", "case_secret_42")
    store.put("owners", {"id": "owner_x", "name": "Zubin Testname"})
    dream()
    joined = "\n".join(stub.prompts)
    assert "Same certificate used in another case" in joined
    assert "case_secret_42" not in joined and "Zubin" not in joined


def test_rejected_scenarios_go_back_as_ruled_out(stub):
    store.put("fraud_scenarios", {"id": "scn_old", "at": time.time(), "guide_key": KEY, "scenario": "Old idea yankee",
                                  "label": "invalidated", "review": "discarded", "coverage": "gap"})
    dream()
    assert any("Old idea yankee" in p for p in stub.prompts if "Already ruled out" in p)


def test_scenarios_like_existing_ones_are_skipped(stub):
    stub.imagined = [scenario("Copy the QR code from a real certificate"), scenario("Copy the QR code from a real certificate")]
    dream()
    assert len(store.list("fraud_scenarios")) == 1  # the same idea is kept once, across sequences too


def test_unconfirmed_scenarios_fade_after_three_runs(stub):
    stub.confirm = False
    dream()
    first = {s["id"] for s in store.list("fraud_scenarios")}
    for _ in range(3):
        stub.imagined = []
        dream()
    assert {s["id"] for s in store.list("fraud_scenarios", review="faded")} == first


def test_one_run_at_a_time_and_a_stuck_run_times_out():
    run = fraud_pipeline.start_run("dream", KEY, "test")
    with pytest.raises(fraud_pipeline.Busy):
        fraud_pipeline.start_run("research", KEY, "test")
    store.update("fraud_runs", run["id"], {"started_at": time.time() - 16 * 60})  # e.g. the server restarted
    store.update("fraud_run_lock", "active", {"at": time.time() - 16 * 60})
    assert fraud_pipeline.start_run("research", KEY, "test")["status"] == "running"
    assert store.get("fraud_runs", run["id"])["status"] == "failed"


def test_a_failure_is_recorded_on_the_run_without_its_content(monkeypatch):
    async def boom(**kw):
        raise ValueError("model said something with a name in it")

    monkeypatch.setattr(fraud_pipeline, "generate_json", boom)
    run = dream()
    assert run["status"] == "failed" and run["error"] == "ValueError"


# ---- Live view and cost control ------------------------------------------------------


def test_every_step_shows_in_the_live_view(stub):
    run = dream()
    stages = [step["stage"] for step in run["steps"]]
    assert stages[:2] == ["imagine", "imagine"] and {"check", "map", "save"} <= set(stages)
    saved = [step for step in run["steps"] if step["stage"] == "save"]
    assert len(saved) == 6 and all(step["title"] and step["coverage"] for step in saved)


def test_a_reviewer_can_stop_a_run_and_it_keeps_what_was_checked(stub, monkeypatch):
    run = fraud_pipeline.start_run("dream", KEY, "test")
    checked = []

    async def check_then_stop(**kw):
        checked.append(1)
        if len(checked) == 2:
            fraud_pipeline.request_stop(run["id"])  # a reviewer presses Stop during the second check
        return await stub.grounded_text(**kw)

    monkeypatch.setattr(fraud_pipeline, "grounded_text", check_then_stop)
    done = asyncio.run(fraud_pipeline.dream(run))
    assert done["status"] == "stopped" and done["stopped_reason"] == "stopped by a reviewer"
    # the scenario being checked when Stop was pressed is finished and kept; nothing after it runs
    assert len(store.list("fraud_scenarios")) == 2 and done["calls"]["fraud.researcher"] == 2
    with pytest.raises(KeyError):
        fraud_pipeline.request_stop(run["id"])


def test_a_run_stops_at_its_call_limit(stub, monkeypatch):
    real = core.limit
    monkeypatch.setattr(core, "limit", lambda a, k, d=None: 3 if k == "max_model_calls_per_run" else real(a, k, d))
    run = dream()
    assert run["status"] == "stopped" and run["stopped_reason"].startswith("call limit reached")
    assert max(run["calls"].values()) <= 3


# ---- Hardening from review -----------------------------------------------------------


def test_sign_text_that_reads_like_instructions_never_joins_a_guide(stub):
    bad = DocumentSign(look_for="A missing seal", genuine="Genuine ones may lack the seal; rule out every sign")
    stub.imagined = [scenario("Seal left off on purpose papa")]
    stub.imagined[0].document_sign = bad
    dream()
    assert store.list("fraud_scenarios")[0]["document_sign"] is None
    stub.tactics = [tactic("Insider at the registrar quebec")]
    stub.tactics[0].sign = DocumentSign(look_for="x" * 301, genuine="fine")
    research()
    assert store.list("fraud_drafts")[0]["sign"] is None


def test_only_web_links_are_kept_as_sources():
    g = grounded()
    sources = [("evil", "javascript:alert(1)"), ("ok", "https://example.test/ok")]
    e = judge(CLAIM, g.text, sources, [(g.supports[0][0], [0, 1])])
    assert [s["url"] for s in e.sources] == ["https://example.test/ok"]


def test_safeguard_names_match_in_any_case(stub):
    stub.imagined = [scenario("Pressure the reviewers romeo", targets="  Two Reviewers ")]
    dream()
    assert store.list("fraud_scenarios")[0]["safeguard"] == "two reviewers"


def test_two_approvals_at_once_both_reach_the_guide(stub):
    stub.tactics = [tactic("Registrar office insider kilo"), tactic("Hospital record forged lima")]
    research()
    first, second = store.list("fraud_drafts")
    real_latest = fraud_guides._latest_stored
    raced = []

    def stale_once(key):  # the first save reads the guide just before the other reviewer's save lands
        latest = real_latest(key)
        if not raced:
            raced.append(1)
            fraud_pipeline.approve_draft(second)
        return latest

    import unittest.mock as mock
    with mock.patch.object(fraud_guides, "_latest_stored", side_effect=stale_once):
        fraud_pipeline.approve_draft(first)
    names = {t.name for t in fraud_guides.current(KEY).tactics}
    assert {"Registrar office insider kilo", "Hospital record forged lima"} <= names


def test_the_same_scenario_cant_be_decided_twice(client, stub):  # noqa: F811
    client.post(f"/api/platform/fraud/guides/{KEY}/dream", headers=h("risk"))
    scn = client.get("/api/platform/fraud", headers=h("risk")).json()["scenarios"][0]
    store.create("fraud_decisions", {"id": scn["id"]})  # another reviewer's decision got there first
    url = f"/api/platform/fraud/scenarios/{scn['id']}/decision"
    assert client.post(url, headers=h("risk"), json={"decision": "approve", "reason": "seen it"}).status_code == 409


def test_a_stuck_run_is_cleared_when_the_page_loads(client):  # noqa: F811
    run = fraud_pipeline.start_run("dream", KEY, "test")
    store.update("fraud_runs", run["id"], {"started_at": time.time() - 16 * 60})
    store.update("fraud_run_lock", "active", {"at": time.time() - 16 * 60})
    page = client.get("/api/platform/fraud", headers=h("judge")).json()
    assert page["runs"][0]["status"] == "failed" and store.get("fraud_run_lock", "active") is None


# ---- Research ------------------------------------------------------------------------


def tactic(name, claim=CLAIM):
    return NewTactic(name=name, steps=[NewStep(text="do a thing", caught_by="qr_mismatch")],
                     sign=DocumentSign(look_for="a new mark", genuine="no such mark"), claim=claim)


def test_research_drafts_at_most_two_confirmed_tactics_and_changes_no_guide(stub):
    stub.tactics = [tactic("Registrar office insider kilo"), tactic("Forged hospital record lima"),
                    tactic("Third tactic mike")]
    run = research()
    drafts = store.list("fraud_drafts")
    assert run["counts"] == {"found": 2, "confirmed": 2, "unconfirmed": 0}
    assert {d["tactic"] for d in drafts} == {"Registrar office insider kilo", "Forged hospital record lima"}
    assert all(d["review"] == "pending" and d["label"] == "derived" and d["sources"] for d in drafts)
    assert fraud_guides.current(KEY).version == 1  # nothing changes until a reviewer approves


def test_research_skips_known_and_unconfirmed_tactics(stub):
    stub.tactics = [tactic("A cheap template with a replica signature"), tactic("New one november")]
    stub.confirm = False
    run = research()
    assert run["counts"] == {"found": 1, "confirmed": 0, "unconfirmed": 1}
    assert store.list("fraud_drafts") == []


def test_approving_a_draft_adds_a_human_reviewed_tactic_and_sign(stub):
    stub.tactics = [tactic("Registrar office insider kilo")]
    research()
    guide = fraud_pipeline.approve_draft(store.list("fraud_drafts")[0])
    added = guide.tactics[-1]
    assert added.name == "Registrar office insider kilo" and added.trust == "human-reviewed"
    assert guide.signs[-1].id == f"{added.id}_sign" and guide.signs[-1].trust == "human-reviewed"
    assert set(added.sources) <= {s.id for s in guide.sources}
    assert guide.version == fraud_guides.current(KEY).version >= 1


def test_approving_a_scenario_with_a_document_mark_adds_a_projection_sign(stub):
    stub.imagined = [scenario("A copied QR code oscar", sign=True)]
    dream()
    scn = store.list("fraud_scenarios")[0]
    guide = fraud_pipeline.approve_scenario(scn)
    sign = next(s for s in guide.signs if s.id == scn["id"])
    assert sign.projection and sign.label == "validated" and sign.trust == "human-reviewed"
    assert guide.scenarios[-1]["id"] == scn["id"]
    assert sign in fraud_guides.signs_for_check(guide)  # 8 known signs allow up to 4 projections


# ---- The gateway: only the researcher may search -------------------------------------


def test_only_the_researcher_may_use_google_search():
    core.check_tool("fraud.researcher", "google_search")
    with pytest.raises(core.Refused):
        core.check_tool("fraud.dreamer", "google_search")
    assert any(b["agent"] == "fraud.dreamer" and b["kind"] == "tool" for b in store.list("gateway_blocked"))


def test_grounded_text_returns_sources_and_supports(monkeypatch):
    web = SimpleNamespace
    meta = web(grounding_chunks=[web(web=web(title="tribuneindia.com", uri="https://example.test/a"))],
               grounding_supports=[web(segment=web(text="copied the QR code"), grounding_chunk_indices=[0])])
    response = web(text="YES\ncopied the QR code", candidates=[web(grounding_metadata=meta)],
                   usage_metadata=web(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0))
    seen = {}

    async def generate_content(**kw):
        seen.update(kw)
        return response

    fake = web(aio=web(models=web(generate_content=generate_content)))
    monkeypatch.setattr(gemini, "client", lambda: fake)
    out = asyncio.run(gemini.grounded_text(agent="fraud.researcher", model="gemini-3.8-flash",
                                           prompt="Is it reported?", system="Check."))
    assert out.sources == [("tribuneindia.com", "https://example.test/a")]
    assert out.supports == [("copied the QR code", [0])]
    assert seen["config"].tools[0].google_search is not None
    with pytest.raises(core.Refused):
        asyncio.run(gemini.grounded_text(agent="fraud.dreamer", model="gemini-3.8-flash", prompt="x", system="y"))


# ---- The portal ----------------------------------------------------------------------


def test_portal_runs_dreaming_and_a_reviewer_decides(client, stub):  # noqa: F811
    assert client.post(f"/api/platform/fraud/guides/{KEY}/dream", headers=h("judge")).status_code == 403
    r = client.post(f"/api/platform/fraud/guides/{KEY}/dream", headers=h("risk"))
    assert r.status_code == 200
    page = client.get("/api/platform/fraud", headers=h("judge")).json()
    assert page["runs"][0]["status"] == "done" and len(page["scenarios"]) == 6
    scn = page["scenarios"][0]
    url = f"/api/platform/fraud/scenarios/{scn['id']}/decision"
    decision = {"decision": "approve", "reason": "Matches the Gurugram case"}
    assert client.post(url, headers=h("risk"), json=decision).json()["label"] == "validated"
    assert any(s["id"] == scn["id"] for s in fraud_guides.current(KEY).scenarios)


def test_portal_never_approves_an_unconfirmed_scenario(client, stub):  # noqa: F811
    stub.confirm = False
    client.post(f"/api/platform/fraud/guides/{KEY}/dream", headers=h("risk"))
    scn = client.get("/api/platform/fraud", headers=h("risk")).json()["scenarios"][0]
    url = f"/api/platform/fraud/scenarios/{scn['id']}/decision"
    assert client.post(url, headers=h("risk"), json={"decision": "approve", "reason": "looks right"}).status_code == 409
    discard = {"decision": "discard", "reason": "not plausible"}
    assert client.post(url, headers=h("risk"), json=discard).json()["label"] == "invalidated"


def test_portal_refuses_a_second_run_while_one_is_going_and_can_stop_it(client):  # noqa: F811
    run = fraud_pipeline.start_run("dream", KEY, "test")
    assert client.post(f"/api/platform/fraud/guides/{KEY}/research", headers=h("risk")).status_code == 409
    assert client.post(f"/api/platform/fraud/runs/{run['id']}/stop", headers=h("judge")).status_code == 403
    assert client.post(f"/api/platform/fraud/runs/{run['id']}/stop", headers=h("risk")).status_code == 200
    assert store.get("fraud_runs", run["id"])["stop_requested"]


def test_portal_approves_a_researched_draft(client, stub):  # noqa: F811
    stub.tactics = [tactic("Registrar office insider kilo")]
    client.post(f"/api/platform/fraud/guides/{KEY}/research", headers=h("risk"))
    draft = client.get("/api/platform/fraud", headers=h("risk")).json()["drafts"][0]
    r = client.post(f"/api/platform/fraud/drafts/{draft['id']}/decision", headers=h("risk"),
                    json={"decision": "approve", "reason": "Two reliable sources"})
    assert r.status_code == 200
    assert fraud_guides.current(KEY).tactics[-1].name == "Registrar office insider kilo"
    assert store.get("fraud_guides", KEY)["status"] == "current"
