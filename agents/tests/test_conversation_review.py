"""The end of a conversation (new design's Talk): a short summary the owner reads, and Discard, which
deletes the conversation and every card from it still waiting for Keep, but never anything kept."""

import time

import pytest
from fastapi.testclient import TestClient

from app import knowledge, loops, main, proposals
from app.schemas import ConversationSummary
from app.store import new_id, store

OWNER = "owner_demo_vikram"
STORY = {"kind": "story", "title": "First job at the mill", "people": [], "places": ["Mysore"], "year": "1987",
         "summary": "His first job was at a textile mill; he gave his first salary to his mother.",
         "quote": "I gave my whole first salary to my mother."}


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    return c


def _exchange(heard: str, said: str, moments: list[str] = ()) -> tuple[dict, dict]:
    now = time.time()
    owner_turn = store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": now, "role": "owner",
                                     "text": heard, "attachment": {"kind": "voice", "name": "live call"}})
    ai_turn = store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": now + 0.001, "role": "assistant",
                                  "text": said, "proposals": {"moments": list(moments), "inventory": [], "people": []}})
    return owner_turn, ai_turn


def test_discard_deletes_the_conversation_and_its_waiting_cards_but_nothing_kept(client):
    kept = proposals.save_moment(OWNER, STORY, {})
    waiting = proposals.save_moment(OWNER, {"kind": "advice", "title": "Read twice before signing", "people": [],
                                            "places": [], "year": None, "quote": "Never sign what you haven't read twice.",
                                            "summary": "Read every paper twice before signing it."}, {})
    assert waiting["id"] != kept["id"]
    assert client.post(f"/api/proposals/{kept['id']}/decision",
                       json={"collection": "moments", "decision": "approve"}).status_code == 200
    earlier = _exchange("Earlier, about the flat.", "Tell me about the flat.")
    call = _exchange("My first job was at the mill.", "How did she react?", [kept["id"], waiting["id"]])

    r = client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": [t["id"] for t in call]})
    assert r.status_code == 200 and r.json() == {"deleted_turns": 2, "deleted_items": 1}

    assert all(store.get("turns", t["id"]) is None for t in call)
    assert store.get("moments", waiting["id"]) is None  # the card still waiting is gone
    assert any(i["title"] == STORY["title"] for i in client.get(f"/api/owners/{OWNER}/kept").json()["items"])
    remaining = [t["id"] for t in client.get(f"/api/owners/{OWNER}/conversation").json()["turns"]]
    assert [t["id"] for t in earlier] == [t for t in remaining if t in {e["id"] for e in earlier}]
    assert not set(remaining) & {t["id"] for t in call}


def test_only_the_owner_discards_or_sums_up(client):
    ids = [t["id"] for t in _exchange("Hello.", "Namaste.")]
    for route in ("forget", "summary"):
        r = client.post(f"/api/owners/{OWNER}/conversation/{route}", json={"turn_ids": ids},
                        headers={"X-Acting-As": "priya"})
        assert r.status_code == 403, route
    assert all(store.get("turns", i) for i in ids)


def test_unknown_turns_are_ignored(client):
    r = client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": ["turn_nope"]})
    assert r.json() == {"deleted_turns": 0, "deleted_items": 0}
    assert client.post(f"/api/owners/{OWNER}/conversation/summary", json={"turn_ids": ["turn_nope"]}).status_code == 404
    assert client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": []}).status_code == 422


def test_summary_is_from_the_registered_agent_and_masks_numbers_in_code(client, monkeypatch):
    seen = {}

    async def fake_generate_json(**kwargs):
        seen.update(kwargs)
        return ConversationSummary(summary="You told Saathi about your LIC policy 123456789012 and your first job.")

    monkeypatch.setattr(loops, "generate_json", fake_generate_json)
    ids = [t["id"] for t in _exchange("My LIC policy is 123456789012.", "Who is the nominee?")]
    r = client.post(f"/api/owners/{OWNER}/conversation/summary", json={"turn_ids": ids})
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert "123456789012" not in summary and "••9012" in summary
    assert seen["agent"] == "saathi.summary"
    assert "My LIC policy" in seen["prompt"] and "Who is the nominee?" in seen["prompt"]
    assert not store.list("summaries")  # shown once, never stored
    assert knowledge.mask(summary) == summary


def test_discard_deletes_the_conversations_files_unless_something_kept_uses_them(client):
    from app import media
    shared = media.save(OWNER, "doc_sim_shared.jpg", b"photo of a policy", "image/jpeg")
    alone = media.save(OWNER, "doc_sim_alone.jpg", b"photo nobody kept", "image/jpeg")
    kept = proposals.save_inventory(OWNER, {"category": "insurance", "institution": "LIC", "product": "Jeevan Anand",
                                            "account_hint": "••4492", "nominee_or_beneficiary": "Ajay",
                                            "approximate_value": None, "missing_fields": []}, {"document": shared})
    proposals.approve("inventory", kept)
    waiting = proposals.save_moment(OWNER, STORY, {"document": alone})
    owner_turn, ai_turn = _exchange("Here are two photos.", "Thank you, I can see the policy.", [waiting["id"]])
    store.update("turns", owner_turn["id"], {"media": shared})
    reply = main._speech_name(ai_turn["text"])
    media.save(OWNER, reply, b"RIFF", "audio/wav")

    turns = client.get(f"/api/owners/{OWNER}/conversation").json()["turns"]
    assert all("media" not in t for t in turns)  # the stored path never reaches the app

    client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": [owner_turn["id"], ai_turn["id"]]})
    assert media.load(OWNER, "doc_sim_alone.jpg") is None  # only the discarded card used it
    assert media.load(OWNER, "doc_sim_shared.jpg") == b"photo of a policy"  # a kept item still uses it
    assert media.load(OWNER, reply) is None  # Saathi's spoken reply from the conversation


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_a_call_exchange_is_marked_saving_until_its_cards_are_ready(client, monkeypatch):
    import asyncio

    from app import live
    from app.schemas import Extraction, Moment
    seen_while_reading = []

    async def extract(owner, heard, said, known):
        seen_while_reading.extend(t for t in store.list("turns", owner_id=OWNER) if t.get("saving"))
        return Extraction(moments=[Moment(**STORY)])

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    ws = _WS()
    asyncio.run(live._save_exchange(ws, {"id": OWNER, "name": "Vikram Rao"}, "My first job...", "How lovely.", main._expand))
    assert {t["role"] for t in seen_while_reading} == {"owner", "assistant"}  # stored, and marked, before the cards
    turns = [t for t in store.list("turns", owner_id=OWNER) if t.get("text") in ("My first job...", "How lovely.")]
    assert len(turns) == 2 and not any(t["saving"] for t in turns)
    assert ws.sent[-1]["type"] == "turn" and ws.sent[-1]["assistant_turn"]["proposals"][0]["title"] == STORY["title"]


def test_an_exchange_discarded_while_saathi_reads_it_makes_no_cards(client, monkeypatch):
    import asyncio

    from app import live
    from app.schemas import Extraction, Moment

    async def extract(owner, heard, said, known):
        for t in store.list("turns", owner_id=OWNER):
            if t.get("saving"):
                store.delete("turns", t["id"])  # the owner discarded it meanwhile
        return Extraction(moments=[Moment(**STORY)])

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    asyncio.run(live._save_exchange(_WS(), {"id": OWNER, "name": "Vikram Rao"}, "Discard me.", "Okay.", main._expand))
    assert not [m for m in store.list("moments", owner_id=OWNER) if m.get("title") == STORY["title"]]


def test_discard_keeps_a_waiting_card_another_conversation_also_mentions(client):
    card = proposals.save_inventory(OWNER, {"category": "insurance", "institution": "LIC", "product": "Jeevan Anand",
                                            "account_hint": "••4492", "nominee_or_beneficiary": None,
                                            "approximate_value": None, "missing_fields": ["nominee"]}, {})
    _exchange("I have an LIC policy.", "Who is the nominee?", [])
    earlier = store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": time.time(), "role": "assistant",
                                  "text": "Noted the policy.", "proposals": {"inventory": [card["id"]]}})
    later = _exchange("Ajay is the nominee.", "Thank you.")
    store.update("turns", later[1]["id"], {"proposals": {"moments": [], "inventory": [card["id"]], "people": []}})
    r = client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": [t["id"] for t in later]})
    assert r.json()["deleted_items"] == 0
    assert store.get("inventory", card["id"]) and store.get("turns", earlier["id"])
