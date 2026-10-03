"""Shared Gemini client and a helper for schema-typed JSON generation.

Resilience: every call retries with exponential backoff on overload and server
errors, and if the main model is still unavailable it falls back to
GEMINI_FALLBACK_MODEL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    governance.record_usage(agent, used, "direct", tokens_in=t_in, tokens_out=t_out)

    if isinstance(response.parsed, schema):
        return response.parsed
    return schema.model_validate_json(response.text or "{}")


@dataclass
class Grounded:
    """A plain-text answer from Google Search grounding, with which sources support which words."""
    text: str
    sources: list[tuple[str, str]] = field(default_factory=list)       # (title, url) per grounding chunk
    supports: list[tuple[str, list[int]]] = field(default_factory=list)  # (answer segment, source indexes)


async def grounded_text(*, model: str, prompt: str, system: str, agent: str) -> Grounded:
    """Ask Gemini to answer with Google Search, and return the grounding data.

    Plain text on purpose: with a JSON schema, Gemini 3 runs the searches but leaves the
    grounding supports empty, and the fraud pipeline needs them to tell whether a source
    really supports a claim. Only agents the registry allows `google_search` may call this.
    """
    governance.check_call(agent, model)
    governance.check_tool(agent, "google_search")
    governance.check_input(agent, governance.estimate_tokens(len(prompt) + len(system)))
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
        labels=governance.labels(agent) if governance.on_vertex() else None,
        max_output_tokens=governance.limit(agent, "max_output_tokens"),
        http_options=types.HttpOptions(retry_options=retry_for(agent)),
    )
    try:
        response = await client().aio.models.generate_content(model=model, contents=prompt, config=config)
    except errors.APIError:
        governance.record_usage(agent, model, "direct", error=True)
        raise
    usage = response.usage_metadata
    t_in = (usage.prompt_token_count or 0) if usage else 0
    t_out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    governance.record_usage(agent, model, "direct", tokens_in=t_in, tokens_out=t_out)

    meta = response.candidates[0].grounding_metadata if response.candidates else None
    chunks = (meta.grounding_chunks or []) if meta else []
    supports = (meta.grounding_supports or []) if meta else []
    return Grounded(
        text=response.text or "",
        sources=[((c.web.title or "") if c.web else "", (c.web.uri or "") if c.web else "") for c in chunks],
        supports=[((s.segment.text or "") if s.segment else "", list(s.grounding_chunk_indices or []))
                  for s in supports],
    )
