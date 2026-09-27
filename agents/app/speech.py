"""Natural-sounding speech for the assistant's replies, using Gemini text-to-speech.

This is the assistant's own voice. It never imitates the owner or anyone else.

Each TTS model has its own quota, so when one is at its limit we move straight on
to the next instead of retrying (a spoken reply that arrives late is no use).
"""

from __future__ import annotations

import io
import logging
import wave
from functools import lru_cache

from google import genai
from google.genai import errors, types

from .config import settings

log = logging.getLogger(__name__)


class VoiceUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))


def _as_wav(data: bytes, mime_type: str) -> bytes:
    """Some TTS models return a WAV file, others raw 16-bit PCM; always hand back WAV."""
    if data[:4] == b"RIFF":
        return data
    rate = 24000
    for part in mime_type.split(";"):
        if part.strip().startswith("rate="):
            rate = int(part.split("=")[1])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return buffer.getvalue()


async def speak(text: str) -> bytes:
    last_error: errors.APIError | None = None
    for model in settings.tts_models:
        try:
            response = await _client().aio.models.generate_content(
                model=model,
                # Plain text only: these models read any style instruction aloud, and they
                # don't accept a system instruction. The warmth comes from the chosen voice.
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice)
                        )
                    ),
                ),
            )
            audio = response.candidates[0].content.parts[0].inline_data
            return _as_wav(audio.data, audio.mime_type or "")
        except errors.APIError as e:
            log.warning("TTS model %s unavailable (%s); trying the next one", model, e.code)
            last_error = e
    if last_error is not None and last_error.code == 429:
        raise VoiceUnavailable("The natural voice is unavailable right now: usage limit reached.")
    raise VoiceUnavailable("The natural voice is unavailable right now.")
