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
from app.schemas import ConversationTurn, Extraction, InventoryItem, Moment, Person, PlanReply
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
    async def understand(*args):
        return turn

    async def respond(*args):
        return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
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


def test_a_failed_extraction_logs_no_words(client, monkeypatch, caplog):
    """The extractor's invalid output holds the owner's words; the log says only what failed."""
    async def extract(owner, heard, said, known):
        return Extraction.model_validate_json('{"moments": [{"kind": "the papers are in the steel almirah"}]}')

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    with caplog.at_level(logging.INFO):
        asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, SAID, "Thank you.", main._expand))
    assert "extraction failed (ValidationError: moments.0" in caplog.text and "test_grounding.py:" in caplog.text
    assert "almirah" not in caplog.text and "bedroom" not in caplog.text


def test_a_hindi_detail_nobody_said_is_cleared():
    heard = "मेरी एलआईसी पॉलिसी के कागज़ बेडरूम की अलमारी में हैं।"
    item = InventoryItem(**{**LIC, "nominee_or_beneficiary": "सुनीता देवी", "papers_at": "नीली फ़ाइल"})
    found = grounding.ground(Extraction(inventory=[item]), owner_words=heard,
                             known={"inventory": ["LIC Life insurance policy ••1234"]})
    assert not found.inventory[0].nominee_or_beneficiary and not found.inventory[0].papers_at
    said_place = InventoryItem(**{**LIC, "papers_at": "बेडरूम की अलमारी"})
    assert grounding.ground(Extraction(inventory=[said_place]), owner_words=heard,
                            known={"inventory": ["LIC ••1234"]}).inventory[0].papers_at == "बेडरूम की अलमारी"


def test_a_person_named_on_a_shared_document_gets_a_card():
    item = InventoryItem(category="insurance", institution="HDFC Life", product="Term life policy",
                         account_hint="••7781", nominee_or_beneficiary="Anjana Rao")
    found = grounding.ground(_turn(inventory=[item], people=[Person(name="Anjana Rao", relationship="wife")]),
                             owner_words="", from_document=True)
    assert [p.name for p in found.people] == ["Anjana Rao"]


def test_a_quick_yes_in_a_call_is_checked_against_the_question_just_asked(client, monkeypatch):
    question = "Are the LIC papers in the bedroom steel cupboard?"
    proposals.store.put("turns", {"id": "turn_q", "owner_id": OWNER, "at": 1.0, "role": "assistant", "text": question})
    proposals.store.update("owners", OWNER, {"last_said": {"text": "Tell me about your work.", "at": 0.5}})  # not yet updated

    async def extract(owner, heard, said, known):
        return Extraction(inventory=[InventoryItem(**LIC, papers_at="bedroom steel cupboard")])

    class WS:
        async def send_json(self, data):
            pass

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)
    asyncio.run(live._save_exchange(WS(), {"id": OWNER, "name": "Vikram Rao"}, "Yes.", "Thank you.", main._expand))
    lic = [i for i in proposals.store.list("inventory", owner_id=OWNER) if i.get("institution") == "LIC"]
    assert any(i.get("papers_at") == "bedroom steel cupboard" for i in lic)


# ---- Placeholders: the model once wrote "N/A · Flat in Mysore .." for a flat with no bank ----------

FLAT_SAID = "One wish: our flat in Mysore should go to Meena, and after her, to Priya and Arjun equally."


def test_a_placeholder_institution_and_an_empty_hint_become_empty():
    item = InventoryItem(category="property", institution="N/A", product="Flat in Mysore", account_hint="..",
                         intended_for="Meena")
    found = grounding.ground(_turn(inventory=[item]), owner_words=FLAT_SAID).inventory[0]
    assert (found.institution, found.account_hint, found.intended_for) == ("", "", "Meena")


@pytest.mark.parametrize("value", ["N/A", "n/a", "NA", "nil", "-", "Not applicable", "  ", None])
def test_placeholders_say_nothing(value):
    assert grounding.placeholder(value)


@pytest.mark.parametrize("value", ["LIC", "SBI", "Nana", "Navi Mumbai", "बेडरूम की अलमारी"])
def test_real_values_are_not_placeholders(value):
    assert not grounding.placeholder(value)


def test_a_kept_property_with_no_institution_reads_plainly():
    from app import knowledge
    assert knowledge._description("inventory", {"product": "Flat in Mysore", "institution": ""}) == \
        "Flat in Mysore; no nominee recorded"
    assert not knowledge._present("N/A") and not knowledge._present("NA")


def test_a_claim_letter_to_nobody_is_dropped(monkeypatch):
    from app.schemas import ClaimLetter, Settlement

    async def fake(**kwargs):
        return Settlement(checklist=[], unclaimed_asset_searches=[], letters=[
            ClaimLetter(institution="LIC", subject="Claim", body="Dear LIC"),
            ClaimLetter(institution="N/A", subject="Flat", body="Dear [name]")])

    monkeypatch.setattr(loops, "generate_json", fake)
    plan = asyncio.run(loops.plan_settlement({"name": "Vikram Rao"}, [], []))
    assert [letter.institution for letter in plan.letters] == ["LIC"]


# The case from the demo (18 Oct 2026): the owner spoke about property papers and the cupboard keys,
# and two cards came back as updates of a kept "LIC policy details" he never mentioned.
KEPT_LIC = {"kind": "practical", "title": "LIC policy details", "quote": "My LIC policy matures next year.",
            "summary": "Vikram Rao has an LIC policy that matures next year."}
PROPERTY = "The property papers are in the cupboard, and my wife has the keys."


def _keep_lic():
    proposals.approve("moments", proposals.save_moment(OWNER, Moment(**KEPT_LIC).model_dump(), {}))


def test_a_saved_title_nobody_named_is_not_an_update(client, monkeypatch):
    _keep_lic()
    wrong = Moment(kind="practical", title="LIC policy details", quote=PROPERTY,
                   summary="The LIC policy documents are in the cupboard; his wife has the keys.")
    cards = _chat(client, monkeypatch, PROPERTY, _turn(moments=[wrong]))
    assert not [c for c in cards if c["collection"] == "moments"]


def test_a_saved_title_the_owner_or_saathi_named_is_an_update(client, monkeypatch):
    _keep_lic()
    detail = Moment(kind="practical", title="LIC policy details", quote="My wife has the keys.",
                    summary="The LIC policy papers are in the cupboard; his wife has the keys.")
    found = grounding.ground(_turn(moments=[detail]), owner_words="My wife has the keys.",
                             saathi_words="Who has the keys to the cupboard with the LIC papers?",
                             known=proposals.known(OWNER))
    assert found.moments  # Saathi's question named the policy
    named = detail.model_copy(update={"quote": "For the LIC policy, my wife has the keys."})
    assert grounding.ground(_turn(moments=[named]), owner_words=named.quote, known=proposals.known(OWNER)).moments
    new = Moment(kind="practical", title="Property papers", quote=PROPERTY, summary="The property papers are in the cupboard.")
    assert grounding.ground(_turn(moments=[new]), owner_words=PROPERTY, known=proposals.known(OWNER)).moments


def test_two_details_about_one_kept_moment_make_one_update_card(client):
    _keep_lic()
    first = proposals.save_moment(OWNER, {**KEPT_LIC, "quote": "The LIC papers are in the cupboard.",
                                          "summary": "The LIC papers are in the cupboard."}, {})
    second = proposals.save_moment(OWNER, {**KEPT_LIC, "quote": "My wife has the keys to the LIC cupboard.",
                                           "summary": "His wife has the keys to the cupboard with the LIC papers."}, {})
    assert first["updates"] and second["id"] == first["id"]
    assert "keys" in second["summary"] and "cupboard" in second["summary"]


def test_another_insurer_or_a_generic_word_does_not_name_a_kept_moment(client):
    _keep_lic()
    for said_now in ("My HDFC policy papers are in the cupboard.", "Here are the details: the papers are in the cupboard."):
        card = Moment(kind="practical", title="LIC policy details", quote=said_now, summary="Papers in the cupboard.")
        assert not grounding.ground(_turn(moments=[card]), owner_words=said_now, known=proposals.known(OWNER)).moments


def test_a_card_waiting_for_keep_can_be_followed_up_with_it(client):
    said_first = "My grandfather built our house in Udupi."
    proposals.save_moment(OWNER, {"kind": "story", "title": "Grandfather's house", "quote": said_first,
                                  "summary": "His grandfather built the family house in Udupi."}, {})
    more = Moment(kind="story", title="Grandfather's house", quote="He planted mango trees around it.",
                  summary="His grandfather built the house in Udupi and planted mango trees around it.")
    assert grounding.ground(_turn(moments=[more]), owner_words=more.quote,
                            saathi_words="What do you remember about it?", known=proposals.known(OWNER)).moments


def test_a_kept_title_with_only_common_words_is_not_checked(client):
    proposals.approve("moments", proposals.save_moment(OWNER, {"kind": "wish", "title": "My will", "quote": "I made a will.",
                                                               "summary": "Vikram Rao has made a will."}, {}))
    card = Moment(kind="wish", title="My will", quote="It is with the lawyer.", summary="His will is with the lawyer.")
    assert grounding.ground(_turn(moments=[card]), owner_words=card.quote, known=proposals.known(OWNER)).moments


def test_saying_a_kept_moment_again_does_not_grow_its_waiting_update(client):
    _keep_lic()
    update = proposals.save_moment(OWNER, {**KEPT_LIC, "quote": "The LIC papers are in the cupboard.",
                                           "summary": "The LIC papers are in the cupboard."}, {})
    assert proposals.save_moment(OWNER, dict(KEPT_LIC), {}) is None  # already kept
    assert proposals.store.get("moments", update["id"])["summary"] == update["summary"]
