"""Measure the quality of three Gemini steps on a small synthetic test set (PR-15.5).

    cd agents && uv run python -m evals.run            # all three
    cd agents && uv run python -m evals.run cards      # or: certificates, questions, cards

It calls real Gemini with the key in GOOGLE_API_KEY (never printed), through the same code and the
same governance gateway as the app, so the calls count against the daily ceiling. A person runs it;
it isn't part of the unit tests. It writes evals/results/<date>-<time>.json and a short .md summary.
Everything is synthetic, kept in memory and a temporary folder, and thrown away when it ends: the
cloud store, the media bucket and Vertex AI are switched off before the app loads.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import sys
import tempfile

from . import score

RESULTS = pathlib.Path(__file__).resolve().parent / "results"
AGENTS = {"certificates": "activation.checker", "questions": "handover.assistant", "cards": "saathi.chat"}
# Before the app loads (its settings are read once): memory only, no bucket, the Gemini API key path.
LOCAL_ONLY = {"STORE": "memory", "MEDIA_BUCKET": "", "GOOGLE_GENAI_USE_VERTEXAI": "false", "KMS_KEY_NAME": ""}


async def certificates() -> list[dict]:
    from app import fraud_guides, loops

    data = score.load("certificates")
    guide = fraud_guides.current(fraud_guides.key_for("death certificate", "India"))
    rows = []
    for case in data["cases"]:
        check = await loops.check_document(score.certificate(case["details"], data["base"]), "application/pdf",
                                           "death certificate", case["person"], guide)
        rows.append(score.score_certificate(case, loops.review_summary(check, guide)))
        print(f"  certificate {case['id']}: {'pass' if rows[-1]['passed'] else 'FAIL'}")
    return rows


async def questions() -> list[dict]:
    from app import executor_agent, knowledge
    from app.store import new_id, store

    data = score.load("questions")
    owner = {"id": "owner_evals_questions", "name": data["owner"]["name"]}
    keys = {}  # kept item id -> case key
    for collection, items in data["kept"].items():
        prefix = {"moments": "m", "inventory": "inv", "people": "p"}[collection]
        for key, item in items.items():
            keys[knowledge.commit(collection, {**item, "id": new_id(prefix)}, owner["id"])["id"]] = key
    rows = []
    for case in data["questions"]:  # each question on its own, as a new conversation
        r = await executor_agent.ask(owner, "family:evals", case["q"])
        cited = [keys[s["id"]] for s in r["sources"] if s["id"] in keys]
        # Made-up citations, counted in the model's own answer (the family's copy merges them).
        conversation = store.get("handover_conversations", r["session_id"]) or {"recent": [{"model_answer": ""}]}
        made_up = sum(1 for i in executor_agent.KEPT_ID.findall(conversation["recent"][-1]["model_answer"]) if i not in keys)
        rows.append(score.score_question(case, r["answer"], cited, made_up))
        print(f"  question {case['q'][:40]!r}: {'pass' if rows[-1]['passed'] else 'FAIL'}")
    return rows


async def cards() -> list[dict]:
    from app import grounding, loops, proposals

    data = score.load("cards")
    owner = {"id": "owner_evals_cards", "name": data["owner"]["name"], "country": "India", "city": "Bengaluru",
             "family": data["owner"]["family"]}
    known = {**proposals.known(owner["id"]), "family": data["owner"]["family"], "last_gap_asked": None}
    rows = []
    for case in data["cases"]:
        last = {"text": case["saathi"]} if case["saathi"] else None
        result = await loops.converse(owner, last, case["owner"], None, known)
        grounded = grounding.ground(result, owner_words=case["owner"], saathi_words=case["saathi"] or "", known=known)
        rows.append(score.score_cards(case, score.cards_text(result), score.cards_text(grounded)))
        print(f"  cards {case['id']}: unsaid before {rows[-1]['unsaid_before_check']}, after {rows[-1]['unsaid_after_check']}")
    return rows


TOTALS = {"certificates": score.certificate_totals, "questions": score.question_totals, "cards": score.card_totals}
STEPS = {"certificates": certificates, "questions": questions, "cards": cards}


def summary(results: dict) -> str:
    """A short Markdown summary of the numbers, for the README."""
    out = [f"# Measured quality, {results['date']}", "",
           "A small synthetic test set run against real Gemini (`agents/evals`). Not a full benchmark.", ""]
    for step, part in results["steps"].items():
        out.append(f"## {step} ({part['agent']}, primary model {part['model']}, {len(part['cases'])} cases)")
        for name, value in part["totals"].items():
            shown = (f"{value['count']} of {value['of']} ({value['share']:.0%})" if isinstance(value, dict)
                     and value.get("share") is not None else value)
            out.append(f"- {name.replace('_', ' ')}: {shown}")
        out.append("")
    return "\n".join(out)


async def main(only: list[str]) -> int:
    if not os.environ.get("GOOGLE_API_KEY"):
        print("Set GOOGLE_API_KEY (a Gemini API key) in the environment first. It is never printed.")
        return 2
    from app.governance import core

    core.set_current_user("u_evals")  # one made-up person, so the gateway's limits apply as for anyone
    now = datetime.datetime.now()
    results = {"date": now.date().isoformat(), "at": now.isoformat(timespec="minutes"), "steps": {}}
    for step in only:
        print(f"{step}:")
        rows = await STEPS[step]()
        results["steps"][step] = {"agent": AGENTS[step], "model": core.primary_model(AGENTS[step]),
                                  "totals": TOTALS[step](rows), "cases": rows}
    RESULTS.mkdir(exist_ok=True)
    name = f"{now:%Y-%m-%d-%H%M}"  # never overwrites an earlier run, even on the same day
    (RESULTS / f"{name}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    (RESULTS / f"{name}.md").write_text(summary(results) + "\n")
    print(summary(results))
    print(f"Written to {RESULTS / name}.json and .md")
    return 0


if __name__ == "__main__":
    os.environ.update(LOCAL_ONLY)
    os.environ["LOCAL_MEDIA_DIR"] = tempfile.mkdtemp(prefix="anvaya-evals-")
    chosen = sys.argv[1:] or list(STEPS)
    if unknown := [s for s in chosen if s not in STEPS]:
        sys.exit(f"Unknown step {unknown}; choose from {list(STEPS)}")
    sys.exit(asyncio.run(main(chosen)))
