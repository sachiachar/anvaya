"""Closing the holes (PR-10.3): spoken replies only for Saathi's own words, with reuse,
and readiness and legal needs re-run only when their inputs change."""

import time

import pytest
from fastapi.testclient import TestClient

from app import loops, main, speech
from app.config import settings
from app.schemas import LegalNeeds, Readiness
from app.store import new_id, store

OWNER = "owner_demo_vikram"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "media_bucket", "")
    monkeypatch.setattr(settings, "local_media_dir", str(tmp_path))
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def turn(role, text):
    return store.put("turns", {"id": new_id("turn"), "owner_id": OWNER, "at": time.time(), "role": role, "text": text})


def test_only_saathis_own_replies_can_be_spoken(client, monkeypatch):
    spoken = []

    async def fake_speak(text):
        spoken.append(text)
        return b"RIFF-audio"

    monkeypatch.setattr(speech, "speak", fake_speak)
    assert client.post("/api/speech", json={"text": "anything at all"}).status_code == 422  # no free text
    assert client.post("/api/speech", json={"turn_id": "turn_does_not_exist"}).status_code == 404
    owner_turn = turn("owner", "my own words")
    assert client.post("/api/speech", json={"turn_id": owner_turn["id"]}).status_code == 404
    reply = turn("assistant", "Namaste Vikram ji.")
    assert client.post("/api/speech", json={"turn_id": reply["id"]}).content == b"RIFF-audio"
    assert spoken == ["Namaste Vikram ji."]


def test_replaying_or_repeating_the_same_words_is_free(client, monkeypatch):
    spoken = []

    async def fake_speak(text):
        spoken.append(text)
        return b"RIFF-audio"

    monkeypatch.setattr(speech, "speak", fake_speak)
    first = turn("assistant", "Who should receive the LIC policy?")
    client.post("/api/speech", json={"turn_id": first["id"]})
    client.post("/api/speech", json={"turn_id": first["id"]})          # play again
    repeat = turn("assistant", "Who should receive the LIC policy?")   # "repeat that": new reply, same words
    assert client.post("/api/speech", json={"turn_id": repeat["id"]}).content == b"RIFF-audio"
    assert len(spoken) == 1


def test_readiness_runs_again_only_when_something_changed(client, monkeypatch):
    runs = []

    async def fake_readiness(*args):
        runs.append(1)
        return Readiness(score=40, gaps=[])

    monkeypatch.setattr(loops, "assess_readiness", fake_readiness)
    assert client.post(f"/api/owners/{OWNER}/readiness").json()["score"] == 40
    second = client.post(f"/api/owners/{OWNER}/readiness").json()
    assert second["score"] == 40 and second.get("cached") and len(runs) == 1
    store.put("inventory", {"id": new_id("inv"), "owner_id": OWNER, "status": "approved", "category": "bank",
                            "institution": "SBI", "product": "Savings", "account_hint": "1187"})
    client.post(f"/api/owners/{OWNER}/readiness")
    assert len(runs) == 2


def test_legal_needs_are_cached_until_wishes_change(client, monkeypatch):
    runs = []

    async def fake_legal(*args):
        runs.append(1)
        return LegalNeeds(needs=[])

    monkeypatch.setattr(loops, "find_legal_needs", fake_legal)
    client.post(f"/api/owners/{OWNER}/legal-needs")
    assert client.post(f"/api/owners/{OWNER}/legal-needs").json().get("cached") and len(runs) == 1
    store.put("moments", {"id": new_id("m"), "owner_id": OWNER, "status": "approved", "kind": "wish",
                          "title": "Flat", "summary": "The flat goes to Meena", "quote": "", "people": [], "places": []})
    client.post(f"/api/owners/{OWNER}/legal-needs")
    assert len(runs) == 2
