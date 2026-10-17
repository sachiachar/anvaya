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
from .gemini import tokens, with_keys
from .governance import core as governance

log = logging.getLogger(__name__)


class VoiceUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))


ONE_TRY = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))


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


AGENT = "saathi.speech"


async def speak(text: str) -> bytes:
    """Speak one of Saathi's replies. Each model is checked by the gateway (registry and kill
    switch) before it's used, and every spoken reply is recorded in the usage ledger."""
    cap = governance.limit(AGENT, "max_characters", 1000)
    if len(text) > cap:
        governance.record_blocked(AGENT, "limit", "max_characters", f"Reply of {len(text)} characters is over {cap}")
        raise governance.Refused(f"Spoken replies are limited to {cap} characters.")
    last_error: errors.APIError | None = None
    refused: governance.Refused | None = None
    for model in governance.models_in_order(AGENT):
        try:
            governance.check_call(AGENT, model)
        except governance.Refused as e:
            refused = e
            continue
        try:
            config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice)
                    )
                ),
                http_options=ONE_TRY,  # a late spoken reply is no use: no retries, on any key
            )

            async def call(client, _last, model=model, config=config):
                # Plain text only: these models read any style instruction aloud, and they
                # don't accept a system instruction. The warmth comes from the chosen voice.
                with governance.working(AGENT):  # the agents map shows it working while it runs
                    return await client.aio.models.generate_content(model=model, contents=text, config=config)

            response, source = await with_keys(AGENT, call, platform=lambda: _client())
            audio = response.candidates[0].content.parts[0].inline_data
            t_in, t_out = tokens(response.usage_metadata)
            governance.record_usage(AGENT, model, "direct", tokens_in=t_in, tokens_out=t_out, key_source=source)
            return _as_wav(audio.data, audio.mime_type or "")
        except errors.APIError as e:
            log.warning("TTS model %s unavailable (%s); trying the next one", model, e.code)
            governance.record_usage(AGENT, model, "direct", error=True,
                                    key_source=getattr(e, "key_source", "platform"))
            last_error = e
    if last_error is None and refused is not None:
        raise VoiceUnavailable(str(refused))
    if last_error is not None and last_error.code == 429:
        raise VoiceUnavailable("The natural voice is unavailable right now: usage limit reached.")
    raise VoiceUnavailable("The natural voice is unavailable right now.")
