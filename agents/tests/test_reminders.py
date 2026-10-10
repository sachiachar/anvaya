"""Saathi remembers only what's kept, and reminds instead of repeating.

No earlier conversation reaches Gemini: only what the owner kept and Saathi's own last message
while it's the same conversation. Gaps feed only the conversation plan (PR-2.12.2): a gap counts as
asked when it is the plan step Saathi carried out (PR-2.12.3), and the reader of the next turn says
how it was answered, so a gap comes back only as a reminder, after a wait set by its harm."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gaps, live, loops, main, planner, proposals
from app.schemas import ConversationTurn, Extraction, PlanReply
from app.store import new_id, store
from tests.test_knowledge import OWNER, POLICY, _keep

DAY = gaps.DAY
SBI = {**POLICY, "category": "bank_account", "institution": "SBI", "product": "Savings account",
       "account_hint": "••4808", "nominee_or_beneficiary": None}
LIC = {**POLICY, "nominee_or_beneficiary": None}  # insurance: harm 3
URL = f"/api/owners/{OWNER}/conversation"


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


@pytest.fixture()
def chat(monkeypatch):
    """Stub Gemini for typed chat (reading the turn, then Saathi's reply): records each prompt, and
    reads the owner's answer to the last step as the test sets."""
    seen: list[str] = []
    reading = {"step_outcome": "none"}

    async def fake(**kw):
        seen.append(kw["prompt"])
        if kw["schema"] is PlanReply:
            return PlanReply(reply="Which bank is it, Vikram ji?", reply_language="en-IN")
        return ConversationTurn(heard="", owner_language="English", **reading)

    monkeypatch.setattr(loops, "generate_json", fake)
    for doc in store.list("conversation_plans"):
        store.delete("conversation_plans", doc["id"])
    return seen, reading


def gap_first(key: str) -> None:
    """Make this gap the plan's current step: the first-plan steps are taken as done."""
    plan = planner.current(OWNER)
    store.update("conversation_plans", OWNER, {"finished": [f"initial:{s.id}" for s in planner.initial_plan().steps]})
    plan = planner.refresh(OWNER, {"kind": "test"})
    assert plan["current"] == f"gap:{key}", plan["current"]


def key_of(institution: str) -> str:
    return next(g["key"] for g in gaps.find(OWNER) if institution.lower() in g["key"])


def ask(key: str, at: float, outcome: str | None = None) -> None:
    doc = store.get("gap_asks", f"{OWNER}:{key}") or {"id": f"{OWNER}:{key}", "owner_id": OWNER, "key": key, "events": []}
    doc["events"].append({"turn_id": new_id("turn"), "at": at, "outcome": outcome})
    store.put("gap_asks", doc)


def due_keys(now: float) -> list[str]:
    return [g["key"] for g in gaps.due(OWNER, n=20, now=now)]


# ---- No earlier conversation ---------------------------------------------------


def test_typed_chat_sends_no_earlier_conversation(client, chat):
    seen, _ = chat
    store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": 1.0, "role": "owner",
                        "text": "Something I discarded from my mind", "attachment": None})
    assert client.post(URL, data={"text": "Hello"}).status_code == 200
    assert "Something I discarded" not in seen[-1]
    assert "(none: a new conversation)" in seen[-1]


def test_saathi_carries_on_from_its_own_last_message_only_within_the_conversation(client, chat):
    seen, _ = chat
    client.post(URL, data={"text": "I have a savings account."})
    client.post(URL, data={"text": "SBI"})
    assert "Your last message in this conversation: Which bank is it, Vikram ji?" in seen[-1]
    assert "I have a savings account." not in seen[-1]  # never the owner's earlier words

    last = store.get("owners", OWNER)["last_said"]
    store.update("owners", OWNER, {"last_said": {**last, "at": last["at"] - gaps.CONVERSATION_GAP - 1}})
    client.post(URL, data={"text": "Hello again"})
    assert "(none: a new conversation)" in seen[-1]


def test_talk_about_this_still_opens_the_call_with_its_question(client):
    store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": 1.0, "role": "owner",
                        "text": "An old story", "attachment": None})
    client.post(f"{URL}/prompt", json={"text": "Who should receive the flat in Pune?"})
    instructions = live._instructions(store.get("owners", OWNER))
    assert "Who should receive the flat in Pune?" in instructions and "An old story" not in instructions


# ---- Reminders -----------------------------------------------------------------


def test_an_asked_gap_waits_then_comes_back_as_a_reminder(client):
    _keep("inventory", LIC)
    key = key_of("lic")
    ask(key, at=0)
    assert key not in due_keys(now=60)  # never twice in one conversation
    assert key not in due_keys(now=2 * DAY)
    reminder = gaps.due(OWNER, now=3 * DAY + 1)[0]
    assert reminder["key"] == key and reminder["reminder"] == 1
    assert reminder["last_asked"] == "3 days ago" and reminder["last_answer"] == "no answer"


def test_a_high_harm_gap_comes_back_sooner_than_a_low_one(client):
    _keep("inventory", LIC)
    _keep("inventory", SBI)
    lic, sbi = key_of("lic"), key_of("sbi")
    ask(lic, at=0)
    ask(sbi, at=0)
    assert lic in due_keys(now=3 * DAY + 1) and sbi not in due_keys(now=3 * DAY + 1)
    store.delete("gap_asks", f"{OWNER}:{lic}")
    assert sbi not in due_keys(now=3 * DAY + 1) and sbi in due_keys(now=7 * DAY + 1)


def test_not_now_doubles_the_wait(client):
    _keep("inventory", LIC)
    key = key_of("lic")
    ask(key, at=0, outcome="not_now")
    assert key not in due_keys(now=5 * DAY)
    assert gaps.due(OWNER, now=6 * DAY + 1)[0]["last_answer"] == "not now"


def test_reminders_stop_after_the_limit_but_the_gap_stays_on_the_list(client):
    _keep("inventory", LIC)
    key = key_of("lic")
    for i in range(len(gaps.REMINDERS[3]) + 1):  # the first question and every reminder
        ask(key, at=i)
    assert key not in due_keys(now=365 * DAY)
    assert key in [g["key"] for g in client.get(f"/api/owners/{OWNER}/gaps").json()["gaps"]]


def test_at_most_one_reminder_at_a_time(client):
    _keep("inventory", LIC)
    _keep("inventory", {**LIC, "institution": "HDFC Life", "account_hint": "••7777"})
    for g in gaps.find(OWNER):
        ask(g["key"], at=0)
    due = gaps.due(OWNER, now=30 * DAY)
    assert len(due) == 1 and due[0]["reminder"] == 1


def test_a_waiting_card_pauses_its_gap_and_keeping_it_closes_the_gap(client):
    _keep("inventory", LIC)
    key = key_of("lic")
    card = proposals.save_inventory(OWNER, {**LIC, "nominee_or_beneficiary": "Meena"}, {})
    assert key not in due_keys(now=0)
    proposals.approve("inventory", card)
    assert key not in [g["key"] for g in gaps.find(OWNER)]


# ---- What Saathi reports -------------------------------------------------------


def test_the_chat_records_the_gap_step_asked_and_how_it_was_answered(client, chat):
    _, reading = chat
    _keep("inventory", LIC)
    key = key_of("lic")
    gap_first(key)
    client.post(URL, data={"text": "Hello"})
    last = store.get("owners", OWNER)["last_said"]
    assert last["gap"] == key and last["step"] == f"gap:{key}"
    reading["step_outcome"] = "not_now"
    client.post(URL, data={"text": "Not now, later"})
    events = store.get("gap_asks", f"{OWNER}:{key}")["events"]
    assert events[0]["outcome"] == "not_now"


def test_a_step_that_waits_for_the_owner_is_never_counted_as_asked(client, chat):
    _keep("inventory", SBI)  # a medium-harm nominee gap; the common gaps come first
    plan = planner.current(OWNER)
    owner_steps = [s for s in plan["steps"] if s["opens"] == "owner"]
    assert owner_steps and all(s["id"] != plan["current"] for s in owner_steps)
    client.post(URL, data={"text": "Hello"})
    asked = {a["key"] for a in store.list("gap_asks", owner_id=OWNER)}
    assert not asked & {s["id"].removeprefix("gap:") for s in owner_steps}


def test_a_call_records_the_gap_step_it_asked_and_the_answer(client, monkeypatch):
    _keep("inventory", LIC)
    key = key_of("lic")
    found = iter([Extraction(), Extraction(step_outcome="unsure")])

    async def extract(owner, heard, said, known):
        return next(found)

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    owner = {"id": OWNER, "name": "Vikram Rao"}
    state = {"step": f"gap:{key}", "correct": None}  # what current_step gave Saathi
    asyncio.run(live._save_exchange(WS(), owner, "I have an LIC policy.", "Who is its nominee?", main._expand, state))
    assert store.get("owners", OWNER)["last_said"]["step"] == f"gap:{key}"
    state["step"] = None
    asyncio.run(live._save_exchange(WS(), owner, "I think Meena.", "Thank you.", main._expand, state))
    assert store.get("gap_asks", f"{OWNER}:{key}")["events"][0]["outcome"] == "unsure"


def test_discard_forgets_the_questions_asked_and_the_last_message(client, chat):
    _keep("inventory", LIC)
    gap_first(key_of("lic"))
    turns = client.post(URL, data={"text": "Hello"}).json()
    ids = [turns["owner_turn"]["id"], turns["assistant_turn"]["id"]]
    assert client.post(f"{URL}/forget", json={"turn_ids": ids}).status_code == 200
    assert store.list("gap_asks", owner_id=OWNER) == []
    assert store.get("owners", OWNER)["last_said"] is None


def test_discarding_the_answer_takes_it_back_even_if_the_question_is_kept(client, chat):
    _, reading = chat
    _keep("inventory", LIC)
    key = key_of("lic")
    gap_first(key)
    client.post(URL, data={"text": "Hello"})  # asked here; this conversation is kept
    reading["step_outcome"] = "declined"
    answer = client.post(URL, data={"text": "I'd rather not say"}).json()
    client.post(f"{URL}/forget", json={"turn_ids": [answer["owner_turn"]["id"], answer["assistant_turn"]["id"]]})
    events = store.get("gap_asks", f"{OWNER}:{key}")["events"]
    assert len(events) == 1 and events[0]["outcome"] is None  # still asked, but "declined" is gone


def test_a_call_judges_each_answer_against_the_question_it_saw(client, monkeypatch):
    """Exchanges of a call save at the same time and can finish out of order: an older exchange
    never replaces a newer last message, and each records the step Saathi was carrying out then."""
    _keep("inventory", LIC)
    key = key_of("lic")
    first_done = asyncio.Event()

    async def extract(owner, heard, said, known):
        if heard == "First.":
            await first_done.wait()  # the first exchange finishes last
        return Extraction()

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    owner = {"id": OWNER, "name": "Vikram Rao"}

    async def run() -> None:
        first = asyncio.create_task(live._save_exchange(WS(), owner, "First.", "Older reply.", main._expand,
                                                        {"step": None, "correct": None}))
        await asyncio.sleep(0.05)
        await live._save_exchange(WS(), owner, "Second.", "Who is the nominee?", main._expand,
                                  {"step": f"gap:{key}", "correct": None})
        first_done.set()
        await first

    asyncio.run(run())
    last = store.get("owners", OWNER)["last_said"]
    assert last["text"] == "Who is the nominee?" and last["gap"] == key

