"""Cards say only what was said (AGENTS.md rule 8; app/grounding.py). The model once filled "where
the papers are" with the example from its own instructions ("blue folder, bedroom cupboard") when
the owner had said "master bedroom". Code now checks every card against what was said before it's
saved: a quote the owner never said drops the card, and a value nobody said is cleared, so it stays
a gap. All data here is synthetic."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app import grounding, live, loops, main, proposals
from app.schemas import ConversationTurn, Extraction, InventoryItem, Moment, Person
from tests.test_knowledge import OWNER

SAID = "The LIC papers are in the master bedroom."
LIC = {"category": "insurance", "institution": "LIC", "product": "Life insurance policy", "account_hint": "••1234"}
PAPERS = {"kind": "practical", "title": "LIC Life insurance policy",
          "summary": "The papers for the LIC life insurance policy are kept in the master bedroom.", "quote": SAID}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    proposals.approve("inventory", proposals.save_inventory(OWNER, InventoryItem(**LIC).model_dump(), {}))
    return c


def _turn(**cards) -> ConversationTurn:
    return ConversationTurn(heard="", owner_language="English", reply="Thank you, Vikram ji.", reply_language="en-IN",
                            **cards)


def _chat(client, monkeypatch, text: str, turn: ConversationTurn) -> list[dict]:
    async def converse(*args):
        return turn

    monkeypatch.setattr(loops, "converse", converse)
    r = client.post(f"/api/owners/{OWNER}/conversation", data={"text": text})
    assert r.status_code == 200
    return r.json()["assistant_turn"]["proposals"]


def test_a_place_nobody_said_is_not_saved_the_case_from_the_demo(client, monkeypatch):
    invented = InventoryItem(**LIC, papers_at="blue folder, bedroom steel cupboard")
    cards = _chat(client, monkeypatch, SAID, _turn(moments=[Moment(**PAPERS)], inventory=[invented]))
    item = next(c for c in cards if c["collection"] == "inventory")
    assert "folder" not in str(item.get("papers_at") or "")  # cleared: it stays a gap Saathi asks about
    assert any(c["collection"] == "moments" and c["quote"] == SAID for c in cards)  # what he did say is kept


def test_a_place_the_owner_said_is_saved(client, monkeypatch):
    cards = _chat(client, monkeypatch, SAID, _turn(inventory=[InventoryItem(**LIC, papers_at="master bedroom")]))
    assert next(c for c in cards if c["collection"] == "inventory")["papers_at"] == "master bedroom"


def test_a_card_whose_quote_the_owner_never_said_is_dropped(client, monkeypatch):
    made_up = {**PAPERS, "title": "Papers in the blue folder", "quote": "All my papers are in the blue folder."}
    cards = _chat(client, monkeypatch, SAID, _turn(moments=[Moment(**made_up)]))
    assert not [c for c in cards if c["collection"] == "moments"]


def test_digits_and_a_nominee_nobody_gave_are_cleared(client, monkeypatch):
    item = InventoryItem(category="bank_account", institution="SBI", product="Savings account",
                         account_hint="••4821", nominee_or_beneficiary="Meena")
    cards = _chat(client, monkeypatch, "I also have an SBI savings account.", _turn(inventory=[item]))
    saved = next(c for c in cards if c["collection"] == "inventory")
    assert "4821" not in saved["account_hint"] and not saved.get("nominee_or_beneficiary")


def test_yes_to_saathis_question_supports_its_answer(client, monkeypatch):
    item = InventoryItem(**{**LIC, "nominee_or_beneficiary": "Meena"})
    found = grounding.ground(_turn(inventory=[item]), owner_words="Yes.",
                             saathi_words="Is Meena the nominee for your LIC policy?")
    assert found.inventory[0].nominee_or_beneficiary == "Meena"


def test_a_person_nobody_named_is_not_added(client, monkeypatch):
    found = grounding.ground(_turn(people=[Person(name="Meena", relationship="wife"),
                                           Person(name="Ajay", relationship="nephew")]),
                             owner_words="Ajay is my nephew.")
    assert [p.name for p in found.people] == ["Ajay"]


def test_a_document_supplies_its_own_account_details(client):
    item = InventoryItem(category="insurance", institution="HDFC Life", product="Term life policy",
                         account_hint="••7781", nominee_or_beneficiary="Priya Rao")
    found = grounding.ground(_turn(inventory=[item]), owner_words="", from_document=True)
    assert found.inventory[0].account_hint == "••7781" and found.inventory[0].nominee_or_beneficiary == "Priya Rao"


def test_hindi_speech_is_checked_by_its_quote_and_english_values_are_kept():
    heard = "एलआईसी के कागज़ बेडरूम की अलमारी में हैं।"
    card = Moment(**{**PAPERS, "quote": heard})
    found = grounding.ground(Extraction(moments=[card], inventory=[InventoryItem(**LIC, papers_at="bedroom almirah")]),
                             owner_words=heard, known={"inventory": ["LIC Life insurance policy ••1234"]})
    assert found.moments and found.inventory[0].papers_at == "bedroom almirah"  # can't match words across scripts
    assert grounding.ground(Extraction(moments=[Moment(**{**PAPERS, "quote": "कागज़ नीले फ़ोल्डर में हैं।"})]),
                            owner_words=heard).moments == []


def test_a_quote_that_differs_by_a_word_or_punctuation_still_counts():
    heard = "Well, the LIC papers are in the master bedroom, in the cupboard."
    assert grounding.said("The LIC papers are in the master bedroom in the cupboard", heard)
    assert grounding.said("the LIC papers are in our master bedroom", heard)
    assert not grounding.said("All my papers are in the blue folder", heard)


def test_a_live_call_card_is_checked_too_and_no_words_are_logged(client, monkeypatch, caplog):
    async def extract(owner, heard, said, known):
        return Extraction(moments=[Moment(**PAPERS)],
                          inventory=[InventoryItem(**LIC, papers_at="blue folder, bedroom steel cupboard")])

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    with caplog.at_level(logging.INFO):
        asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, SAID, "Thank you.", main._expand))
    lic = [i for i in proposals.store.list("inventory", owner_id=OWNER) if i.get("institution") == "LIC"]
    assert lic and not any("folder" in str(i.get("papers_at") or "") for i in lic)
    assert "cleared 1" in caplog.text and "folder" not in caplog.text and "bedroom" not in caplog.text
