"""Kept knowledge as an OKF v0.2 bundle (PR-7.1, 7.2, 7.4): Keep is the commit, the bundle is
valid OKF, an update deprecates the old version, the index can be rebuilt, numbers are masked,
the vault never enters, and Firestore keeps drafts only."""

import pathlib
import threading

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import demo, knowledge, main, media, proposals
from app.governance import core
from app.store import new_id, store

OWNER = "owner_demo_vikram"
STORY = {"kind": "story", "title": "My first salary", "people": ["Amma"], "places": ["Mysore"], "year": "1987",
         "summary": "His first job was at a textile mill in Mysore; he gave his first salary to his mother.",
         "quote": "My first job was a textile mill in Mysore. I gave my whole first salary to my mother."}
POLICY = {"category": "insurance", "institution": "LIC", "product": "Term life policy", "account_hint": "••4821",
          "holder_name": "Vikram Rao", "nominee_or_beneficiary": "Ajay", "approximate_value": None,
          "missing_fields": []}


@pytest.fixture(autouse=True)
def fresh():
    TestClient(main.app).post("/api/demo/seed")


def _keep(collection: str, fields: dict) -> dict:
    save = {"moments": proposals.save_moment, "inventory": proposals.save_inventory}[collection]
    return proposals.approve(collection, save(OWNER, fields, {}))


def _files() -> dict[str, str]:
    return {n: (media.load(OWNER, n) or b"").decode() for n in media.names(OWNER, "kept/") if n.endswith(".md")}


def assert_conformant() -> None:
    """OKF v0.2 section 8: every non-reserved Markdown file has frontmatter with a non-empty type;
    the root index carries only okf_version; the log is grouped by date, newest first."""
    files = _files()
    assert files, "the bundle is empty"
    for name, text in files.items():
        rel = name.removeprefix("kept/")
        if rel == "index.md":
            front, _ = knowledge.parse(text)
            assert front == {"okf_version": "0.2"}
            continue
        if rel == "log.md":
            assert text.startswith("# Log")
            days = [line[3:] for line in text.splitlines() if line.startswith("## ")]
            assert days == sorted(days, reverse=True)
            continue
        front, _ = knowledge.parse(text)
        assert isinstance(front.get("type"), str) and front["type"], name
        assert front.get("status") in ("stable", "deprecated", "draft"), name


def test_keep_writes_a_valid_okf_concept():
    kept = _keep("moments", STORY)
    text = media.load(OWNER, f"kept/{kept['concept']}").decode()
    front, body = knowledge.parse(text)
    assert front["type"] == "Story" and front["title"] == "My first salary" and front["status"] == "stable"
    assert front["verified"]["by"] == f"human:owner-{OWNER}"  # the owner's Keep is the human check
    assert front["generated"]["by"].startswith("saathi/")
    assert front["sources"][0]["resource"] == "anvaya:conversation"
    assert "I gave my whole first salary" in body
    assert_conformant()


def test_firestore_keeps_drafts_only():
    _keep("moments", STORY)
    _keep("inventory", POLICY)
    for collection in ("moments", "inventory", "people"):
        assert not store.list(collection, owner_id=OWNER, status="approved"), collection
    assert [i["institution"] for i in knowledge.items(OWNER, "inventory")] == ["LIC"]


def test_accounts_ask_to_be_reconfirmed_after_a_year():
    kept = _keep("inventory", POLICY)
    front, _ = knowledge.parse(media.load(OWNER, f"kept/{kept['concept']}").decode())
    assert front["type"] == "Insurance Policy" and kept["concept"].startswith("policies/")
    assert front["stale_after"] > front["verified"]["at"]


def test_an_update_leaves_one_stable_and_one_deprecated_version():
    first = _keep("moments", STORY)
    update = proposals.save_moment(OWNER, {**STORY, "summary": STORY["summary"] + " She cried with joy.",
                                           "quote": "She cried with joy."}, {})
    second = proposals.approve("moments", update)
    statuses = sorted(i["okf_status"] for i in knowledge.items(OWNER, "moments", include_replaced=True))
    assert statuses == ["deprecated", "stable"]
    old_front, _ = knowledge.parse(media.load(OWNER, f"kept/{first['concept']}").decode())
    assert old_front["status"] == "deprecated" and old_front["superseded_by"] == second["id"]
    log = media.load(OWNER, "kept/log.md").decode()
    assert "**Deprecation**" in log and "**Update**" in log
    assert_conformant()


def test_the_index_rebuilds_to_the_same_result():
    _keep("moments", STORY)
    _keep("inventory", POLICY)
    index, catalog = media.load(OWNER, "kept/index.md"), knowledge.catalog(OWNER)
    assert "[My first salary](/stories/" in index.decode() and "# Insurance policies" in index.decode()
    media.delete(OWNER, "kept/index.md")
    media.delete(OWNER, "kept/.catalog.json")
    knowledge.rebuild(OWNER)
    assert media.load(OWNER, "kept/index.md") == index
    assert knowledge.catalog(OWNER)["concepts"] == catalog["concepts"]


def test_long_numbers_are_masked_everywhere():
    kept = _keep("moments", {**STORY, "title": "Salary account",
                             "quote": "The salary went into account 1234 5678 9012, and my PAN is fine."})
    text = media.load(OWNER, f"kept/{kept['concept']}").decode()
    assert "1234 5678 9012" not in text and "••9012" in text
    assert "9012" in kept["quote"] and "5678" not in kept["quote"]


def test_the_vault_never_enters_the_bundle():
    with pytest.raises(knowledge.NotKnowledge):
        knowledge.commit("vault_items", {"id": new_id("vault"), "ciphertext": "x"}, OWNER)


def test_only_markdown_inside_the_bundle_can_be_read():
    _keep("moments", STORY)
    assert knowledge.read(OWNER, "index.md").startswith("---")
    for path in (".catalog.json", "../owner_other/kept/index.md", "/stories/../../secret.md", "kept.json"):
        assert knowledge.read(OWNER, path) is None, path


def test_the_family_comes_from_kept_people():
    assert knowledge.family(OWNER) == ["Priya Rao (daughter)"]  # the persona's starting knowledge
    priya = knowledge.items(OWNER, "people")[0]
    front, _ = knowledge.parse(media.load(OWNER, f"kept/{priya['concept']}").decode())
    assert front["generated"]["by"] == "process:demo-persona" and "verified" not in front  # not the owner's Keep
    assert TestClient(main.app).get(f"/api/owners/{OWNER}").json()["family"] == ["Priya Rao (daughter)"]


def test_a_kept_card_still_shows_in_the_conversation():
    card = proposals.save_moment(OWNER, STORY, {})
    store.put("turns", {"id": "turn_k", "owner_id": OWNER, "at": 1.0, "role": "assistant", "text": "Saved?",
                        "proposals": {"moments": [card["id"]], "inventory": [], "people": []}})
    proposals.approve("moments", card)
    turn = next(t for t in TestClient(main.app).get(f"/api/owners/{OWNER}/conversation").json()["turns"]
                if t["id"] == "turn_k")
    assert turn["proposals"][0]["status"] == "approved" and turn.get("discarded") == 0


def test_reset_removes_the_bundle():
    _keep("moments", STORY)
    TestClient(main.app).post("/api/demo/seed")
    assert knowledge.items(OWNER, "moments") == []
    assert knowledge.family(OWNER) == ["Priya Rao (daughter)"]


def test_overlapping_keeps_never_lose_a_concept():
    cards = [proposals.save_moment(OWNER, {**STORY, "title": f"Story {i}", "quote": f"Quote number {i} " * 3,
                                            "summary": f"Summary {i} " * 5}, {}) for i in range(8)]
    errors = []

    def keep(card):
        try:
            proposals.approve("moments", card)
        except Exception as e:  # a lost Keep must fail the test, not vanish in a thread
            errors.append(e)

    threads = [threading.Thread(target=keep, args=(c,)) for c in cards]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(knowledge.items(OWNER, "moments")) == 8
    # The index and log were never overwritten by an older Keep: all 8 are there.
    index, log = media.load(OWNER, "kept/index.md").decode(), media.load(OWNER, "kept/log.md").decode()
    assert all(f"[Story {i}]" in index for i in range(8)) and log.count("**Creation**") >= 8
    assert_conformant()


def test_copies_from_before_the_bundle_start_again_once():
    copy_id = core.user_key(demo.LOCAL)
    store.put("moments", {"id": "m_old", "owner_id": OWNER, "status": "approved", "kind": "story", "title": "old"})
    store.update("demo_copies", copy_id, {"knowledge": None})
    TestClient(main.app).get("/api/me")
    assert store.get("moments", "m_old") is None  # kept items no longer live in Firestore
    assert store.get("demo_copies", copy_id)["knowledge"] == demo.KNOWLEDGE_VERSION
    TestClient(main.app).get("/api/me")  # and only once
    assert knowledge.family(OWNER) == ["Priya Rao (daughter)"]


def test_concepts_parse_as_plain_yaml():
    kept = _keep("inventory", POLICY)
    text = media.load(OWNER, f"kept/{kept['concept']}").decode()
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["links"] == {} and front["record"]["institution"] == "LIC"


def test_two_updates_to_the_same_item_keep_one_current_version_with_both():
    account = {**POLICY, "category": "bank_account", "institution": "SBI", "product": "Savings",
               "account_hint": "••1187", "nominee_or_beneficiary": None}
    kept = _keep("inventory", account)
    nominee = proposals.save_inventory(OWNER, {**account, "nominee_or_beneficiary": "Priya"}, {})
    value = proposals.save_inventory(OWNER, {**account, "approximate_value": "4 lakh"}, {})
    assert nominee["updates"] == value["updates"] == kept["id"]  # both cards point at the first version
    proposals.approve("inventory", nominee)
    proposals.approve("inventory", value)
    current = knowledge.items(OWNER, "inventory")
    assert len(current) == 1
    assert current[0]["nominee_or_beneficiary"] == "Priya" and current[0]["approximate_value"] == "4 lakh"


def test_masking_hides_identifiers_but_keeps_dates_and_amounts():
    kept = _keep("moments", {**STORY, "title": "Numbers", "summary": "Born 12-05-1951; worth 1500000 or 15,00,000; "
                             "years 1965-70 and 2019 2020.",
                             "quote": "Account 1234.5678.9012, policy LIC/123/456/789, PAN ABCDE1234F."})
    assert kept["summary"] == "Born 12-05-1951; worth 1500000 or 15,00,000; years 1965-70 and 2019 2020."
    assert kept["quote"] == "Account ••9012, policy LIC/••6789, PAN ••234F."  # the prefix isn't a number
    assert knowledge.mask(123456789012) == "••9012" and knowledge.mask(1500000) == 1500000


def test_edits_cannot_change_where_a_card_came_from():
    card = proposals.save_moment(OWNER, STORY, {"recording": "gs://bucket/owner_demo_vikram/rec_1.webm"})
    TestClient(main.app).post(f"/api/proposals/{card['id']}/decision", json={
        "collection": "moments", "decision": "approve",
        "edits": {"recording": "account 123456789012", "document": "x", "title": "My first salary, edited"}})
    kept = knowledge.items(OWNER, "moments")[0]
    assert kept["recording"] == "gs://bucket/owner_demo_vikram/rec_1.webm" and "document" not in kept
    assert kept["title"] == "My first salary, edited"


def test_only_the_demo_itself_can_keep_unconfirmed_knowledge():
    with pytest.raises(knowledge.NotKnowledge):
        knowledge.commit("moments", {**STORY, "id": new_id("m")}, OWNER, verified=False)
    index = media.load(OWNER, "kept/index.md").decode()
    assert "Priya Rao (daughter)" in index and "(not confirmed by the owner)" in index


def test_the_live_call_knows_the_family(monkeypatch):
    from app import live

    seen = {}

    async def fake_call(ws, owner, expand):
        seen["family"] = owner.get("family")
        await ws.close()

    monkeypatch.setattr(live, "call", fake_call)
    try:
        with TestClient(main.app).websocket_connect(f"/api/owners/{OWNER}/live") as ws:
            ws.receive_text()
    except WebSocketDisconnect:
        pass  # the fake call hangs up straight away
    assert seen["family"] == ["Priya Rao (daughter)"]


def test_two_first_requests_start_an_old_copy_only_once():
    copy_id = core.user_key(demo.LOCAL)
    store.update("demo_copies", copy_id, {"knowledge": None})
    client = TestClient(main.app)
    threads = [threading.Thread(target=client.get, args=("/api/me",)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert knowledge.family(OWNER) == ["Priya Rao (daughter)"]
    assert len(knowledge.catalog(OWNER)["concepts"]) == 1



def test_a_reader_never_sees_a_half_written_file(monkeypatch):
    """save() writes a temporary file and renames it into place. Here every write pauses halfway
    and another reader looks: it must still get the old file, and names() must not list the
    temporary one. (This is what made test_overlapping_keeps_never_lose_a_concept flaky.)"""
    old, new = b'{"v": "old"}' * 50, b'{"v": "new"}' * 80
    media.save(OWNER, "kept/probe.json", old, "application/json")
    seen = []

    def write_half_then_look(self, data):
        with open(self, "wb") as f:
            f.write(data[: len(data) // 2])
            f.flush()
            seen.append((media.load(OWNER, "kept/probe.json"), media.names(OWNER, "kept/")))
            f.write(data[len(data) // 2:])
        return len(data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", write_half_then_look)
    media.save(OWNER, "kept/probe.json", new, "application/json")
    monkeypatch.undo()
    assert seen[0][0] == old and not any(n.rsplit("/", 1)[-1].startswith(".") for n in seen[0][1])
    assert media.load(OWNER, "kept/probe.json") == new
    media.delete(OWNER, "kept/probe.json")


def test_kept_items_have_the_list_fields_cards_have():
    # A kept account without missing_fields blanked the app's conversation screen (iPhone, 4 October).
    account = _keep("inventory", POLICY)
    story = _keep("moments", {**STORY, "people": [], "places": []})
    assert account["missing_fields"] == [] and knowledge.items(OWNER, "inventory")[0]["missing_fields"] == []
    kept_story = knowledge.items(OWNER, "moments")[0]
    assert kept_story["people"] == [] and kept_story["places"] == [] and kept_story["intended_for"] == []
    assert story["status"] == "approved"


def test_each_kept_item_says_when_it_was_kept():
    """PR-2.13.1: the time an item was kept, in the list the owner's book reads and on the item itself."""
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc).replace(microsecond=0)
    kept = _keep("moments", STORY)
    [row] = [i for i in TestClient(main.app).get(f"/api/owners/{OWNER}/kept").json()["items"] if i["id"] == kept["id"]]
    at = datetime.strptime(row["kept_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= at <= datetime.now(timezone.utc) and kept["kept_at"] == row["kept_at"]
