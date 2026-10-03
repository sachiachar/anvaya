"""Handover loop: the family/executor assistant, built with Google ADK.

It answers only from the owner's approved archive and financial inventory, cites
the source of every fact, and says plainly when the owner never recorded
something. It has no tool that can reach vault secrets.
"""

from __future__ import annotations

import re

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models import FallbackModel, Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from .config import settings
from .gemini import retry_for
from .governance import core as governance
from .store import store

APP_NAME = "life-after"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def search_archive(query: str, tool_context: ToolContext) -> dict:
    """Search the owner's approved moments (stories, advice, practical facts, wishes).

    Args:
        query: What to look for, in plain words.

    Returns:
        Up to 5 matching moments, each with an `id` to cite.
    """
    owner_id = tool_context.state["owner_id"]
    wanted = _words(query)
    scored = []
    for m in store.list("moments", owner_id=owner_id, status="approved"):
        text = " ".join([m["title"], m["summary"], m["quote"], *m.get("people", []), *m.get("places", [])])
        score = len(wanted & _words(text))
        if score:
            scored.append((score, m))
    scored.sort(key=lambda pair: -pair[0])
    return {
        "results": [
            {k: m.get(k) for k in ("id", "kind", "title", "summary", "quote", "year", "recording")}
            for _, m in scored[:5]
        ]
    }


def list_inventory(tool_context: ToolContext) -> dict:
    """List the owner's approved financial inventory: accounts, policies, investments, property.

    Returns:
        Every item with an `id` to cite. Numbers are masked to the last 4 characters.
    """
    owner_id = tool_context.state["owner_id"]
    return {"items": store.list("inventory", owner_id=owner_id, status="approved")}


AGENT = "handover.assistant"


def gateway_check(callback_context, llm_request: LlmRequest) -> LlmResponse | None:
    """Before every model call the agent makes: the gateway checks the registry and kill switch."""
    model = llm_request.model or settings.flash_model
    callback_context.state["temp:model"] = model
    governance.check_call(AGENT, model)
    chars = sum(len(p.text or "") for c in (getattr(llm_request, "contents", None) or []) for p in (c.parts or []))
    governance.check_input(AGENT, governance.estimate_tokens(chars))
    if llm_request.config is not None:
        llm_request.config.labels = governance.labels(AGENT) if governance.on_vertex() else None
        llm_request.config.max_output_tokens = governance.limit(AGENT, "max_output_tokens")
    return None


def gateway_record(callback_context, llm_response: LlmResponse) -> LlmResponse | None:
    """After every model call: record its tokens in the usage ledger."""
    usage = llm_response.usage_metadata
    model = callback_context.state.get("temp:model") or settings.flash_model
    t_in = (usage.prompt_token_count or 0) if usage else 0
    t_out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    governance.record_usage(AGENT, model, "direct", tokens_in=t_in, tokens_out=t_out,
                            error=bool(llm_response.error_code))
    return None


def _max_calls() -> int:
    entry = governance.agent_entry(AGENT) or {}
    return int(entry.get("limits", {}).get("max_model_calls_per_run", 6))


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="handover_assistant",
        # Retries on overload, then falls back to a second model.
        model=FallbackModel(models=[
            Gemini(model=settings.flash_model, retry_options=retry_for(AGENT)),
            Gemini(model=settings.fallback_model, retry_options=retry_for(AGENT, 1)),
        ]),
        description="Answers the family's questions from the owner's own words and records.",
        instruction=(
            "You help the family of {owner_name}, who has died, during the handover period. "
            "Rules you must always follow:\n"
            "1. Answer ONLY from tool results. Always call a tool before answering.\n"
            "2. Cite every fact with its source id in square brackets, e.g. [m_1a2b3c].\n"
            "3. When you quote {owner_name}, use their exact words from `quote`.\n"
            "4. If the tools return nothing relevant, say: '{owner_name} never recorded "
            "anything about that.' Never guess or fill gaps.\n"
            "5. You cannot see passwords or vault contents; if asked, explain that the vault "
            "is released separately to the named recipient.\n"
            "6. No legal or financial advice; suggest the legal partner for that.\n"
            "7. Be warm and brief. Reply in the language of the question."
        ),
        tools=[search_archive, list_inventory],
        before_model_callback=gateway_check,
        after_model_callback=gateway_record,
    )


_session_service = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=build_agent(), session_service=_session_service)


class UnknownSession(LookupError):
    """A session that isn't this person's, for this owner."""


async def ask(owner: dict, user_id: str, question: str, session_id: str | None = None) -> dict:
    """user_id names who is asking, within one demo copy; a session continues only for the same
    person and owner, so a session ID from another copy is refused (PR-1.6)."""
    if session_id is not None:
        session = await _session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        if session is None or session.state.get("owner_id") != owner["id"]:
            raise UnknownSession(session_id)
    else:
        session = await _session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            state={"owner_id": owner["id"], "owner_name": owner["name"]},
        )
        session_id = session.id
    answer = ""
    tools_used: list[str] = []
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=question)]),
        run_config=RunConfig(max_llm_calls=_max_calls()),
    ):
        for call in event.get_function_calls():
            tools_used.append(call.name)
        if event.is_final_response() and event.content and event.content.parts:
            answer = "".join(p.text or "" for p in event.content.parts)
    return {
        "answer": answer,
        "sources": sorted(set(re.findall(r"\[((?:m|inv)_[0-9a-f]{12})\]", answer))),
        "tools_used": tools_used,
        "session_id": session_id,
    }
