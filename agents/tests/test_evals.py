"""Measured quality (PR-15.5), without the cloud: the case files are well formed, the synthetic
certificates are valid PDFs, the scoring is right, and the whole run works end to end with Gemini
stubbed out. The real run (evals/run.py) is for a person, with a Gemini key."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from app import executor_agent, fraud_guides, loops
from app.schemas import ConversationTurn, DocumentCheck, Moment
from evals import run, score


def test_the_cases_are_well_formed():
    guide = fraud_guides.current(fraud_guides.key_for("death certificate", "India"))
    signs = {s.id for s in fraud_guides.signs_for_check(guide)}
    certs = score.load("certificates")["cases"]
    assert {c["expect"] for c in certs} == {"genuine", "forgery", "wrong_person", "wrong_type"}
    assert all(c["sign"] in signs for c in certs if c["expect"] == "forgery")  # the guide's own signs
    questions = score.load("questions")
    keys = {k for items in questions["kept"].values() for k in items}
    for q in questions["questions"]:
        assert q["expect"] == "never" or set(q["expect"]) <= keys, q
    assert sum(q["expect"] == "never" for q in questions["questions"]) >= 5
    for case in score.load("cards")["cases"]:
        assert isinstance(case["forbidden"], list) and all(isinstance(g, list) for g in case["expected"])


def test_a_synthetic_certificate_is_a_valid_pdf_with_its_details():
    data = score.certificate({"name": "Lakshmi Narayanan"})
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"Lakshmi Narayanan" in data and b"crsorgi.gov.in" in data
    xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
    offsets = [int(o) for o in re.findall(rb"(\d{10}) 00000 n", data[xref:])]
    for number, offset in enumerate(offsets, start=1):  # every object is where the index says
        assert data[offset:].startswith(b"%d 0 obj" % number)
    altered = score.certificate({"name_font": "mono", "name": "Ramesh   Kulkarni"})
    assert b"/F3 12 Tf" in altered  # the name in Courier, as if pasted in


def test_certificate_scoring():
    forged = {"id": "f", "expect": "forgery", "sign": "dates_impossible"}
    flagged = {"verdict": "needs_attention", "signs": [{"id": "dates_impossible", "result": "found"}]}
    assert score.score_certificate(forged, flagged)["passed"] and score.score_certificate(forged, flagged)["sign_named"]
    assert not score.score_certificate(forged, {"verdict": "likely_valid", "signs": []})["passed"]
    assert score.score_certificate({"id": "g", "expect": "genuine"}, {"verdict": "likely_valid"})["passed"]
    assert not score.score_certificate({"id": "t", "expect": "wrong_type"}, {"verdict": "needs_attention"})["passed"]


def test_question_scoring():
    assert score.score_question({"q": "q", "expect": ["will"]}, "In 2019.", ["will"], 0)["passed"]
    assert not score.score_question({"q": "q", "expect": ["will"]}, "In 2019.", ["papers"], 0)["passed"]
    never = {"q": "q", "expect": "never"}
    assert score.score_question(never, "Vikram never recorded anything about that.", [], 0)["passed"]
    assert not score.score_question(never, "He had a loan with SBI.", ["papers"], 0)["passed"]


def test_card_scoring():
    case = {"id": "c", "forbidden": ["steel cupboard"], "expected": [["almirah", "cupboard"], ["LIC"]]}
    row = score.score_cards(case, "papers in the steel cupboard (lic)", "papers in the almirah (lic)")
    assert row["unsaid_before_check"] == ["steel cupboard"] and row["unsaid_after_check"] == []
    assert row["said_and_kept"] == "2/2"


@pytest.fixture()
def stubbed(monkeypatch, tmp_path):
    """Gemini stubbed out everywhere the run calls it; results written to a temporary folder."""
    async def check_document(image, mime, expected, person, guide):
        assert image.startswith(b"%PDF") and mime == "application/pdf"
        return DocumentCheck(document_type="death certificate", is_expected_type=True, matches_expected_person=True,
                             confidence=0.95)

    async def ask(owner, user, question, session_id=None):
        return {"answer": f"{owner['name']} never recorded anything about that.", "sources": [], "session_id": "hs"}

    async def converse(owner, last, text, media, known):
        return ConversationTurn(reply="Thank you.", reply_language="en", owner_language="en", heard="",
                                moments=[Moment(kind="practical", title="Papers", summary=f"{text} In the steel cupboard.",
                                                quote=text)], inventory=[], people=[])

    monkeypatch.setattr(loops, "check_document", check_document)
    monkeypatch.setattr(executor_agent, "ask", ask)
    monkeypatch.setattr(loops, "converse", converse)
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")
    return tmp_path


def test_the_whole_run_works_end_to_end_with_gemini_stubbed(stubbed):
    assert asyncio.run(run.main(["certificates", "questions", "cards"])) == 0
    [written] = list(stubbed.glob("*.json"))
    results = json.loads(written.read_text())
    certs = results["steps"]["certificates"]["totals"]
    assert certs["genuine_passed"]["share"] == 1.0 and certs["forgeries_caught"]["count"] == 0  # the stub flags nothing
    assert results["steps"]["questions"]["totals"]["says_never_recorded"]["share"] == 1.0
    assert results["steps"]["cards"]["model"]
    assert (stubbed / written.name.replace(".json", ".md")).read_text().startswith("# Measured quality")


def test_the_run_needs_a_key_and_never_prints_it(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert asyncio.run(run.main(["cards"])) == 2
    assert "GOOGLE_API_KEY" in capsys.readouterr().out


def test_kept_items_for_the_questions_load_offline():
    data = score.load("questions")
    owner = {"id": "owner_evals_offline", "name": "Vikram Rao"}
    from app import knowledge
    from app.store import new_id
    item = data["kept"]["moments"]["papers"]
    kept = knowledge.commit("moments", {**item, "id": new_id("m")}, owner["id"])
    assert kept["id"].startswith("m_") and SimpleNamespace(**kept).title == "Where the papers are"
