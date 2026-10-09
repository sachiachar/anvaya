"""The family's conversation survives restarts (PR-15.8): it's kept in the store, not in one
server's memory. Two questions on two app instances both work; each question sees the last turns;
only the same person in the same copy can continue; a reset deletes it; it expires after 30 days."""

import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from app import executor_agent, main
from app.store import store

OWNER = {"id": "owner_sessions_test", "name": "Vikram Rao"}
PRIYA = "family:u_copyaaaa01"
SOMEONE_ELSE = "family:u_copybbbb02"


class FakeRunner:
    """Answers without Gemini, and notes what history each question saw."""

    def __init__(self):
        self.seen: list[list[str]] = []

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        session = await executor_agent._session_service.get_session(
            app_name=executor_agent.APP_NAME, user_id=user_id, session_id=session_id)
        self.seen.append([e.content.parts[0].text for e in session.events])
        question = new_message.parts[0].text
        content = SimpleNamespace(parts=[SimpleNamespace(text=f"Answer to: {question}")])
        yield SimpleNamespace(content=content, get_function_calls=lambda: [], is_final_response=lambda: True)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for doc in store.list("handover_turns"):
        store.delete("handover_turns", doc["id"])
    runner = FakeRunner()
    monkeypatch.setattr(executor_agent, "_runner", runner)
    return runner


def new_instance(monkeypatch, runner):
    """A restart, or a second server: nothing from the first one's memory."""
    service = InMemorySessionService()
    monkeypatch.setattr(executor_agent, "_session_service", service)
    return runner


def ask(question, session_id=None, user=PRIYA, owner=OWNER):
    return asyncio.run(executor_agent.ask(owner, user, question, session_id))


def test_two_questions_on_two_app_instances_both_work(monkeypatch, fresh):
    first = ask("Where are Papa's property papers?")
    new_instance(monkeypatch, fresh)  # the server restarts, or another instance answers
    second = ask("Who else knows about them?", first["session_id"])
    assert second["session_id"] == first["session_id"]
    assert fresh.seen[1] == ["Where are Papa's property papers?", "Answer to: Where are Papa's property papers?"]


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
    assert history[0] == "Question 2" and history[-1] == "Answer to: Question 7"


def test_only_the_same_person_in_the_same_copy_can_continue(fresh):
    session_id = ask("Where are Papa's property papers?")["session_id"]
    with pytest.raises(executor_agent.UnknownSession):
        ask("And the gold?", session_id, user=SOMEONE_ELSE)
    with pytest.raises(executor_agent.UnknownSession):
        ask("And the gold?", session_id, owner={"id": "owner_other", "name": "Someone"})
    with pytest.raises(executor_agent.UnknownSession):
        ask("And the gold?", "hs_doesnotexist")


def test_a_turn_holds_the_answer_as_seen_and_expires_after_30_days(fresh):
    ask("Where are Papa's property papers?")
    [turn] = store.list("handover_turns")
    assert set(turn) == {"id", "session_id", "owner_id", "asker", "at", "question", "answer", "sources", "expire_at"}
    assert (turn["owner_id"], turn["asker"]) == (OWNER["id"], PRIYA)
    assert 29.9 * 86400 < turn["expire_at"].timestamp() - turn["at"] < 30.1 * 86400


def test_a_reset_deletes_the_conversation():
    client = TestClient(main.app)
    owner_id = client.get("/api/me").json()["owner_id"]
    asyncio.run(executor_agent.ask({"id": owner_id, "name": "Vikram Rao"}, PRIYA, "Where are the papers?"))
    store.put("handover_turns", {"id": "ht_other", "owner_id": "owner_other", "session_id": "hs_x"})
    client.post("/api/demo/seed")
    assert [t["id"] for t in store.list("handover_turns")] == ["ht_other"]  # another owner's stays


def test_the_release_expires_the_conversation():
    repo = pathlib.Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "deploy.yml").read_text())
    run = next(s["run"] for s in workflow["jobs"]["api"]["steps"] if "ttls update" in s.get("run", ""))
    assert "handover_turns" in re.search(r"for group in ([\w ]+); do", run).group(1).split()
