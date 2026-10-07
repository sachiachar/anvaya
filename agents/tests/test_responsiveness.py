"""The API stays responsive: sign-in certificates are fetched once, not on every request, and saving
a call exchange runs off the event loop, so a live call's audio never waits on the store."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, live, main
from app.schemas import Extraction, Moment
from app.store import store

OWNER = "owner_demo_vikram"
STORY = {"kind": "story", "title": "First job at the mill", "people": [], "places": ["Mysore"], "year": "1987",
         "summary": "His first job was at a textile mill; he gave his first salary to his mother.",
         "quote": "I gave my whole first salary to my mother."}
CERTS = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"


class _Response:
    def __init__(self, status=200, cache_control="public, max-age=600"):
        self.status, self.headers, self.data = status, {"cache-control": cache_control}, b"{}"


class _Http:
    def __init__(self, response):
        self.response, self.calls = response, 0

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.calls += 1
        return self.response


def _cached(response) -> tuple[auth._CachedRequest, _Http]:
    request = auth._CachedRequest()
    request._http = _Http(response)
    return request, request._http


def test_certificates_are_fetched_once_until_they_expire(monkeypatch):
    request, http = _cached(_Response())
    for _ in range(5):
        assert request(CERTS).status == 200
    assert http.calls == 1
    later = time.time() + 601  # past max-age
    monkeypatch.setattr(auth.time, "time", lambda: later)
    request(CERTS)
    assert http.calls == 2


def test_a_failed_certificate_fetch_is_not_kept():
    request, http = _cached(_Response(status=500))
    request(CERTS)
    request(CERTS)
    assert http.calls == 2


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    return c


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_saving_a_call_exchange_does_not_hold_up_the_call(client, monkeypatch):
    slow_put = store.put

    def put(collection, doc):
        time.sleep(0.05)  # a slow Firestore write
        return slow_put(collection, doc)

    async def extract(owner, heard, said, known):
        return Extraction(moments=[Moment(**STORY)])

    monkeypatch.setattr(store, "put", put)
    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)

    async def run() -> int:
        ticks = 0
        saving = asyncio.create_task(live._save_exchange(_WS(), {"id": OWNER, "name": "Vikram Rao"},
                                                         "My first job...", "How lovely.", main._expand))
        while not saving.done():  # stands in for the call's audio, which must keep flowing
            ticks += 1
            await asyncio.sleep(0.005)
        await saving
        return ticks

    assert asyncio.run(run()) >= 10  # with the writes on the event loop, it barely ticks at all


def test_two_exchanges_saved_at_once_make_one_card(client, monkeypatch):
    async def extract(owner, heard, said, known):
        return Extraction(moments=[Moment(**STORY)])

    monkeypatch.setattr(live.loops, "extract_from_exchange", extract)

    async def run() -> None:
        owner = {"id": OWNER, "name": "Vikram Rao"}
        # Both exchanges say the card's quote, so both make the same card at the same moment.
        await asyncio.gather(live._save_exchange(_WS(), owner, "My first job... I gave my whole first salary to my "
                                                 "mother.", "How lovely.", main._expand),
                             live._save_exchange(_WS(), owner, "At the mill... I gave my whole first salary to my "
                                                 "mother.", "Tell me more.", main._expand))

    asyncio.run(run())
    assert len([m for m in store.list("moments", owner_id=OWNER) if m.get("title") == STORY["title"]]) == 1
