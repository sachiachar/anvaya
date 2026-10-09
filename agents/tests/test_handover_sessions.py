"""The family's conversation survives restarts (PR-15.8): it's kept in the store, not in one
server's memory. Two questions on two app instances both work; each question sees the last turns,
with the model's own citations; only the same person in the same copy can continue; a conversation
that's gone (reset, expired) starts again instead of failing; each question is recorded for audit,
numbers masked, blocked ones too; a reset deletes it all; it expires after 30 days."""

import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from app import demo, executor_agent, main
from app.governance import core
from app.store import store

OWNER = {"id": "owner_sessions_test", "name": "Vikram Rao"}
PRIYA = "family:u_copyaaaa01"
SOMEONE_ELSE = "family:u_copybbbb02"
CITED = "m_0123456789ab"


class FakeRunner:
    """Answers without Gemini, citing a kept item, and notes what history each question saw."""

    def __init__(self):
        self.seen: list[list[str]] = []
        self.reply = None  # set to "" to answer nothing

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        session = await executor_agent._session_service.get_session(
            app_name=executor_agent.APP_NAME, user_id=user_id, session_id=session_id)
        self.seen.append([e.content.parts[0].text for e in session.events])
        text = self.reply if self.reply is not None else f"Answer to: {new_message.parts[0].text} [{CITED}]"
        content = SimpleNamespace(parts=[SimpleNamespace(text=text)])
        yield SimpleNamespace(content=content, get_function_calls=lambda: [], is_final_response=lambda: True)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for collection in ("handover_turns", "handover_conversations"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    runner = FakeRunner()
    monkeypatch.setattr(executor_agent, "_runner", runner)
    return runner


def ask(question, session_id=None, user=PRIYA, owner=OWNER):
    return asyncio.run(executor_agent.ask(owner, user, question, session_id))


def test_two_questions_on_two_app_instances_both_work(monkeypatch, fresh):
    first = ask("Where are Papa's property papers?")
    monkeypatch.setattr(executor_agent, "_session_service", InMemorySessionService())  # a restart, or another server
    second = ask("Who else knows about them?", first["session_id"])
    assert second["session_id"] == first["session_id"]
    assert fresh.seen[1][0] == "Where are Papa's property papers?"


def test_the_model_sees_its_own_earlier_answers_with_their_citations(fresh):
    first = ask("Where are Papa's property papers?")
    assert CITED not in first["answer"]  # the family never sees ids
    ask("Who else knows about them?", first["session_id"])
    assert fresh.seen[1][1] == f"Answer to: Where are Papa's property papers? [{CITED}]"  # so it keeps citing


def test_nothing_stays_in_server_memory(fresh):
    ask("Where are Papa's property papers?")
    assert asyncio.run(executor_agent._session_service.list_sessions(
        app_name=executor_agent.APP_NAME, user_id=PRIYA)).sessions == []


def test_each_question_sees_only_the_last_six_turns(fresh):
    session_id = ask("Question 0")["session_id"]
    for i in range(1, 9):
        ask(f"Question {i}", session_id)
    history = fresh.seen[-1]
    assert len(history) == 2 * executor_agent.HISTORY_TURNS
    assert history[0] == "Question 2" and history[-1].startswith("Answer to: Question 7")
    assert len(store.list("handover_turns")) == 9  # the audit keeps every question


def test_only_the_same_person_in_the_same_copy_can_continue(fresh):
    session_id = ask("Where are Papa's property papers?")["session_id"]
    with pytest.raises(executor_agent.UnknownSession):
        ask("And the gold?", session_id, user=SOMEONE_ELSE)
    with pytest.raises(executor_agent.UnknownSession):
        ask("And the gold?", session_id, owner={"id": "owner_other", "name": "Someone"})


def test_a_conversation_thats_gone_starts_again_instead_of_failing(fresh):
    """After a reset or the 30-day expiry, the app still holds the old id."""
    r = ask("Where are the papers?", "hs_resetorexpired")
    assert r["session_id"] != "hs_resetorexpired" and r["answer"]


def test_a_turn_is_recorded_with_numbers_masked_and_expires_after_30_days(fresh):
    ask("Is policy 123456789012 still active?")
    [turn] = store.list("handover_turns")
    assert set(turn) == {"id", "session_id", "owner_id", "asker", "at", "question", "answer", "sources",
                         "blocked", "expire_at"}
    assert "123456789012" not in turn["question"] and "9012" in turn["question"]
    assert turn["sources"] == [] and not turn["blocked"]  # the cited id isn't a kept item of this test owner
    assert 29.9 * 86400 < turn["expire_at"].timestamp() - turn["at"] < 30.1 * 86400
    [conversation] = store.list("handover_conversations")
    assert "123456789012" not in str(conversation)
    assert f"[{CITED}]" in conversation["recent"][0]["model_answer"]  # masking leaves citation ids whole


def test_an_empty_answer_isnt_part_of_the_conversation(fresh):
    fresh.reply = ""
    ask("Where are the papers?")
    assert store.list("handover_turns") == [] and store.list("handover_conversations") == []


def test_a_failed_save_still_gives_the_family_the_answer(fresh, monkeypatch):
    def broken(*_, **__):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(store, "put", broken)
    assert ask("Where are the papers?")["answer"].startswith("Answer to")


def test_a_blocked_question_is_recorded_too(fresh):
    session_id = ask("Where are the papers?")["session_id"]
    same = executor_agent.record_blocked(OWNER["id"], PRIYA, "What is Papa's ATM PIN?", "I can't help.", session_id)
    assert same == session_id
    blocked = [t for t in store.list("handover_turns") if t["blocked"]]
    assert [t["question"] for t in blocked] == ["What is Papa's ATM PIN?"]
    other = executor_agent.record_blocked(OWNER["id"], SOMEONE_ELSE, "PIN?", "I can't help.", session_id)
    assert other != session_id  # never added to someone else's conversation


def test_a_reset_deletes_the_conversation():
    client = TestClient(main.app)
    owner_id = demo.owner_id_for(core.user_key(demo.LOCAL))
    client.post("/api/demo/seed")
    asyncio.run(executor_agent.ask({"id": owner_id, "name": "Vikram Rao"}, PRIYA, "Where are the papers?"))
    store.put("handover_turns", {"id": "ht_other", "owner_id": "owner_other", "session_id": "hs_x"})
    client.post("/api/demo/seed")
    assert [t["id"] for t in store.list("handover_turns")] == ["ht_other"]  # another owner's stays
    assert store.list("handover_conversations", owner_id=owner_id) == []


def test_the_release_expires_both_records():
    repo = pathlib.Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "deploy.yml").read_text())
    run = next(s["run"] for s in workflow["jobs"]["api"]["steps"] if "ttls update" in s.get("run", ""))
    groups = re.search(r"for group in ([\w ]+); do", run).group(1).split()
    assert {"handover_turns", "handover_conversations"} <= set(groups)
