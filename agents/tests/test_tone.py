"""The owner is alive: what Saathi says to them never talks about their death (rule 8: checked in
code, not only asked for in the prompt). Other people's deaths are still part of their stories."""

import asyncio

from app import loops, tasks
from app.loops import reply_problems
from app.schemas import Gap, Readiness
from app.store import new_id, store
from app.tone import about_their_death, step_problems

BAD = "Have you shared your preferences regarding your digital accounts or any specific final wishes with Sunita or Priya?"
GOOD = "Who in the family knows how you'd like your email and phone handled, Vikram ji?"


def test_death_framing_is_caught_in_english_hinglish_and_hindi():
    for text in (BAD, "What should happen after you're gone?", "When you pass away, who gets the flat?",
                 "If something happens to you, will Sunita know?", "Aapke jaane ke baad flat kiska hoga?",
                 "आपके जाने के बाद यह किसे मिलेगा?", "Do you have thoughts about your funeral?"):
        assert about_their_death(text), text
    for text in (GOOD, "Who is the nominee on your LIC policy?", "Where are the papers for the Mysore flat?"):
        assert not about_their_death(text), text


def test_saathi_may_still_talk_about_others_who_have_left_us():
    assert not reply_problems("My father died in 1998", "I'm sorry your father passed away. What was he like?")
    assert reply_problems("I have an LIC policy", "Who should get it after you're gone?")


def _gap(question, title="Tell the family about your online accounts", why="So Sunita can find them."):
    return Gap(title=title, why_it_matters=why, action="talk", question_for_owner=question, severity="high")


def test_the_planner_rewrites_a_step_about_death_once_then_leaves_it_out(monkeypatch):
    replies = [Readiness(score=50, gaps=[_gap(BAD)]), Readiness(score=50, gaps=[_gap(GOOD)])]
    prompts = []

    async def fake(**kw):
        prompts.append(kw["prompt"])
        return replies.pop(0)

    monkeypatch.setattr(loops, "generate_json", fake)
    owner = {"name": "Vikram Rao", "age": 62, "country": "India", "family": ["Sunita"]}
    result = asyncio.run(loops.assess_readiness(owner, [], [], [], [], [], {}))
    assert [g.question_for_owner for g in result.gaps] == [GOOD]
    assert "talked about death" in prompts[1]

    replies[:] = [Readiness(score=50, gaps=[_gap(BAD)]), Readiness(score=50, gaps=[_gap(GOOD, why="In case of your death.")])]
    assert asyncio.run(loops.assess_readiness(owner, [], [], [], [], [], {})).gaps == []  # still wrong: left out


def test_steps_saved_before_the_check_never_reach_the_owner():
    owner_id = f"owner_{new_id('t')}"
    for q in (BAD, GOOD):
        store.put("tasks", {"id": new_id("task"), "owner_id": owner_id, "status": "open", "created_at": 1,
                            **_gap(q).model_dump()})
    assert [t["question_for_owner"] for t in tasks.open_tasks(owner_id)] == [GOOD]
    assert step_problems(_gap(GOOD).model_dump()) == []
