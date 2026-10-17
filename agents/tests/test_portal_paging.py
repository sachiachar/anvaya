"""The admin portal's paged lists: one page at a time, search, filters, and scoping before counting."""

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, main
from app.config import settings
from app.governance import core as governance
from app.governance import paging
from app.store import store

TOKENS = {
    "judge": {"sub": "pg_judge", "email": "judge@gmail.com", "email_verified": True},
}


def q(page=1, size=20, text="", date_from=None, date_to=None):
    return paging.page_query(page=page, size=size, q=text, date_from=date_from, date_to=date_to)


# ---- The helper ------------------------------------------------------------------------


def test_a_page_says_where_it_sits_and_a_page_past_the_end_shows_the_last():
    rows = [{"n": i} for i in range(45)]
    first = paging.page(rows, q(size=20))
    assert [r["n"] for r in first["items"]][:2] == [0, 1] and first["page"] == {"page": 1, "size": 20, "total": 45, "pages": 3}
    last = paging.page(rows, q(page=9, size=20))
    assert last["page"]["page"] == 3 and len(last["items"]) == 5
    empty = paging.page([], q())
    assert empty["items"] == [] and empty["page"] == {"page": 1, "size": 20, "total": 0, "pages": 1}


def test_search_needs_every_word_in_any_listed_field_and_ignores_case():
    rows = [{"a": "Another VOICE was heard", "b": None}, {"a": "voice check", "b": ["held", {"x": "Mismatch"}]},
            {"a": "other", "secret": "voice"}]
    assert len(paging.search(rows, "voice", ("a", "b"))) == 2
    assert paging.search(rows, "voice mismatch", ("a", "b")) == [rows[1]]
    assert paging.search(rows, "voice", ("a",)) == rows[:2]  # an unlisted field is never searched
    assert paging.search(rows, "   ", ("a",)) == rows


def test_dates_cover_whole_days_in_utc_and_bad_dates_are_refused():
    day = q(date_from="2026-10-17", date_to="2026-10-17")
    rows = [{"at": day.since - 1}, {"at": day.since}, {"at": day.until - 1}, {"at": day.until}, {"no_time": 1}]
    assert paging.dated(rows, day) == rows[1:3]
    assert paging.dated(rows, q()) == rows  # no dates set: every row
    with pytest.raises(HTTPException) as bad:
        q(date_from="17/10/2026")
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException):
        q(date_from="2026-10-18", date_to="2026-10-17")


def test_filters_and_counts():
    rows = [{"s": "open"}, {"s": "open"}, {"s": "reversed"}, {"s": None}]
    assert paging.where(rows, s="open") == rows[:2]
    assert paging.where(rows, s=None) == rows  # not applied
    assert paging.where(rows, s=lambda r: r["s"] != "open") == rows[2:]
    assert paging.counts(rows, "s") == {"open": 2, "reversed": 1}


# ---- The routes, as the Anvaya team (sign-in off) ----------------------------------------------


@pytest.fixture()
def team():
    return TestClient(main.app)


def _events(agent: str, n: int, start: float, **extra):
    for i in range(n):
        store.put("guardian_events", {"id": f"gev_{agent}_{i}", "at": start + i, "agent": agent, "response": "add_check",
                                      "reason": f"pager test {agent} number {i}", "case_key": None,
                                      "user_key": "u_pager", "status": "open", **extra})


def test_guardian_events_page_by_page_with_filters_and_search(team):
    _events("test.pager", 45, start=time.time())
    r = team.get("/api/platform/guardian/events", params={"agent": "test.pager", "size": 20})
    body = r.json()
    assert r.status_code == 200 and body["page"]["total"] == 45 and body["page"]["pages"] == 3
    assert body["items"][0]["reason"].endswith("number 44")  # newest first
    third = team.get("/api/platform/guardian/events", params={"agent": "test.pager", "size": 20, "page": 3}).json()
    assert len(third["items"]) == 5
    found = team.get("/api/platform/guardian/events", params={"agent": "test.pager", "q": "NUMBER 44"}).json()
    assert {e["id"] for e in found["items"]} == {"gev_test.pager_44"}  # every word, any case, part of a word
    decided = team.get("/api/platform/guardian/events", params={"agent": "test.pager", "status": "decided"}).json()
    assert decided["page"]["total"] == 0
    assert "test.pager" in body["choices"]["agent"] and body["counts"]["waiting"] >= 45


def test_page_size_is_capped_and_bad_values_are_refused(team):
    assert team.get("/api/platform/guardian/events", params={"size": 101}).status_code == 422
    assert team.get("/api/platform/guardian/events", params={"page": 0}).status_code == 422
    assert team.get("/api/platform/guardian/events", params={"status": "maybe"}).status_code == 422
    assert team.get("/api/platform/audit/entries", params={"from": "yesterday"}).status_code == 422
    assert team.get("/api/platform/audit/entries", params={"q": "x" * 101}).status_code == 422


def test_audit_has_no_200_cap_any_more(team):
    now = time.time()
    for i in range(230):
        store.put("platform_audit", {"id": f"aud_pg_{i}", "at": now + i, "actor": "team@example.com",
                                     "actor_key": "u_team", "role": "platform_admin", "action": "pager.test",
                                     "target": f"t{i}", "reason": "r", "detail": {}})
    body = team.get("/api/platform/audit/entries", params={"action": "pager.test", "size": 100, "page": 3}).json()
    assert body["page"]["total"] == 230 and len(body["items"]) == 30


def test_fraud_runs_filter_by_kind_and_date(team):
    store.put("fraud_runs", {"id": "run_pg_old", "kind": "research", "guide_key": "g", "status": "done",
                             "started_at": 1_700_000_000, "counts": {}, "calls": {}})
    store.put("fraud_runs", {"id": "run_pg_new", "kind": "dream", "guide_key": "g", "status": "done",
                             "started_at": time.time(), "counts": {}, "calls": {}})
    dreams = team.get("/api/platform/fraud/runs", params={"kind": "dream", "q": "dream"}).json()
    assert "run_pg_new" in {r["id"] for r in dreams["items"]} and "run_pg_old" not in {r["id"] for r in dreams["items"]}
    old = team.get("/api/platform/fraud/runs", params={"from": "2023-11-14", "to": "2023-11-14"}).json()
    assert [r["id"] for r in old["items"]] == ["run_pg_old"]


# ---- Scoping: a judge's pages and counts are their own only -------------------------------------


@pytest.fixture()
def judge(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_firebase_token", lambda token, request, audience=None: TOKENS[token])
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "open_sign_in", True)
    monkeypatch.setattr(settings, "allowed_emails", [])
    client = TestClient(main.app)
    client.headers["Authorization"] = "Bearer judge"
    return client


def test_a_judge_pages_and_counts_only_their_own_copy(judge):
    mine = governance.user_key(TOKENS["judge"]["sub"])
    now = time.time()
    for i in range(3):
        store.put("guardian_events", {"id": f"gev_pg_mine_{i}", "at": now + i, "agent": "pager.scope",
                                      "response": "add_check", "reason": "mine", "case_key": None,
                                      "user_key": mine, "status": "open"})
    for i in range(30):
        store.put("guardian_events", {"id": f"gev_pg_other_{i}", "at": now + i, "agent": "pager.scope",
                                      "response": "block", "reason": "someone else", "case_key": None,
                                      "user_key": "u_someone_else", "status": "reversed"})
    store.put("platform_audit", {"id": "aud_pg_other", "at": now, "actor": "team@example.com", "actor_key": "u_team",
                                 "role": "platform_admin", "action": "pager.scope", "target": "x", "reason": "r", "detail": {}})
    assert judge.get("/api/platform/me").json()["role"] == "demo_admin"
    body = judge.get("/api/platform/guardian/events", params={"agent": "pager.scope"}).json()
    assert body["page"]["total"] == 3 and {e["user_key"] for e in body["items"]} == {mine}
    # The counts and filter choices come from their own rows: nothing reveals the other 30.
    assert body["counts"]["mistake"] == 0 and "block" not in body["choices"]["response"]
    assert judge.get("/api/platform/guardian/events", params={"q": "someone else"}).json()["page"]["total"] == 0
    assert judge.get("/api/platform/audit/entries", params={"action": "pager.scope"}).json()["page"]["total"] == 0
    assert judge.get("/api/platform/demos/copies").json()["nicknames_visible"] is False
