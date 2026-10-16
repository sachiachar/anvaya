"""Shared Gemini client and a helper for schema-typed JSON generation.

Resilience: every call retries with exponential backoff on overload and server
errors, and if the main model is still unavailable it falls back to the agent's own
fallback model from the registry. An agent with no fallback reports the model as busy.

Keys (PR-10.10, governance/keys.py): each call tries the key of the person who caused it first,
and the platform key only as failover. A key that's refused, out of quota or can't reach the
model moves the call straight on to the next key, before any fallback model.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from functools import lru_cache
from typing import Awaitable, Callable, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .governance import core as governance
from .governance import keys

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")
log = logging.getLogger(__name__)

TRANSIENT_CODES = [429, 500, 502, 503, 504]
# Default retries: 2 after the first attempt (3 attempts). Each agent's own max_retries
# from the registry overrides this per request (PR-10.2).
RETRY = types.HttpRetryOptions(
    attempts=3, initial_delay=2.0, max_delay=20.0, http_status_codes=TRANSIENT_CODES
)


class ModelBusy(RuntimeError):
    """The model stayed unavailable (overloaded or out of quota), and so did the agent's fallback."""


class CutOff(RuntimeError):
    """The answer stopped at the agent's output limit, so it can't be read. Thinking counts
    toward that limit, which is why each agent's policy can set its thinking level."""


THINKING_LEVELS = {"minimal", "low", "medium", "high"}


def retry_for(agent: str, attempts: int | None = None) -> types.HttpRetryOptions:
    """Retry options for one agent: its max_retries after the first attempt."""
    if attempts is None:
        attempts = 1 + int(governance.limit(agent, "max_retries", 2))
    return types.HttpRetryOptions(attempts=attempts, initial_delay=2.0, max_delay=20.0,
                                  http_status_codes=TRANSIENT_CODES)


@lru_cache(maxsize=1)
def client() -> genai.Client:
    # Reads GOOGLE_API_KEY from the environment (the Gemini API key path; not Vertex AI).
    return genai.Client(http_options=types.HttpOptions(retry_options=RETRY))


_own_clients: OrderedDict[str, genai.Client] = OrderedDict()
OWN_CLIENTS_MAX = 64


def make_client(kind: str, key: str) -> genai.Client:
    """A client for a person's own key. Vertex AI only for a Vertex express-mode key; the
    platform itself stays on the Gemini API key path."""
    return genai.Client(vertexai=kind == "vertex_express", api_key=key,
                        http_options=types.HttpOptions(retry_options=RETRY))


def client_for(kind: str | None, key: str | None) -> genai.Client:
    """The platform client, or a person's own (cached by a hash of the key, never the key)."""
    if key is None or kind is None:
        return client()
    name = keys.client_cache_key(kind, key)
    if name in _own_clients:
        _own_clients.move_to_end(name)
        return _own_clients[name]
    made = _own_clients[name] = make_client(kind, key)
    while len(_own_clients) > OWN_CLIENTS_MAX:
        _own_clients.popitem(last=False)
    return made


async def with_keys(agent: str, call: Callable[[genai.Client, bool], Awaitable[R]],
                    platform: Callable[[], genai.Client] | None = None) -> tuple[R, str]:
    """Run `call(client, last)` on each key in turn until one answers; returns (result, source).
    `last` is False while another key is left, so the caller can skip waiting out a 429 there.
    `platform` builds the platform's client when the caller has its own (speech: one try)."""
    links = keys.chain()
    for i, (source, kind, key) in enumerate(links):
        last = i == len(links) - 1
        if source == keys.PLATFORM and i > 0:
            governance.check_allowance_now(agent)  # their key failed: our key counts again
        c = (platform or client)() if key is None else client_for(kind, key)
        try:
            result = await call(c, last)
        except errors.APIError as e:
            if not last and keys.fails_over(e):
                keys.failed(agent, source, e.code)
                continue
            e.key_source = source  # so the ledger records the error against the key that failed
            raise
        keys.worked(source)
        return result, source
    raise AssertionError("the key chain always ends with the platform key")


def no_429(retry: types.HttpRetryOptions) -> types.HttpRetryOptions:
    """Retries without waiting out a 429: another key is left to try."""
    return retry.model_copy(update={"http_status_codes": [c for c in TRANSIENT_CODES if c != 429]})


async def generate_json(
    *,
    schema: type[T],
    prompt: str,
    media: list[tuple[bytes, str]] | None = None,
    system: str | None = None,
    agent: str,
    model: str | None = None,
) -> T:
    """Ask Gemini for output that validates against `schema`.

    `media` is a list of (bytes, mime_type) pairs, e.g. an audio recording or a
    photo of a document, sent alongside the text prompt.

    `agent` is the registered agent making the call (registry.yaml). It uses the agent's
    primary model, then its fallback, from the registry; `model` is only for tests. The
    governance layer refuses models the agent's policy doesn't allow and calls the kill switch
    pauses, and records every call's tokens in the ledger.
    """
    model = model or governance.primary_model(agent)
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
        # How much the model may think, from the registry. Thinking counts toward the output cap,
        # so an agent with long answers thinks less. Not set: the model's own default.
        thinking_config=types.ThinkingConfig(thinking_level=level.upper()) if (level := thinking_for(agent)) else None,
    )
    contents = [types.Content(role="user", parts=parts)]

    def on(model_name: str, cfg: types.GenerateContentConfig):
        async def call(c: genai.Client, last: bool):
            if not last:
                cfg_here = cfg.model_copy(update={"http_options": types.HttpOptions(
                    retry_options=no_429(cfg.http_options.retry_options))})
            else:
                cfg_here = cfg
            with governance.working(agent):  # the agents map shows it working while it runs
                return await c.aio.models.generate_content(model=model_name, contents=contents, config=cfg_here)
        return call

    used = model
    try:
        response, source = await with_keys(agent, on(model, config))
    except errors.APIError as e:
        # The fallback is the agent's own, from the registry; never a model the agent isn't allowed.
        fallbacks = [m for m in (governance.agent_entry(agent) or {}).get("models", {}).get("fallback", []) if m != model]
        if e.code not in TRANSIENT_CODES or not fallbacks:
            governance.record_usage(agent, model, "direct", error=True, key_source=getattr(e, "key_source", keys.PLATFORM))
            if e.code in TRANSIENT_CODES:
                raise ModelBusy(f"{model} is busy or out of quota ({e.code}); try again in a few minutes.") from e
            raise
        fallback = fallbacks[0]
        log.warning("Gemini %s unavailable (%s); falling back to %s", model, e.code, fallback)
        governance.check_call(agent, fallback)
        used = fallback
        # One attempt on the fallback model; its retries already ran on the primary.
        fallback_config = config.model_copy(update={"http_options": types.HttpOptions(retry_options=retry_for(agent, 1))})
        try:
            response, source = await with_keys(agent, on(fallback, fallback_config))
        except errors.APIError as e2:
            governance.record_usage(agent, fallback, "direct", error=True,
                                    key_source=getattr(e2, "key_source", keys.PLATFORM))
            if e2.code in (429, 503):  # both busy or out of quota: say so plainly
                raise ModelBusy(f"{model} and {fallback} are busy or out of quota ({e.code}, {e2.code}); "
                                "try again in a few minutes.") from e2
            raise
    usage = response.usage_metadata
    t_in = (usage.prompt_token_count or 0) if usage else 0
    t_out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    governance.record_usage(agent, used, "direct", tokens_in=t_in, tokens_out=t_out, key_source=source)

    if isinstance(response.parsed, schema):
        return response.parsed
    candidates = getattr(response, "candidates", None) or []
    if candidates and getattr(candidates[0], "finish_reason", None) == types.FinishReason.MAX_TOKENS:
        raise CutOff(f"The answer was cut off at {agent}'s output limit "
                     f"({governance.limit(agent, 'max_output_tokens')} tokens, thinking included).")
    return schema.model_validate_json(response.text or "{}")


def thinking_for(agent: str) -> str | None:
    """The agent's thinking level from its policy, if it sets one."""
    return governance.thinking(agent)
