"""The voice check (voice fingerprinting): does the voice on a call sound like the owner's?

The owner turns it on by reading a few short sentences. Each recording goes to the speaker model
(ECAPA-TDNN, `VOICE_MODEL_URL`), which returns a speaker embedding: 192 numbers that describe a
voice, not what was said. We keep those numbers, never the audio. The owner's voiceprint is the
average of their samples, kept under the owner's ID only: the model never gets a name or an ID.

On a call, the first seconds of the owner's speech in an exchange are compared with the voiceprint
(cosine similarity, in our code). The answer is "match", "mismatch", or no answer (None) when the
check is off, the clip is too short, or the model can't be reached. No answer never holds anything
back. What a mismatch does is decided by the caller (live.py), not here.

Demo limits (docs/architecture.md): the model is an outside service without a secret, and it may
keep what it computes from the audio it receives. A voiceprint is biometric data; turning the check
off, or resetting the demo, deletes it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import sys
import time
import wave
from array import array

import httpx

from .config import settings
from .store import store

log = logging.getLogger(__name__)

COLLECTION = "voiceprints"   # one document per owner, its ID the owner's ID
RATE = 16000                 # 16 kHz mono 16-bit PCM, as the browser's recorder and the live call send it
BYTES_PER_SECOND = RATE * 2
DIM = 192                    # the model's embedding size
CLIP_SECONDS = 5             # how much speech is compared: a registration sample, or the start of an exchange
MIN_SECONDS = 2.0            # less speech than this says too little about a voice
MAX_UPLOAD_SECONDS = 20      # a registration sample longer than this is refused
SAMPLES_NEEDED = 3           # the check is on once there are this many samples
MAX_SAMPLES = 10             # the oldest sample drops out after this
# Cosine similarity of two embeddings. The model's own threshold is 0.25; a 5-second clip from a
# phone gets lower scores than studio audio, but a stricter line gives fewer false matches.
MATCH_THRESHOLD = 0.35
CONSISTENT = 0.5             # a new sample this close to the earlier ones is clearly the same voice
# A new sample under MATCH_THRESHOLD isn't kept (another voice, or a noisy room): registration keeps
# only what the check itself would call a match, so a poor sample can't pull the voiceprint away.
SILENCE = 0.02               # loudness (0 to 1) under which a 100 ms piece counts as silence
TIMEOUT_SECONDS = 15


class NotSetUp(RuntimeError):
    """No speaker model: the voice check is off on this server."""


class Unavailable(RuntimeError):
    """The speaker model couldn't be reached, or answered with something unusable."""


class TooShort(ValueError):
    """Not enough speech to describe a voice."""


def set_up() -> bool:
    return bool(settings.voice_model_url)


def wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buffer.getvalue()


def _loudness(piece: bytes) -> float:
    """Root-mean-square loudness of 16-bit PCM, 0 to 1."""
    samples = array("h", piece[:len(piece) - len(piece) % 2])
    if sys.byteorder == "big":
        samples.byteswap()  # the recording is little-endian
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples) / len(samples)) / 0x8000


def speech(pcm: bytes, seconds: float = CLIP_SECONDS) -> bytes:
    """The first `seconds` of the recording from where speech starts: silence before it is dropped."""
    pcm = pcm[:len(pcm) - len(pcm) % 2]
    step = BYTES_PER_SECOND // 10  # 100 ms
    start = next((i for i in range(0, len(pcm), step) if _loudness(pcm[i:i + step]) >= SILENCE), len(pcm))
    return pcm[start:start + int(seconds * BYTES_PER_SECOND)]


async def embed(pcm: bytes) -> list[float]:
    """The speaker model's embedding of a clip. Raises NotSetUp or Unavailable; never logs the audio."""
    if not set_up():
        raise NotSetUp("The voice check isn't set up on this server.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            r = await client.post(f"{settings.voice_model_url}/embed", params={"max_seconds": CLIP_SECONDS},
                                  content=wav(pcm), headers={"content-type": "audio/wav"})
        r.raise_for_status()
        vector = r.json().get("embedding")
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log.warning("voice model call failed: %s", type(e).__name__)
        raise Unavailable("The voice check couldn't be reached. Try again in a moment.") from None
    if (not isinstance(vector, list) or len(vector) != DIM
            or not all(isinstance(x, int | float) and math.isfinite(x) for x in vector)):
        log.warning("voice model answered without a usable embedding")
        raise Unavailable("The voice check couldn't be reached. Try again in a moment.")
    if not any(vector):  # describes no voice, and can't be compared
        log.warning("voice model answered with an empty embedding")
        raise Unavailable("The voice check couldn't be reached. Try again in a moment.")
    return [float(x) for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def _average(samples: list[list[float]]) -> list[float]:
    """The voiceprint: each sample scaled to length 1 first, so a loud sample doesn't count more."""
    units = [[x / n for x in s] for s in samples if (n := math.sqrt(sum(x * x for x in s)))]
    return [sum(column) / len(units) for column in zip(*units, strict=True)]


def _samples(owner_id: str) -> list[list[float]]:
    # Stored as {"v": [...]} per sample: Firestore can't hold an array of arrays.
    return [s["v"] for s in (store.get(COLLECTION, owner_id) or {}).get("samples", [])]


# One registration at a time per owner, so two uploads at once can't lose a sample (this instance).
_locks: dict[str, asyncio.Lock] = {}


def _status(count: int) -> dict:
    return {"set_up": set_up(), "on": set_up() and count >= SAMPLES_NEEDED, "samples": count, "needed": SAMPLES_NEEDED}


def status(owner_id: str) -> dict:
    """What the owner's screen shows: never the numbers themselves. Off while there's no model."""
    return _status(len(_samples(owner_id)))


async def add_sample(owner_id: str, pcm: bytes) -> dict:
    """Register one recording. Says how close it is to the earlier samples; one that sounds like a
    different voice isn't kept, so the owner can record it again."""
    clip = await asyncio.to_thread(speech, pcm)
    if len(clip) < MIN_SECONDS * BYTES_PER_SECOND:
        raise TooShort("That was too short to hear your voice. Read the whole sentence, then try again.")
    vector = await embed(clip)
    async with _locks.setdefault(owner_id, asyncio.Lock()):
        earlier = await asyncio.to_thread(_samples, owner_id)
        similarity = cosine(vector, _average(earlier)) if earlier else None
        kept = similarity is None or similarity >= MATCH_THRESHOLD
        samples = [*earlier, vector][-MAX_SAMPLES:] if kept else earlier
        if kept:
            await asyncio.to_thread(store.put, COLLECTION, {
                "id": owner_id, "owner_id": owner_id, "samples": [{"v": v} for v in samples], "updated_at": time.time()})
    consistency = None if similarity is None else "good" if similarity >= CONSISTENT else "fair" if kept else "different"
    return {**_status(len(samples)), "kept": kept, "consistency": consistency}


def forget(owner_id: str) -> dict:
    """Turn the check off: the voiceprint is deleted."""
    store.delete(COLLECTION, owner_id)
    return status(owner_id)


async def check(owner_id: str, pcm: bytes) -> str | None:
    """Does the start of this speech sound like the owner? "match", "mismatch", or None (no answer:
    the check is off, there's too little speech, or the model can't be reached)."""
    if not set_up():
        return None
    samples = await asyncio.to_thread(_samples, owner_id)
    if len(samples) < SAMPLES_NEEDED:
        return None
    clip = await asyncio.to_thread(speech, pcm)
    if len(clip) < MIN_SECONDS * BYTES_PER_SECOND:
        return None
    try:
        vector = await embed(clip)
    except (NotSetUp, Unavailable):
        return None
    return "match" if cosine(vector, _average(samples)) >= MATCH_THRESHOLD else "mismatch"
