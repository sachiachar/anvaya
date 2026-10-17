"""Another voice heard (PR-2.13.2): the extractor says whether more than one person spoke in the
owner's side of an exchange, never who. Another voice is a quiet look for the team, a warm "who's
with you" step in the plan (once a conversation) and a mark on that exchange's cards. No audio or
voiceprint is stored. All data here is synthetic."""

import asyncio
import sys
import time

from app import audit_chain, live, loops, main, planner
from app.schemas import Extraction, Moment
from app.store import store
from tests.test_grounding import OWNER, client  # noqa: F401  (the app with a seeded demo)

SAID = "My nephew should get the Jayanagar flat."
WISH = Moment(kind="wish", title="Jayanagar flat to my nephew", summary=SAID, quote=SAID)


class WS:
    async def send_json(self, data):
        pass


class Session:
    def __init__(self):
        self.notes = []

    async def send_realtime_input(self, **kw):
        self.notes.append(kw.get("text"))


def exchange(monkeypatch, voices: str, audio: bytes | None = b"\x00\x01" * 800, *, state: dict | None = None,
             heard_live: bool = False, session: Session | None = None) -> dict:
    seen = {}

    async def extract(owner, heard, said, known, audio=None):
        seen["audio"] = audio
        return Extraction(moments=[WISH], voices=voices)

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    for collection in ("conversation_plans", "moments", "guardian_actions"):
        for doc in store.list(collection):
            if collection != "moments" or doc.get("quote") == SAID:  # a card from an earlier test would merge
                store.delete(collection, doc["id"])
    asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, SAID, "I see, Vikram ji.",
                                    main._expand, state or {"step": None, "correct": None}, audio,
                                    heard_live=heard_live, session=session))
    return seen


def fresh_plan():
    for doc in store.list("conversation_plans"):
        store.delete("conversation_plans", doc["id"])


def owner_turns() -> list[dict]:
    return sorted((t for t in store.list("turns", owner_id=OWNER) if t["role"] == "owner"), key=lambda t: t["at"])


def voice_actions() -> list[dict]:
    return [a for a in store.list("guardian_actions") if (a.get("refs") or {}).get("voice") == "another"]


def test_the_extractor_hears_the_owners_side_as_a_wav_and_it_is_never_stored(client, monkeypatch):  # noqa: F811
    seen = exchange(monkeypatch, "one")
    assert seen["audio"][:4] == b"RIFF" and seen["audio"][8:12] == b"WAVE"
    stored = str([store.list(c) for c in ("turns", "moments", "guardian_actions", "plan_versions")])
    assert "RIFF" not in stored and "audio" not in str(owner_turns()[-1])


def test_another_voice_marks_the_cards_records_a_look_and_adds_a_warm_step(client, monkeypatch):  # noqa: F811
    exchange(monkeypatch, "more")
    [card] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    assert card["another_voice"] is True
    [action] = voice_actions()
    assert action["response"] == "add_check" and action["refs"]["turn"] == owner_turns()[-1]["id"]
    event = next(e for e in store.list("guardian_events") if e["id"] == action["event_id"])
    assert event["reason"] == "Another voice was heard in the owner's conversation"
    plan = store.get("conversation_plans", OWNER)
    assert plan["steps"][0]["source"] == "voice" and plan["steps"][0]["tone"] == "warm"
    assert plan["steps"][0]["opens"] == "saathi" and plan["cause"]["voice"] == "another"


def test_one_voice_changes_nothing(client, monkeypatch):  # noqa: F811
    exchange(monkeypatch, "one")
    [card] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    assert not card.get("another_voice") and voice_actions() == []
    assert not [s for s in planner.current(OWNER)["steps"] if s["source"] == "voice"]


def test_saathi_asks_who_is_there_only_once_a_conversation():
    owner = "owner_test_voices"
    store.put("owners", {"id": owner, "name": "Test Owner"})
    planner.current(owner)
    first = planner.after_turn(owner, "t1", [], another_voice=True)
    again = planner.after_turn(owner, "t2", [], another_voice=True)
    assert [s["id"] for s in first["steps"] if s["source"] == "voice"] == ["voice:t1"]
    assert [s["id"] for s in again["steps"] if s["source"] == "voice"] == ["voice:t1"]


def test_a_call_without_audio_still_works(client, monkeypatch):  # noqa: F811
    assert exchange(monkeypatch, "one", audio=None)["audio"] is None


def test_the_audio_kept_for_an_exchange_is_capped():
    class Session:
        async def send_realtime_input(self, **kw):
            pass

    class Browser:
        def __init__(self):
            self.messages = [{"type": "websocket.receive", "bytes": b"\x00" * (live.MAX_EXCHANGE_AUDIO // 2)}] * 3 + \
                            [{"type": "websocket.disconnect"}]

        async def receive(self):
            return self.messages.pop(0)

    buffer = bytearray()
    asyncio.run(live._from_browser(Browser(), Session(), buffer))
    assert len(buffer) == live.MAX_EXCHANGE_AUDIO


def test_the_audit_chain_shows_a_voice_step_by_its_kind():
    assert audit_chain.safe_step("voice:turn_1") == "voice"


def test_the_extractor_is_told_to_count_voices_never_name_them(monkeypatch):
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return Extraction()

    monkeypatch.setattr(loops, "generate_json", fake)
    asyncio.run(loops.extract_from_exchange({"name": "Vikram Rao"}, SAID, "I see.", {}, audio=b"RIFFxxxxWAVE"))
    assert seen["media"] == [(b"RIFFxxxxWAVE", "audio/wav")] and "never who" in seen["system"]


def test_a_voice_note_with_another_voice_is_marked_too(client, monkeypatch):  # noqa: F811
    from app.schemas import ConversationTurn, PlanReply

    async def understand(*args):
        return ConversationTurn(heard=SAID, owner_language="English", moments=[WISH], voices="more")

    async def respond(*args):
        return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    for doc in [d for d in store.list("moments") if d.get("quote") == SAID]:
        store.delete("moments", doc["id"])
    r = client.post(f"/api/owners/{OWNER}/conversation", files={"file": ("note.webm", b"\x1aE\xdf\xa3", "audio/webm")})
    assert r.status_code == 200
    [card] = [c for c in r.json()["assistant_turn"]["proposals"] if c["collection"] == "moments"]
    assert card["another_voice"] is True and voice_actions()


# ---- Faster: Saathi says at once, and the extractor's finding reaches Saathi at once --------------


def test_saathi_reports_another_voice_and_gets_the_warm_step_at_once(client):  # noqa: F811
    fresh_plan()
    state = {"step": None, "correct": None, "voice_heard": False, "voice_told": False}
    out = live._plan_tool(OWNER, "voice_joined", {}, state)
    assert out["step"]["source"] == "voice" and state["step"] == out["step"]["id"]
    assert state["voice_heard"] and state["voice_told"]
    again = live._plan_tool(OWNER, "voice_joined", {}, state)  # a second report adds no second step
    assert [s["id"] for s in planner.current(OWNER)["steps"] if s["source"] == "voice"] == [again["step"]["id"]]


def test_reporting_another_voice_keeps_the_owners_mood(client):  # noqa: F811
    fresh_plan()
    planner.after_turn(OWNER, "t_sad", [], mood="sad")
    planner.voice_heard(OWNER, "live_1")
    assert planner.mood_now(planner.current(OWNER), time.time()) == "sad"


def test_what_saathi_heard_marks_the_exchange_even_when_the_extractor_hears_one_voice(client, monkeypatch):  # noqa: F811
    exchange(monkeypatch, "one", heard_live=True)
    [card] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    assert card["another_voice"] is True
    [action] = voice_actions()
    assert action["agent"] == "saathi.voice" and action["refs"]["turn"] == owner_turns()[-1]["id"]


def test_the_extractor_finding_tells_saathi_to_read_the_plan_now_once_a_call(client, monkeypatch):  # noqa: F811
    session, state = Session(), {"step": None, "correct": None, "voice_heard": False, "voice_told": False}
    exchange(monkeypatch, "more", state=state, session=session)
    exchange(monkeypatch, "more", state=state, session=session)
    assert session.notes == [live.VOICE_NOTE] and "current_step" in live.VOICE_NOTE


def test_no_note_when_saathi_already_reported_the_voice_or_heard_one_voice(client, monkeypatch):  # noqa: F811
    session = Session()
    exchange(monkeypatch, "more", session=session, state={"step": None, "correct": None, "voice_told": True})
    exchange(monkeypatch, "one", session=session, state={"step": None, "correct": None, "voice_told": False})
    assert session.notes == []


def test_saathi_is_told_to_report_another_voice_never_who():
    text = loops.live_instructions({"name": "Vikram Rao"}, {})
    assert "voice_joined" in text and "Never guess or say who" in text


def test_the_warm_step_comes_once_a_conversation_even_after_it_is_answered():
    owner = "owner_test_voice_answered"
    store.put("owners", {"id": owner, "name": "Test Owner"})
    planner.current(owner)
    first = planner.voice_heard(owner, "live_1")
    step = next(s for s in first["steps"] if s["source"] == "voice")
    planner.after_turn(owner, "t1", [], answered_step=step["id"], outcome="answered")
    assert not [s for s in planner.current(owner)["steps"] if s["source"] == "voice"]
    planner.voice_heard(owner, "live_2")  # a second call in the same conversation
    planner.after_turn(owner, "t2", [], another_voice=True)
    assert not [s for s in planner.current(owner)["steps"] if s["source"] == "voice"]


def test_the_audit_chain_links_the_turn_when_saathi_added_the_step_live(client, monkeypatch):  # noqa: F811
    fresh_plan()
    planner.current(OWNER)
    planner.voice_heard(OWNER, "live_1")
    exchange(monkeypatch, "one", heard_live=True)
    turn = owner_turns()[-1]["id"]
    linked = [v for v in store.list("plan_versions", owner_id=OWNER) if v["cause"].get("turn") == turn]
    assert linked and linked[-1]["cause"]["voice"] == "another"
    assert any(v["cause"].get("voice") == "another" for v in audit_chain.for_turn(turn)["plans"])


def test_voice_joined_also_gives_a_waiting_correction(client):  # noqa: F811
    fresh_plan()
    state = {"step": None, "correct": "Don't say 'noted'.", "voice_heard": False, "voice_told": False}
    out = live._plan_tool(OWNER, "voice_joined", {}, state)
    assert out["correct"] == "Don't say 'noted'." and state["correct"] is None


def test_the_record_says_who_heard_the_voice(client, monkeypatch):  # noqa: F811
    exchange(monkeypatch, "more", heard_live=True)
    [action] = voice_actions()
    assert action["agent"] == "saathi.voice" and action["refs"]["heard_by"] == "saathi.voice,capture.extractor"


def test_the_note_waits_while_someone_is_speaking_and_goes_when_the_turn_ends(client, monkeypatch):  # noqa: F811
    session = Session()
    state = {"step": None, "correct": None, "voice_told": False, "talking": True}
    exchange(monkeypatch, "more", state=state, session=session)
    assert session.notes == [] and state["note_waiting"]

    class Message:
        usage_metadata = tool_call = None

        class server_content:  # noqa: N801  (the Live API's name)
            input_transcription = output_transcription = model_turn = interrupted = None
            turn_complete = True

    class Live(Session):
        def __init__(self):
            super().__init__()
            self.turns = [[Message()], []]

        async def receive(self):
            for message in self.turns.pop(0):
                yield message

    live_session = Live()
    asyncio.run(live._to_browser(WS(), live_session, {"id": OWNER, "name": "Vikram Rao"}, main._expand, {}, None, state))
    assert live_session.notes == [live.VOICE_NOTE] and not state["note_waiting"]


def test_no_note_when_the_warm_step_was_not_put_first(client, monkeypatch):  # noqa: F811
    session = Session()
    state = {"step": None, "correct": None, "voice_told": False}
    monkeypatch.setattr(live.planner, "after_turn", lambda *a, **kw: {})  # e.g. already asked this conversation
    exchange(monkeypatch, "more", state=state, session=session)
    assert session.notes == []


def _keep(card: dict, **body):
    from fastapi.testclient import TestClient
    return TestClient(main.app).post(f"/api/proposals/{card['id']}/decision",
                                     json={"collection": "moments", "decision": "approve", **body})


def test_a_wish_said_with_someone_else_there_waits_for_the_owner(client, monkeypatch):  # noqa: F811
    exchange(monkeypatch, "more")
    [card] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    assert card["another_voice_hold"] is True
    refused = _keep(card)
    assert refused.status_code == 409 and "Confirm it's what you want" in refused.json()["detail"]
    assert _keep(card, edits={"kind": "story", "another_voice_hold": False}).status_code == 409  # edits can't free it
    refusal = max((a for a in store.list("guardian_actions") if a["action"] == "keep_card"), key=lambda a: a["at"])
    assert refusal["response"] == "deflect" and refusal["detectors"] == ["voice_rule"]
    kept = _keep(card, voice_confirmed=True)
    assert kept.status_code == 200 and "another_voice_hold" not in kept.json()
    allowed = max((a for a in store.list("guardian_actions") if a["action"] == "keep_card"), key=lambda a: a["at"])
    assert allowed["response"] == "continue" and allowed["refs"]["voice"] == "confirmed_by_owner"


def test_a_story_said_with_someone_else_there_is_only_marked(client, monkeypatch):  # noqa: F811
    story = Moment(kind="story", title="First salary", summary=SAID, quote=SAID)
    monkeypatch.setattr(sys.modules[__name__], "WISH", story)
    exchange(monkeypatch, "more")
    [card] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    assert card["another_voice"] is True and not card.get("another_voice_hold")
    assert _keep(card).status_code == 200


def test_which_cards_wait_when_someone_else_is_there():
    from app.guardian_inputs import needs_owner_confirmation as needs
    assert needs("moments", {"kind": "wish"}) and needs("moments", {"kind": "practical"})
    assert needs("inventory", {}) and needs("people", {})
    assert not needs("moments", {"kind": "story"}) and not needs("moments", {"kind": "advice"})
