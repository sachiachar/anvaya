"""The voice check (voice fingerprinting): the owner registers a voiceprint by reading a few
sentences, and the start of their speech can be compared with it. Only the numbers that describe a
voice are kept, never the audio, and only under the owner's ID. The speaker model is stubbed; all
audio here is synthetic tones."""

import asyncio
import json
import math
import struct

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, voiceprint
from app.store import store
from tests.people import PRIYA

OWNER = "owner_demo_vikram"
URL = f"/api/owners/{OWNER}/voiceprint"
MODEL = "https://speaker-model.test"


def tone(seconds: float, *, silence_before: float = 0.0, loud: float = 0.3) -> bytes:
    """Synthetic 16 kHz mono 16-bit PCM: silence, then a tone."""
    quiet = b"\x00\x00" * int(silence_before * voiceprint.RATE)
    count = int(seconds * voiceprint.RATE)
    return quiet + b"".join(struct.pack("<h", int(loud * 0x7FFF * math.sin(i / 8))) for i in range(count))


def voice(main_axis: int, wobble: float = 0.0) -> list[float]:
    """A made-up embedding: mostly along one axis, so two people are far apart and one person is close."""
    vector = [0.0] * voiceprint.DIM
    vector[main_axis] = 10.0
    vector[(main_axis + 1) % voiceprint.DIM] = wobble
    return vector


VIKRAM, OTHER = 0, 50


@pytest.fixture()
def model(monkeypatch):
    """The speaker model, stubbed: each call returns the next embedding in `model.next`."""
    calls: list[bytes] = []

    class Model:
        next: list[list[float]] = []

    async def embed(pcm: bytes) -> list[float]:
        if not voiceprint.set_up():
            raise voiceprint.NotSetUp("The voice check isn't set up on this server.")
        calls.append(pcm)
        return Model.next.pop(0)

    monkeypatch.setattr(voiceprint.settings, "voice_model_url", MODEL)
    monkeypatch.setattr(voiceprint, "embed", embed)
    Model.calls = calls
    return Model


@pytest.fixture()
def client():
    c = TestClient(main.app)
    assert c.post("/api/demo/seed").status_code == 200
    return c


def register(client, model, *vectors) -> list[dict]:
    model.next = [*vectors]
    return [client.post(f"{URL}/samples", content=tone(5)).json() for _ in vectors]


def test_off_until_three_samples(client, model):
    assert client.get(URL).json() == {"set_up": True, "on": False, "samples": 0, "needed": 3}
    first, second, third = register(client, model, voice(VIKRAM), voice(VIKRAM, 2), voice(VIKRAM, 1))
    assert first["consistency"] is None and first["kept"] and not first["on"]  # nothing to compare with yet
    assert second["consistency"] == "good" and third["consistency"] == "good"
    assert third["on"] and third["samples"] == 3


def test_the_voiceprint_never_leaves_the_server(client, model):
    """The screen sees whether the check is on, never the numbers."""
    *_, added = register(client, model, voice(VIKRAM), voice(VIKRAM), voice(VIKRAM))
    for body in (added, client.get(URL).json()):
        assert not any(isinstance(v, list) for v in body.values())


def test_only_numbers_are_kept_under_the_owner_id(client, model):
    register(client, model, voice(VIKRAM))
    doc = store.get("voiceprints", OWNER)
    assert set(doc) == {"id", "owner_id", "samples", "updated_at"}
    assert doc["id"] == OWNER and len(doc["samples"][0]["v"]) == voiceprint.DIM
    assert not any(isinstance(s, list) for s in doc["samples"])  # Firestore can't hold an array of arrays
    assert "Vikram" not in json.dumps(doc)


def test_a_different_voice_is_not_kept(client, model):
    register(client, model, voice(VIKRAM))
    other = register(client, model, voice(OTHER))[0]
    assert other["consistency"] == "different" and not other["kept"] and other["samples"] == 1


def test_registration_keeps_only_what_the_check_would_match(client, model):
    """A noisy sample the check would call a mismatch (0.30 here) isn't kept either."""
    register(client, model, voice(VIKRAM))
    noisy = voice(VIKRAM, 10 * math.sqrt(1 / 0.3**2 - 1))  # cosine 0.30 with the first
    assert round(voiceprint.cosine(noisy, voice(VIKRAM)), 2) == 0.30
    assert register(client, model, noisy)[0]["kept"] is False
    fair = voice(VIKRAM, 10 * math.sqrt(1 / 0.4**2 - 1))   # cosine 0.40: kept, but only "fair"
    added = register(client, model, fair)[0]
    assert added["kept"] and added["consistency"] == "fair" and added["samples"] == 2


def test_samples_must_hold_speech(client, model):
    model.next = [voice(VIKRAM)]
    assert client.post(f"{URL}/samples", content=tone(1)).status_code == 400
    assert client.post(f"{URL}/samples", content=b"\x00\x00" * voiceprint.RATE * 5).status_code == 400  # silence
    too_long = tone(0.1) * (voiceprint.MAX_UPLOAD_SECONDS * 10 + 10)
    assert client.post(f"{URL}/samples", content=too_long).status_code == 413
    chunked = iter([too_long[i:i + 64000] for i in range(0, len(too_long), 64000)])  # no Content-Length
    assert client.post(f"{URL}/samples", content=chunked).status_code == 413
    assert model.calls == []  # none of it reached the model


def test_only_the_owner(client, model):
    assert client.get(URL, headers=PRIYA).status_code == 403
    assert client.post(f"{URL}/samples", content=tone(5), headers=PRIYA).status_code == 403
    assert client.delete(URL, headers=PRIYA).status_code == 403
    assert client.get("/api/owners/someone_else/voiceprint").status_code == 404


def test_not_set_up(client, model, monkeypatch):
    register(client, model, voice(VIKRAM), voice(VIKRAM), voice(VIKRAM))
    monkeypatch.setattr(voiceprint.settings, "voice_model_url", "")
    assert client.get(URL).json() == {"set_up": False, "on": False, "samples": 3, "needed": 3}
    assert asyncio.run(voiceprint.check(OWNER, tone(5))) is None  # never "on" without a model
    r = client.post(f"{URL}/samples", content=tone(5))
    assert r.status_code == 503 and "isn't set up" in r.json()["detail"]


def test_model_down(client, model, monkeypatch):
    async def down(pcm):
        raise voiceprint.Unavailable("The voice check couldn't be reached. Try again in a moment.")

    monkeypatch.setattr(voiceprint, "embed", down)
    assert client.post(f"{URL}/samples", content=tone(5)).status_code == 502
    assert store.get("voiceprints", OWNER) is None


def test_turning_off_and_resetting_forget_it(client, model):
    register(client, model, voice(VIKRAM), voice(VIKRAM), voice(VIKRAM))
    assert client.delete(URL).json()["on"] is False
    assert store.get("voiceprints", OWNER) is None
    register(client, model, voice(VIKRAM))
    client.post("/api/demo/seed")
    assert store.get("voiceprints", OWNER) is None


def test_at_most_ten_samples(client, model):
    register(client, model, *[voice(VIKRAM, i / 10) for i in range(12)])
    assert client.get(URL).json()["samples"] == voiceprint.MAX_SAMPLES


def test_check(client, model, monkeypatch):
    assert asyncio.run(voiceprint.check(OWNER, tone(5))) is None  # not on yet
    register(client, model, voice(VIKRAM), voice(VIKRAM), voice(VIKRAM))
    model.next = [voice(VIKRAM, 3), voice(OTHER)]
    assert asyncio.run(voiceprint.check(OWNER, tone(5))) == "match"
    assert asyncio.run(voiceprint.check(OWNER, tone(5))) == "mismatch"
    assert asyncio.run(voiceprint.check(OWNER, tone(1))) is None  # too little speech: no answer

    async def down(pcm):
        raise voiceprint.Unavailable("down")

    monkeypatch.setattr(voiceprint, "embed", down)
    assert asyncio.run(voiceprint.check(OWNER, tone(5))) is None  # the model is down: no answer, nothing held


def test_the_clip_starts_where_speech_starts(model):
    clip = voiceprint.speech(tone(8, silence_before=2))
    assert len(clip) == voiceprint.CLIP_SECONDS * voiceprint.BYTES_PER_SECOND
    assert voiceprint._loudness(clip[:3200]) > voiceprint.SILENCE
    assert voiceprint.speech(b"\x00\x00" * voiceprint.RATE * 3) == b""


def test_the_model_call(monkeypatch):
    """The real client: a WAV body, five seconds at most, and an unusable answer is "unavailable"."""
    seen = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content
        return httpx.Response(200, json=seen.get("reply", {"embedding": voice(VIKRAM), "dim": 192}))

    real = httpx.AsyncClient
    monkeypatch.setattr(voiceprint.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    monkeypatch.setattr(voiceprint.settings, "voice_model_url", MODEL)
    assert asyncio.run(voiceprint.embed(tone(3))) == voice(VIKRAM)
    assert seen["url"] == f"{MODEL}/embed?max_seconds=5" and seen["body"][:4] == b"RIFF"
    for bad in ({"embedding": [1.0] * 5}, {"embedding": [float("nan")] * 192}, {"embedding": [0.0] * 192},
                {"error": "Not a WAV file"}, []):
        seen["reply"] = bad
        with pytest.raises(voiceprint.Unavailable):
            asyncio.run(voiceprint.embed(tone(3)))
