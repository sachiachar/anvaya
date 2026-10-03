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
from .governance import core as governance

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)

TRANSIENT_CODES = [429, 500, 502, 503, 504]
# Default retries: 2 after the first attempt (3 attempts). Each agent's own max_retries
# from the registry overrides this per request (PR-10.2).
RETRY = types.HttpRetryOptions(
    attempts=3, initial_delay=2.0, max_delay=20.0, http_status_codes=TRANSIENT_CODES
)


def retry_for(agent: str, attempts: int | None = None) -> types.HttpRetryOptions:
    """Retry options for one agent: its max_retries after the first attempt."""
    if attempts is None:
        attempts = 1 + int(governance.limit(agent, "max_retries", 2))
    return types.HttpRetryOptions(attempts=attempts, initial_delay=2.0, max_delay=20.0,
                                  http_status_codes=TRANSIENT_CODES)


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
    agent: str,
) -> T:
    """Ask Gemini for output that validates against `schema`.

    `media` is a list of (bytes, mime_type) pairs, e.g. an audio recording or a
    photo of a document, sent alongside the text prompt.

    `agent` is the registered agent making the call (registry.yaml). The governance
    layer refuses models the agent isn't allowed and calls the kill switch pauses, and
    records every call's tokens and cost in the ledger.
    """
    governance.check_call(agent, model)
    governance.check_input(agent, governance.estimate_tokens(len(prompt) + len(system or ""), len(media or [])))
    parts: list[types.Part] = [types.Part.from_bytes(data=d, mime_type=m) for d, m in media or []]
    parts.append(types.Part(text=prompt))
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
        # No tools are passed here; turning this off also silences an SDK warning.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        # Billing labels per agent; only Vertex AI accepts them (the Gemini API key path doesn't).
        labels=governance.labels(agent) if governance.on_vertex() else None,
        # Limits from the registry: the model stops at the agent's output cap, and retries stop
        # at its max_retries.
        max_output_tokens=governance.limit(agent, "max_output_tokens"),
        http_options=types.HttpOptions(retry_options=retry_for(agent)),
    )
    contents = [types.Content(role="user", parts=parts)]

    used = model
    try:
        response = await client().aio.models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as e:
        if e.code not in TRANSIENT_CODES or model == settings.fallback_model:
            governance.record_usage(agent, model, "direct", error=True)
            raise
        log.warning("Gemini %s unavailable (%s); falling back to %s", model, e.code, settings.fallback_model)
        governance.check_call(agent, settings.fallback_model)
        used = settings.fallback_model
        # One attempt on the fallback model; its retries already ran on the primary.
        fallback_config = config.model_copy(update={"http_options": types.HttpOptions(retry_options=retry_for(agent, 1))})
        response = await client().aio.models.generate_content(
            model=settings.fallback_model, contents=contents, config=fallback_config
        )
    usage = response.usage_metadata
    t_in = (usage.prompt_token_count or 0) if usage else 0
    t_out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    governance.record_usage(agent, used, "direct", tokens_in=t_in, tokens_out=t_out,
                            cost_usd=governance.cost_usd(used, t_in, t_out))

    if isinstance(response.parsed, schema):
        return response.parsed
    return schema.model_validate_json(response.text or "{}")
