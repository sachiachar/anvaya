"""Shared Gemini client and a helper for schema-typed JSON generation.

Resilience: every call retries with exponential backoff on overload and server
errors, and if the main model is still unavailable it falls back to
GEMINI_FALLBACK_MODEL.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .config import settings

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)

TRANSIENT_CODES = [429, 500, 502, 503, 504]
RETRY = types.HttpRetryOptions(
    attempts=4, initial_delay=2.0, max_delay=20.0, http_status_codes=TRANSIENT_CODES
)


@lru_cache(maxsize=1)
def client() -> genai.Client:
    # Reads GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION
    # (or GOOGLE_API_KEY) from the environment.
    return genai.Client(http_options=types.HttpOptions(retry_options=RETRY))


async def generate_json(
    *,
    model: str,
    schema: type[T],
    prompt: str,
    media: list[tuple[bytes, str]] | None = None,
    system: str | None = None,
) -> T:
    """Ask Gemini for output that validates against `schema`.

    `media` is a list of (bytes, mime_type) pairs, e.g. an audio recording or a
    photo of a document, sent alongside the text prompt.
    """
    parts: list[types.Part] = [types.Part.from_bytes(data=d, mime_type=m) for d, m in media or []]
    parts.append(types.Part(text=prompt))
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
        # No tools are passed here; turning this off also silences an SDK warning.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [types.Content(role="user", parts=parts)]

    try:
        response = await client().aio.models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as e:
        if e.code not in TRANSIENT_CODES or model == settings.fallback_model:
            raise
        log.warning("Gemini %s unavailable (%s); falling back to %s", model, e.code, settings.fallback_model)
        response = await client().aio.models.generate_content(
            model=settings.fallback_model, contents=contents, config=config
        )

    if isinstance(response.parsed, schema):
        return response.parsed
    return schema.model_validate_json(response.text or "{}")
