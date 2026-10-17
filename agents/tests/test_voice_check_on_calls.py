"""The voice check on a call (voice fingerprinting): the start of the owner's side of each exchange is
compared with their voiceprint (voiceprint.py). A voice that doesn't match holds that exchange's
cards: Keep waits until the owner confirms it was them. The guardian records it for the team, and
Saathi warmly asks who's there. A match, or no answer, changes nothing. The speaker model and the
extractor are stubbed; all data here is synthetic."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import live, main
from app.store import store
from tests.test_another_voice import SAID, Session, exchange, owner_turns
from tests.test_grounding import OWNER, client  # noqa: F401  (the app with a seeded demo)


def answer(monkeypatch, result: str | None, seen: dict | None = None):
    """The voice check, stubbed: "match", "mismatch" or None (no answer)."""
    async def check(owner_id, pcm):
        if seen is not None:
            seen.update(owner=owner_id, pcm=pcm)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(live.voiceprint, "check", check)


def card() -> dict:
    [doc] = [c for c in store.list("moments", owner_id=OWNER, status="pending") if c["quote"] == SAID]
    return doc


def check_actions() -> list[dict]:
    return [a for a in store.list("guardian_actions") if (a.get("refs") or {}).get("voice") == "mismatch"]


def keep(doc: dict, **extra):
    return TestClient(main.app).post(f"/api/proposals/{doc['id']}/decision",
                                     json={"collection": "moments", "decision": "approve", **extra})


def test_a_mismatch_holds_the_cards_records_a_look_and_adds_a_warm_step(client, monkeypatch):  # noqa: F811
    seen = {}
    answer(monkeypatch, "mismatch", seen)
    audio = b"\x00\x01" * 800
    exchange(monkeypatch, "one", audio)
    assert seen == {"owner": OWNER, "pcm": audio}  # the exchange's own PCM, checked for this owner
    assert card()["voice_mismatch"] is True and owner_turns()[-1]["voice_mismatch"] is True
    [action] = check_actions()
    assert action["response"] == "add_check" and action["refs"]["turn"] == owner_turns()[-1]["id"]
    event = next(e for e in store.list("guardian_events") if e["id"] == action["event_id"])
    assert event["reason"] == "The voice on the call didn't sound like the owner's"
    plan = store.get("conversation_plans", OWNER)
    assert plan["steps"][0]["source"] == "voice" and plan["steps"][0]["tone"] == "warm"


def test_a_match_or_no_answer_changes_nothing(client, monkeypatch):  # noqa: F811
    for result in ("match", None, RuntimeError("the check broke")):
        answer(monkeypatch, result)
        exchange(monkeypatch, "one")
        assert not card().get("voice_mismatch") and not owner_turns()[-1].get("voice_mismatch")
        assert check_actions() == []
    assert keep(card()).status_code == 200  # no confirmation asked


def test_a_call_without_audio_is_not_checked(client, monkeypatch):  # noqa: F811
    seen = {}
    answer(monkeypatch, "mismatch", seen)
    exchange(monkeypatch, "one", audio=None)
    assert seen == {} and not card().get("voice_mismatch")


def test_keep_waits_until_the_owner_confirms(client, monkeypatch):  # noqa: F811
    answer(monkeypatch, "mismatch")
    exchange(monkeypatch, "one")
    held = card()
    refused = keep(held)
    assert refused.status_code == 409 and "Confirm it was you" in refused.json()["detail"]
    # An edit can't take the hold away.
    assert keep(held, edits={"voice_mismatch": False, "voice_confirmed": True}).status_code == 409
    assert store.get("moments", held["id"])["status"] == "pending"
    kept = keep(held, voice_confirmed=True)
    assert kept.status_code == 200 and not {"voice_mismatch", "voice_confirmed"} & set(kept.json())
    decision = max((a for a in store.list("guardian_actions") if a["action"] == "keep_card"), key=lambda a: a["at"])
    assert decision["refs"]["voice"] == "confirmed_by_owner" and decision["refs"]["card"] == held["id"]


def test_the_guardian_holds_the_card_until_the_owner_confirms(client, monkeypatch):  # noqa: F811
    answer(monkeypatch, "mismatch")
    exchange(monkeypatch, "one")
    held = card()
    keep(held)
    keep(held)  # asking twice is still one intervention
    refusals = [a for a in store.list("guardian_actions")
                if a["action"] == "keep_card" and a["refs"].get("card") == held["id"]]
    assert refusals and all(a["response"] == "deflect" and a["detectors"] == ["voice_rule"] for a in refusals)
    events = [e for e in store.list("guardian_events") if e["id"] == f"gev_keep_{held['id']}"]
    assert len(events) == 1 and "confirm it was them" in events[0]["reason"]
    assert keep(held, voice_confirmed=True).status_code == 200
    allowed = max((a for a in store.list("guardian_actions") if a["action"] == "keep_card"), key=lambda a: a["at"])
    assert allowed["response"] == "continue" and allowed["detectors"] == []


def test_discarding_a_held_card_needs_no_confirmation(client, monkeypatch):  # noqa: F811
    answer(monkeypatch, "mismatch")
    exchange(monkeypatch, "one")
    r = TestClient(main.app).post(f"/api/proposals/{card()['id']}/decision",
                                  json={"collection": "moments", "decision": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_a_mismatch_tells_saathi_to_read_the_plan_now_once_a_call(client, monkeypatch):  # noqa: F811
    answer(monkeypatch, "mismatch")
    session, state = Session(), {"step": None, "correct": None, "voice_heard": False, "voice_told": False}
    exchange(monkeypatch, "one", state=state, session=session)
    exchange(monkeypatch, "one", state=state, session=session)
    assert session.notes == [live.VOICE_NOTE]


def test_the_owners_audio_goes_with_the_exchange_their_words_are_in(monkeypatch):
    """Saathi speaks first, and the owner starts before Saathi's turn ends: the audio heard during
    Saathi's turn (the last 3 seconds) carries over to the exchange the owner's words reach, so the
    check hears the owner's voice, not silence."""

    def message(heard=None, said=None):
        content = SimpleNamespace(input_transcription=heard and SimpleNamespace(text=heard),
                                  output_transcription=said and SimpleNamespace(text=said),
                                  model_turn=None, interrupted=None, turn_complete=True)
        return SimpleNamespace(usage_metadata=None, tool_call=None, server_content=content)

    early = b"\x01\x00" * (12 * 16000)  # 12 seconds heard while Saathi was still speaking
    buffer = bytearray(early)

    class Live(Session):
        def __init__(self):
            super().__init__()
            self.turns = [[message(said="Namaste, Vikram ji.")], [message(heard="My papers are in the cupboard.")], []]

        async def receive(self):
            for m in self.turns.pop(0):
                yield m

    class Browser:
        async def send_json(self, data):
            pass

    saved = []

    def save(ws, owner, heard, said, expand, plan_state, audio, **kw):
        saved.append((heard, audio))
        if heard:
            assert not buffer  # the owner's exchange takes the audio, and the next one starts empty
        else:
            buffer.extend(b"\x02\x00" * 16000)  # one more second arrives before the owner's turn ends
        return asyncio.sleep(0)

    monkeypatch.setattr(live, "_save_exchange", save)
    asyncio.run(live._to_browser(Browser(), Live(), {"id": OWNER, "name": "Vikram Rao"}, main._expand, {},
                                 None, {"step": None}, buffer))
    assert saved[0] == ("", None)  # Saathi's turn: no audio of the owner's goes with it
    assert saved[1][0] == "My papers are in the cupboard."
    assert saved[1][1] == early[-live.CARRY_OVER_AUDIO:] + b"\x02\x00" * 16000
