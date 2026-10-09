"""A person's own model key (PR-10.10): test it, save it, remove it. See governance/keys.py.

Every route acts on the signed-in person's own key only, found from the verified sign-in;
no route takes a person's ID. A key is never sent back, not even to its owner: responses carry
its kind, last 4 characters, status and which features work.

Testing a key stores nothing. It makes one tiny call per model the person's key might be used
for, on that key alone (never on ours), through the gateway as the registered agent access.tester.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException
from google.genai import errors, types
from pydantic import BaseModel

from .gemini import make_client
from .governance import core as governance
from .governance import keys

AGENT = "access.tester"
# The agents behind "typing, photos and documents": their primary models are tested as one feature.
CHAT_AGENTS = ("saathi.chat", "capture.extractor", "saathi.summary", "documents.reader", "readiness.planner",
               "legal.needs", "activation.checker", "settlement.planner")
PROBE_SECONDS = 20  # each model's test call gives up after this
ONE_TRY = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
NO_TOOLS = types.AutomaticFunctionCallingConfig(disable=True)  # no tools are passed; silences an SDK warning


def features() -> list[tuple[str, str, list[str], str]]:
    """(id, label, models, need): what the app does with a key, and the models each needs.
    "all": every model must work; "any": one is enough (spoken replies try several)."""
    first = governance.primary_model
    return [
        ("chat", "Typing, photos and documents", sorted({first(a) for a in CHAT_AGENTS}), "all"),
        ("handover", "The family's questions", [first("handover.assistant")], "all"),
        ("voice", "Voice call", [first("saathi.voice")], "all"),
        ("speech", "Spoken replies", governance.models_in_order("saathi.speech"), "any"),
        ("fraud", "Fraud research (platform admins)", sorted({first("fraud.researcher"), first("fraud.dreamer")}), "all"),
    ]


def _result(e: Exception) -> str:
    if isinstance(e, governance.Refused):
        return "not_allowed"
    if not isinstance(e, errors.APIError):
        return "error"
    if keys.out_of_quota(e):
        return "out_of_quota"
    if keys.key_refused(e) or e.code in (401, 403):
        return "refused"
    if e.code == 404:
        return "not_available"
    return "error"


async def _try_model(client, model: str) -> tuple[str, int, int]:
    """One tiny call on one model: (result, tokens in, tokens out)."""
    governance.check_call(AGENT, model)
    with governance.working(AGENT):  # the agents map shows it working while it runs
        return await _one_call(client, model)


async def _one_call(client, model: str) -> tuple[str, int, int]:
    if governance.model_kind(model) == "live":
        async with client.aio.live.connect(model=model, config=types.LiveConnectConfig(response_modalities=["AUDIO"])):
            pass  # connected: the key reaches the live model; nothing is said
        return "works", 0, 0
    if governance.model_kind(model) == "speech":
        config = types.GenerateContentConfig(response_modalities=["AUDIO"], http_options=ONE_TRY,
                                             automatic_function_calling=NO_TOOLS)
        response = await client.aio.models.generate_content(model=model, contents="Hi", config=config)
    else:
        config = types.GenerateContentConfig(max_output_tokens=governance.limit(AGENT, "max_output_tokens", 16),
                                             http_options=ONE_TRY, automatic_function_calling=NO_TOOLS)
        response = await client.aio.models.generate_content(model=model, contents="Reply with the word OK.",
                                                            config=config)
    usage = response.usage_metadata
    return "works", (usage.prompt_token_count or 0) if usage else 0, (usage.candidates_token_count or 0) if usage else 0


async def test_key(kind: str, key: str) -> tuple[list[dict], bool]:
    """Test a key on every model the app uses; returns (features, usable). Stores nothing.
    The client is made for this test only, never cached."""
    client = make_client(kind, key)

    async def one(model: str) -> dict:
        started = time.monotonic()
        try:
            result, t_in, t_out = await asyncio.wait_for(_try_model(client, model), PROBE_SECONDS)
            governance.record_usage(AGENT, model, "direct", tokens_in=t_in, tokens_out=t_out,
                                    key_source=keys.SOURCES[kind])
        except TimeoutError:
            result = "error"
        except Exception as e:
            result = _result(e)
        return {"result": result, "ms": round((time.monotonic() - started) * 1000)}

    models = list(dict.fromkeys(m for _, _, ms, _ in features() for m in ms))  # each model once, in order
    with keys.held(key):
        by_model = dict(zip(models, await asyncio.gather(*(one(m) for m in models)), strict=True))
    out = []
    for fid, label, models, need in features():
        tried = [by_model[m] for m in models]
        working = [t for t in tried if t["result"] == "works"]
        ok = bool(working) if need == "any" else len(working) == len(tried)
        first_problem = next((t for t in tried if t["result"] != "works"), tried[0])
        shown = (working[0] if need == "any" else max(tried, key=lambda t: t["ms"])) if ok else first_problem
        out.append({"id": fid, "label": label, "result": "works" if ok else first_problem["result"], "ms": shown["ms"]})
    return out, any(f["result"] == "works" for f in out)


# ---- Routes: the signed-in person's own key only ------------------------------------------

router = APIRouter(prefix="/model-access")


class KeyIn(BaseModel):
    # Plain strings, checked in code: a length rule here would echo the key back in a 422.
    kind: str
    key: str


def _me() -> str:
    user = governance.current_user()
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def _checked(user: str, body: KeyIn) -> str:
    try:
        keys.check_allowed(user, body.kind)
        key = keys.clean_key(body.kind, body.key)
        keys.allow_test(user)
    except keys.NotAllowed as e:
        raise HTTPException(400, str(e)) from None
    return key


@router.get("")
def get_access() -> dict:
    user = _me()
    return {"policy": keys.policy(user), "access": keys.access(user), "ready": keys.ready(),
            "kinds": list(keys.KINDS)}


@router.post("/test")
async def test_access(body: KeyIn) -> dict:
    """Test a key without saving it."""
    key = _checked(_me(), body)
    found, usable = await test_key(body.kind, key)
    return {"features": found, "usable": usable}


@router.put("")
async def save_access(body: KeyIn) -> dict:
    """Test the key again, then save it encrypted. A key that reaches no model isn't saved."""
    user = _me()
    if not keys.ready():
        raise HTTPException(400, "Adding your own key isn't set up yet on this server.")
    key = _checked(user, body)
    found, usable = await test_key(body.kind, key)
    if not usable:
        raise HTTPException(422, "This key can't reach any of the models Anvaya uses, so it wasn't saved.")
    try:
        saved = keys.save(user, body.kind, key, found)
    except keys.NotAllowed as e:
        raise HTTPException(400, str(e)) from None
    return {"access": saved, "features": found}


@router.delete("")
def remove_access() -> dict:
    keys.remove(_me())
    return {"access": None}
