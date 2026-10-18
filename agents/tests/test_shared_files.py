"""A photo or document the owner shares gets its own step in the plan: Saathi says what it found in
it and asks about the one most important thing it doesn't show, or asks what it is when nothing was
found. An optional comment from the owner goes with the file. All data here is synthetic."""

import pytest
from fastapi.testclient import TestClient

from app import loops, main, planner
from app.schemas import ConversationTurn, InventoryItem, PlanReply
from tests.test_knowledge import OWNER

POLICY = {"category": "insurance", "institution": "LIC", "product": "Jeevan Anand policy", "account_hint": "..4492"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%synthetic\n"


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def share(client, monkeypatch, reading: ConversationTurn, *, text: str | None = None,
          name: str = "policy.png", body: bytes = PNG, mime: str = "image/png") -> dict:
    given = {}

    async def understand(owner, last_said, said, upload, known):
        given["said"], given["upload"] = said, upload
        return reading

    async def respond(owner, step, last_said, said, known, earlier=None):
        given["step"] = step
        return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    r = client.post(f"/api/owners/{OWNER}/conversation", data={"text": text} if text else {},
                    files={"file": (name, body, mime)})
    assert r.status_code == 200
    return given


def reading(**cards) -> ConversationTurn:
    return ConversationTurn(heard="A photo of an insurance policy document", owner_language="English", **cards)


def test_a_photo_says_what_was_found_and_asks_for_what_is_missing(client, monkeypatch):
    given = share(client, monkeypatch, reading(inventory=[InventoryItem(**POLICY)]))
    step = given["step"]
    assert step["source"] == "shared" and step["opens"] == "saathi"
    assert "LIC Jeevan Anand policy, ending 4492" in step["ask"]
    assert "who the nominee is for Jeevan Anand policy" in step["ask"]


def test_a_document_works_the_same_way(client, monkeypatch):
    item = InventoryItem(**POLICY, nominee_or_beneficiary="Ajay")
    given = share(client, monkeypatch, reading(inventory=[item]), name="policy.pdf", body=PDF, mime="application/pdf")
    assert given["step"]["source"] == "shared" and "ending 4492" in given["step"]["ask"]
    assert "what they'd like their family to know" in given["step"]["ask"]  # nothing missing: Saathi asks why it matters


def test_nothing_found_means_an_honest_question(client, monkeypatch):
    step = share(client, monkeypatch, reading())["step"]
    assert step["source"] == "shared" and "couldn't find details" in step["ask"] and "what it is" in step["ask"]


def test_the_owners_comment_goes_with_the_file(client, monkeypatch):
    comment = "This is Papa's old policy."
    given = share(client, monkeypatch, reading(), text=comment)
    assert given["said"] == comment and given["upload"][1] == "image/png"  # both reach Gemini together
    assert "Thank them" in given["step"]["ask"]  # with a comment, Saathi doesn't ask what it is
    turns = [t for t in main.store.list("turns", owner_id=OWNER) if t["role"] == "owner"]
    assert max(turns, key=lambda t: t["at"])["text"] == comment


def test_typed_chat_without_a_file_gets_no_shared_step(client, monkeypatch):
    async def understand(*args):
        return ConversationTurn(heard="", owner_language="English")

    given = {}

    async def respond(owner, step, *args, **kwargs):
        given["step"] = step
        return PlanReply(reply="Thank you.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    client.post(f"/api/owners/{OWNER}/conversation", data={"text": "Hello Saathi"})
    assert (given["step"] or {}).get("source") != "shared"


def test_a_property_without_a_recipient_asks_who_it_should_go_to():
    flat = {"category": "property", "institution": "", "product": "Flat in Bangalore", "account_hint": ""}
    step = planner.shared_step("turn_x", [flat], [], comment=False)
    assert "who Flat in Bangalore should go to" in step["ask"]


def test_saathi_says_what_it_found_once_and_then_carries_on(client, monkeypatch):
    first = share(client, monkeypatch, reading(inventory=[InventoryItem(**POLICY)]))["step"]
    assert first["source"] == "shared"
    main.store.update("owners", OWNER, {"last_said": {"text": "I can see your LIC policy.", "step": first["id"],
                                                      "at": main.time.time()}})

    async def understand(*args):
        return ConversationTurn(heard="", owner_language="English")  # the owner talks about something else

    given = {}

    async def respond(owner, step, *args, **kwargs):
        given["step"] = step
        return PlanReply(reply="Thank you.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    client.post(f"/api/owners/{OWNER}/conversation", data={"text": "Let me tell you about my first job."})
    assert (given["step"] or {}).get("id") != first["id"]


def test_a_reset_starts_the_conversation_plan_again(client, monkeypatch):
    share(client, monkeypatch, reading(inventory=[InventoryItem(**POLICY)]))
    assert planner.view(planner.current(OWNER))["current"]["source"] == "shared"
    assert client.post("/api/demo/seed").status_code == 200
    assert planner.view(planner.current(OWNER))["current"]["source"] != "shared"
