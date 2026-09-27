"""Handover loop: the family/executor assistant, built with Google ADK.

It answers only from the owner's approved archive and financial inventory, cites
the source of every fact, and says plainly when the owner never recorded
something. It has no tool that can reach vault secrets.
"""

from __future__ import annotations

import re

from google.adk.agents import LlmAgent
from google.adk.models import FallbackModel, Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from .config import settings
from .gemini import RETRY
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


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="handover_assistant",
        # Retries on overload, then falls back to a second model.
        model=FallbackModel(models=[
            Gemini(model=settings.flash_model, retry_options=RETRY),
            Gemini(model=settings.fallback_model, retry_options=RETRY),
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
    )


_session_service = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=build_agent(), session_service=_session_service)


async def ask(owner: dict, user_id: str, question: str, session_id: str | None = None) -> dict:
    if session_id is None:
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
