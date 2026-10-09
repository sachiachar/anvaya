"""The parts of the quality measurement that need no cloud: loading the cases, drawing the synthetic
certificates, and scoring answers. evals/run.py calls Gemini and uses these; the unit tests check them."""

from __future__ import annotations

import functools
import json
import pathlib
import re

from . import pdf

CASES = pathlib.Path(__file__).resolve().parent / "cases"


@functools.cache
def _read(name: str) -> str:
    return (CASES / f"{name}.json").read_text()


def load(name: str) -> dict:
    return json.loads(_read(name))  # a fresh copy each time, from a file read once


# ---- (a) Certificate check -----------------------------------------------------------------


def certificate(details: dict, base: dict | None = None) -> bytes:
    """A synthetic Indian certificate, as a PDF, from the base layout and one case's changes. Death by
    default; `"kind": "birth"` draws a birth certificate (the wrong kind of document)."""
    f = {**(base or load("certificates")["base"]), **details}
    birth = f.get("kind") == "birth"
    who, when, where = ("child", "birth", "birth") if birth else ("deceased", "death", "death")
    title = "BIRTH CERTIFICATE  (Form No. 5" if birth else "DEATH CERTIFICATE  (Form No. 6"
    lines = [
        ("GOVERNMENT OF KARNATAKA", "bold", 13),
        ("DEPARTMENT OF ECONOMICS AND STATISTICS", "sans", 11),
        (f"{title}, Rule 8; Registration of Births and Deaths Act, 1969)", "bold", 12),
        ("", "sans", 6),
        (f"Name of the {who}: {f['name']}" if f["name_font"] == "sans" else f"Name of the {who}:", "sans", 11),
    ]
    if f["name_font"] != "sans":  # the name in another font, as if pasted in
        lines.append((f"        {f['name']}", f["name_font"], 12))
    lines += [
        (f"Sex: {f['sex']}", "sans", 11),
        (f"Date of {when}: {f['date_of_death']}", "sans", 11),
        (f"Place of {where}: {f['place']}", "sans", 11),
        (f"Registration number: {f['registration_no']}", "sans", 11),
        (f"Date of registration: {f['date_of_registration']}", "sans", 11),
        (f"Date of issue: {f['date_of_issue']}", "sans", 11),
        (f"Issuing authority: {f['office']}", "sans", 10),
        ("", "sans", 6),
        (f["signed"], "serif", 10),
        (f["verify"], "sans", 10),
        ("This is a computer-generated certificate.", "sans", 9),
    ]
    return pdf.page([line for line in lines if line[0] or line[2] == 6])


def score_certificate(case: dict, summary: dict) -> dict:
    """One certificate case against the check's summary (loops.review_summary): passed or not, and why."""
    verdict = summary.get("verdict")
    found = [s["id"] for s in summary.get("signs") or [] if s.get("result") == "found"]
    expect = case["expect"]
    if expect == "genuine":
        passed = verdict == "likely_valid"
    elif expect == "wrong_type":
        passed = verdict == "not_valid"
    else:  # a forgery or the wrong person: anything but "likely valid"
        passed = verdict != "likely_valid"
    return {"id": case["id"], "expect": expect, "verdict": verdict, "signs_found": found, "passed": passed,
            "flagged_by_a_sign": bool(found) if expect == "forgery" else None,
            "sign_named": (case.get("sign") in found) if expect == "forgery" else None}


def certificate_totals(rows: list[dict]) -> dict:
    """"Flagged" alone can come from low confidence; "flagged by a sign" and "right sign named" show
    whether the check saw the forgery itself."""
    forgeries = [r for r in rows if r["expect"] == "forgery"]
    genuine = [r for r in rows if r["expect"] == "genuine"]
    others = [r for r in rows if r["expect"] in ("wrong_person", "wrong_type")]
    return {"forgeries_flagged": _share(forgeries), "forgeries_flagged_by_a_sign": _share(forgeries, "flagged_by_a_sign"),
            "right_sign_named": _share(forgeries, "sign_named"), "genuine_passed": _share(genuine),
            "wrong_person_or_type_caught": _share(others)}


# ---- (b) The family's questions --------------------------------------------------------------

NEVER = re.compile(r"never recorded|didn't record|did not record|never mentioned", re.I)  # the "never" questions are in English


def score_question(case: dict, answer: str, cited_keys: list[str], unknown_citations: int) -> dict:
    """cited_keys: the kept items the answer's sources point to (by case key). unknown_citations: ids
    the answer cited that aren't kept items ("(source not found)")."""
    if case["expect"] == "never":
        passed = not cited_keys and bool(NEVER.search(answer))
    else:
        passed = bool(set(cited_keys) & set(case["expect"]))
    return {"q": case["q"], "expect": case["expect"], "cited": cited_keys, "made_up_citations": unknown_citations,
            "passed": passed}


def question_totals(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["expect"] != "never"]
    never = [r for r in rows if r["expect"] == "never"]
    return {"right_source_cited": _share(answerable), "says_never_recorded": _share(never),
            "made_up_citations": sum(r["made_up_citations"] for r in rows)}


# ---- (c) Cards say only what was said --------------------------------------------------------


def cards_text(result) -> str:
    """What the cards of one extraction say: their values only (never the field names), lower-cased."""
    def values(v):
        if isinstance(v, dict):
            return [x for item in v.values() for x in values(item)]
        if isinstance(v, list):
            return [x for item in v for x in values(item)]
        return [str(v)] if isinstance(v, str) else []

    cards = [*getattr(result, "moments", []), *getattr(result, "inventory", []), *getattr(result, "people", [])]
    return " | ".join(x for card in cards for x in values(card.model_dump())).lower()


def says(text: str, word: str) -> bool:
    """Whether the text has this word or phrase on its own, not inside another word ("son" isn't in "reason")."""
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text) is not None


def score_cards(case: dict, raw_text: str, grounded_text: str) -> dict:
    def unsaid(text):
        return [w for w in case["forbidden"] if says(text, w)]

    kept = [any(says(grounded_text, w) for w in group) for group in case["expected"]]
    return {"id": case["id"], "unsaid_before_check": unsaid(raw_text), "unsaid_after_check": unsaid(grounded_text),
            "said_and_kept": f"{sum(kept)}/{len(kept)}"}


def card_totals(rows: list[dict]) -> dict:
    expected = sum(int(r["said_and_kept"].split("/")[1]) for r in rows)
    kept = sum(int(r["said_and_kept"].split("/")[0]) for r in rows)
    return {"cases_with_unsaid_detail_before_check": _share_of(rows, lambda r: bool(r["unsaid_before_check"])),
            "cases_with_unsaid_detail_after_check": _share_of(rows, lambda r: bool(r["unsaid_after_check"])),
            "said_details_kept": {"count": kept, "of": expected, "share": round(kept / expected, 3) if expected else None}}


# ---- Shared --------------------------------------------------------------------------------


def _share(rows: list[dict], key: str = "passed") -> dict:
    return _share_of(rows, lambda r: bool(r[key]))


def _share_of(rows: list[dict], test) -> dict:
    count = sum(1 for r in rows if test(r))
    return {"count": count, "of": len(rows), "share": round(count / len(rows), 3) if rows else None}
