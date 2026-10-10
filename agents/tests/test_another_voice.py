"""Another voice heard (PR-2.13.2): the extractor says whether more than one person spoke in the
owner's side of an exchange, never who. Another voice is a quiet look for the team, a warm "who's
with you" step in the plan (once a conversation) and a mark on that exchange's cards. No audio or
voiceprint is stored. All data here is synthetic."""

import asyncio

from app import audit_chain, live, loops, main, planner
from app.schemas import Extraction, Moment
from app.store import store
from tests.test_grounding import OWNER, client  # noqa: F401  (the app with a seeded demo)

SAID = "My nephew should get the Jayanagar flat."
WISH = Moment(kind="wish", title="Jayanagar flat to my nephew", summary=SAID, quote=SAID)


class WS:
    async def send_json(self, data):
        pass


def exchange(monkeypatch, voices: str, audio: bytes | None = b"\x00\x01" * 800) -> dict:
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
                                    main._expand, {"step": None, "correct": None}, audio))
    return seen


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
