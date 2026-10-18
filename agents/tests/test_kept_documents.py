"""A shared photo or document can be kept in its own right: a card for the file, with the owner's
comment; once kept it is a Document in the bundle, linked both ways to the items found in it. The
owner can open it; the family only after the handover; nobody else. Discard and removal delete the
file when nothing uses it. All data here is synthetic."""

import pytest
from fastapi.testclient import TestClient

from app import knowledge, loops, main, media, release
from app.schemas import ConversationTurn, InventoryItem, PlanReply
from app.store import store
from tests.people import acting
from tests.test_knowledge import OWNER

POLICY = {"category": "insurance", "institution": "LIC", "product": "Jeevan Anand policy", "account_hint": "..4492"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def share(client, monkeypatch, *, text: str | None = None, inventory=()) -> dict:
    async def understand(*args):
        return ConversationTurn(heard="A photo of a policy document", owner_language="English",
                                inventory=list(inventory))

    async def respond(*args, **kwargs):
        return PlanReply(reply="Thank you, Vikram ji.", reply_language="en-IN")

    monkeypatch.setattr(loops, "understand", understand)
    monkeypatch.setattr(loops, "respond", respond)
    r = client.post(f"/api/owners/{OWNER}/conversation", data={"text": text} if text else {},
                    files={"file": ("policy.png", PNG, "image/png")})
    assert r.status_code == 200
    return r.json()


def cards(body: dict, collection: str) -> list[dict]:
    return [c for c in body["assistant_turn"]["proposals"] if c["collection"] == collection]


def keep(client, card: dict):
    return client.post(f"/api/proposals/{card['id']}/decision",
                       json={"collection": card["collection"], "decision": "approve"})


def test_a_shared_photo_gets_its_own_card_with_the_comment(client, monkeypatch):
    body = share(client, monkeypatch, text="Papa's old LIC policy. Ajay should get it.", inventory=[InventoryItem(**POLICY)])
    [doc] = cards(body, "documents")
    assert doc["title"] == "Papa's old LIC policy" and "Ajay should get it" in doc["description"]
    assert "LIC Jeevan Anand policy, ending 4492" in doc["description"] and doc["mime"] == "image/png"
    assert body["assistant_turn"]["proposals"][0]["collection"] == "documents"  # first, so Keep-all keeps it first
    [policy] = cards(body, "inventory")
    assert policy["document_id"] == doc["id"]


def test_kept_document_and_policy_link_both_ways_in_either_order(client, monkeypatch):
    body = share(client, monkeypatch, inventory=[InventoryItem(**POLICY)])
    [doc], [policy] = cards(body, "documents"), cards(body, "inventory")
    assert keep(client, policy).status_code == 200  # the item first, then the document
    assert keep(client, doc).status_code == 200
    cat = knowledge.catalog(OWNER)["concepts"]
    assert cat[doc["id"]]["graph"]["type"] == "Document"
    assert cat[doc["id"]]["graph"]["links"]["contains"] == [f"/{cat[policy['id']]['path']}"]
    assert cat[policy["id"]]["graph"]["links"]["evidence"] == [f"/{cat[doc['id']]['path']}"]


def test_the_owner_opens_it_the_family_only_after_the_handover(client, monkeypatch):
    body = share(client, monkeypatch, text="The deed of the Bangalore flat.")
    [doc] = cards(body, "documents")
    url = f"/api/owners/{OWNER}/documents/{doc['id']}/file"
    assert client.get(url).status_code == 404  # waiting for Keep: nobody can open it yet
    keep(client, doc)
    opened = client.get(url)
    assert opened.status_code == 200 and opened.content == PNG and "no-store" in opened.headers["cache-control"]
    listed = client.get(f"/api/owners/{OWNER}/documents").json()["documents"]
    assert [d["id"] for d in listed] == [doc["id"]] and listed[0]["comment"] == "The deed of the Bangalore flat."
    for person in ("priya", "anjali", "rahul", "admin"):
        assert client.get(url, headers=acting(person)).status_code == 403, person
    monkeypatch.setattr(release, "is_released", lambda case: True)
    assert client.get(url, headers=acting("priya")).content == PNG
    assert client.get(f"/api/owners/{OWNER}/documents", headers=acting("priya")).status_code == 200
    assert client.get(url, headers=acting("anjali")).status_code == 403  # a reviewer never opens it


def test_discarding_the_conversation_deletes_the_unkept_file(client, monkeypatch):
    body = share(client, monkeypatch)
    [doc] = cards(body, "documents")
    path = store.get("documents", doc["id"])["document"]
    assert media.load_path(OWNER, path) == PNG
    turn_ids = [body["owner_turn"]["id"], body["assistant_turn"]["id"]]
    assert client.post(f"/api/owners/{OWNER}/conversation/forget", json={"turn_ids": turn_ids}).status_code == 200
    assert store.get("documents", doc["id"]) is None and media.load_path(OWNER, path) is None


def test_removing_a_kept_document_deletes_its_file(client, monkeypatch):
    body = share(client, monkeypatch, text="An old receipt.")
    [doc] = cards(body, "documents")
    path = store.get("documents", doc["id"])["document"]
    keep(client, doc)
    assert client.delete(f"/api/owners/{OWNER}/kept/{doc['id']}").status_code == 200
    assert media.load_path(OWNER, path) is None
    assert client.get(f"/api/owners/{OWNER}/documents/{doc['id']}/file").status_code == 404


def test_a_path_outside_the_owners_folder_is_never_read():
    assert media.load_path(OWNER, "gs://bucket/someone_else/doc_x.png") is None
    assert media.load_path(OWNER, f"gs://bucket/{OWNER}/../someone_else/doc_x.png") is None


def test_the_knowledge_map_shows_the_document_and_what_was_found_in_it(client, monkeypatch):
    from app import knowledge_map
    body = share(client, monkeypatch, inventory=[InventoryItem(**POLICY)])
    [doc], [policy] = cards(body, "documents"), cards(body, "inventory")
    keep(client, doc)
    keep(client, policy)
    graph = knowledge_map.build(OWNER, "Vikram")
    assert next(n for n in graph["nodes"] if n["id"] == doc["id"])["group"] == "document"
    edges = [(e["from"], e["to"], e["label"]) for e in graph["edges"]]
    assert (doc["id"], policy["id"], "found in it") in edges and (doc["id"], "owner", "shared") in edges
    assert not any(e[2] == "evidence" for e in edges)  # drawn once, not both ways
