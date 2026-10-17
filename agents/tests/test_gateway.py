"""The agent gateway covers every model call (PR-10.1): structured calls, spoken replies,
the live call and the ADK handover agent all go through the registry, the policies and the ledger."""

import asyncio
import pathlib
import re
import time
from types import SimpleNamespace

import pytest

from app import executor_agent, speech
from app.governance import core
from app.governance.seed import clear_samples as seed
from app.store import store

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
MODEL_CALLS = re.compile(r"generate_content\(|live\.connect\(|genai\.Client\(")


@pytest.fixture(autouse=True)
def seeded():
    seed()
    yield
    store.update("platform_settings", "settings", {"kill_switch": "off"})


def test_every_file_that_calls_gemini_goes_through_the_gateway():
    for path in APP.rglob("*.py"):
        source = path.read_text()
        if MODEL_CALLS.search(source):
            assert "check_call(" in source, f"{path.name} calls Gemini without the gateway"
    agent_source = (APP / "executor_agent.py").read_text()
    assert "before_model_callback=gateway_check" in agent_source and "max_llm_calls" in agent_source


def test_every_agent_named_in_code_is_registered():
    registered = {a["id"] for a in core.registry()}
    named = set()
    for path in APP.rglob("*.py"):
        source = path.read_text()
        named |= set(re.findall(r'agent="([a-z_.]+)"', source))
        named |= set(re.findall(r'^AGENT = "([a-z_.]+)"', source, re.M))
    assert named and named <= registered, named - registered


def test_kill_switch_pauses_optional_features_first():
    store.update("platform_settings", "settings", {"kill_switch": "optional_paused"})
    with pytest.raises(core.Refused):
        core.check_call("saathi.voice", core.primary_model("saathi.voice"))
    core.check_call("saathi.chat", core.primary_model("saathi.chat"))  # typed conversation continues
    with pytest.raises(speech.VoiceUnavailable):
        asyncio.run(speech.speak("Hello"))
    assert any(b["agent"] == "saathi.speech" and b["kind"] == "kill_switch" for b in store.list("gateway_blocked"))


def test_spoken_reply_is_recorded_in_the_ledger(monkeypatch):
    audio = SimpleNamespace(data=b"RIFF....WAVE", mime_type="audio/wav")
    response = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=audio)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=250))

    class FakeModels:
        async def generate_content(self, **_):
            return response

    monkeypatch.setattr(speech, "_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    before = (store.get("usage_days", core._today()) or {"agents": {}})["agents"].get("saathi.speech", {}).get("calls", 0)
    assert asyncio.run(speech.speak("Hello")).startswith(b"RIFF")
    assert store.get("usage_days", core._today())["agents"]["saathi.speech"]["calls"] == before + 1


def test_handover_agent_calls_are_checked_and_recorded():
    ctx = SimpleNamespace(state={})
    with pytest.raises(core.Refused):
        executor_agent.gateway_check(ctx, SimpleNamespace(model="gemini-3.8-pro", config=None))
    executor_agent.gateway_check(ctx, SimpleNamespace(model=core.primary_model("saathi.chat"), config=None))
    before = (store.get("usage_days", core._today()) or {"agents": {}})["agents"].get("handover.assistant", {}).get("calls", 0)
    executor_agent.gateway_record(ctx, SimpleNamespace(error_code=None, usage_metadata=SimpleNamespace(
        prompt_token_count=900, candidates_token_count=80, thoughts_token_count=None)))
    row = store.get("usage_days", core._today())["agents"]["handover.assistant"]
    assert row["calls"] == before + 1 and row["tokens_in"] >= 900


def test_billing_labels_are_valid():
    assert core.labels("handover.assistant", "Demo-Bank") == {"agent": "handover_assistant", "tenant": "demo-bank",
                                                               "app": "anvaya"}


def test_prototype_release_uses_the_gemini_api_not_vertex(monkeypatch):
    """The prototype release runs on the Gemini API key path. Billing labels are a Vertex AI
    feature, so they must not be sent unless GOOGLE_GENAI_USE_VERTEXAI is switched on."""
    from app import gemini
    from app.schemas import DocumentCheck

    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    assert core.on_vertex() is False
    sent = {}
    parsed = DocumentCheck(document_type="x", is_expected_type=True, matches_expected_person=True, confidence=0.9)

    class FakeModels:
        async def generate_content(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(parsed=parsed, text=None, usage_metadata=None)

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    asyncio.run(gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt="p",
                                     agent="activation.checker"))
    assert sent["config"].labels is None


def test_handover_agent_tools_are_checked():
    """The agent may use only the tools its policy grants; anything else is refused and recorded."""
    assert executor_agent._runner.agent.before_tool_callback is executor_agent.tool_check
    for name in ("read_bundle_index", "read_concept", "list_accounts"):
        assert executor_agent.tool_check(SimpleNamespace(name=name), {}, None) is None
    # Refused and recorded; the model is told, so the family's question still gets an answer.
    assert "error" in executor_agent.tool_check(SimpleNamespace(name="web_search"), {}, None)
    assert any(b["agent"] == "handover.assistant" and b["kind"] == "tool" and b["attempted"] == "(unknown tool)"
               for b in store.list("gateway_blocked"))
    # A name the model made up never reaches the portal: it could carry what someone said.
    executor_agent.tool_check(SimpleNamespace(name="Vikram's PIN is 4821"), {}, None)
    assert not any("4821" in str(b) for b in store.list("gateway_blocked"))


def _fallback_run(monkeypatch) -> tuple[list, str]:
    """The handover agent's first model is overloaded, so ADK moves on to the fallback model."""
    from google import genai
    from google.adk.models import Gemini
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import errors, types

    from app import gemini

    primary, fallback = core.models_in_order("handover.assistant")[:2]
    monkeypatch.setattr(gemini, "client", lambda: genai.Client(api_key="AIzaSyTESTONLYpppppppppppppppppppppppppp"))

    async def fake_generate(self, llm_request, stream=False):
        if self.model == primary:
            raise errors.APIError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))

    monkeypatch.setattr(Gemini, "generate_content_async", fake_generate)
    model = executor_agent.build_agent().model
    request = LlmRequest(model=primary, config=types.GenerateContentConfig(),
                         contents=[types.Content(role="user", parts=[types.Part(text="Where are the papers?")])])

    async def run():
        return [r async for r in model.generate_content_async(request)]

    return asyncio.run(run()), fallback


def test_handover_fallback_model_is_checked_by_the_gateway(monkeypatch):
    """gateway_check sees only the first model; the fallback must pass the policy too."""
    original = core.policy

    def without_fallback(agent):
        rules = original(agent)
        if agent != "handover.assistant":
            return rules
        return {**rules, "models": {**rules["models"], "allow": [core.primary_model(agent)]}}

    monkeypatch.setattr(core, "policy", without_fallback)
    with pytest.raises(core.Refused):
        _fallback_run(monkeypatch)
    fallback = core.models_in_order("handover.assistant")[1]
    assert any(b["agent"] == "handover.assistant" and b["kind"] == "model" and b["attempted"] == fallback
               for b in store.list("gateway_blocked"))


def test_handover_fallback_model_is_recorded_under_its_own_name(monkeypatch):
    responses, fallback = _fallback_run(monkeypatch)
    assert responses[0].custom_metadata["model"] == fallback
    before = ((store.get("usage_days", core._today()) or {}).get("model_use") or {}).get(fallback, {}).get("calls", 0)
    ctx = SimpleNamespace(state={"temp:model": core.primary_model("handover.assistant")})
    executor_agent.gateway_record(ctx, SimpleNamespace(error_code=None, custom_metadata=responses[0].custom_metadata,
                                                       usage_metadata=None))
    assert store.get("usage_days", core._today())["model_use"][fallback]["calls"] == before + 1


def test_agents_map_counts_only_the_last_30_days_of_blocked_calls():
    from app.governance import portal

    def blocked() -> int:
        return {a["id"]: a for a in portal.agents_view()}["handover.assistant"]["usage_30d"]["blocked"]

    before = blocked()
    store.put("gateway_blocked", {"id": "blk_old", "at": time.time() - 40 * 86400, "day": "2000-01-01",
                                  "agent": "handover.assistant", "kind": "model", "attempted": "x", "reason": "x"})
    store.put("gateway_blocked", {"id": "blk_new", "at": time.time() - 86400, "day": "2000-01-01",
                                  "agent": "handover.assistant", "kind": "model", "attempted": "x", "reason": "x"})
    try:
        assert blocked() == before + 1
    finally:
        store.delete("gateway_blocked", "blk_old")
        store.delete("gateway_blocked", "blk_new")


def test_tokens_count_thinking_as_output_for_every_kind_of_call():
    from app import gemini

    call = SimpleNamespace(prompt_token_count=100, candidates_token_count=20, thoughts_token_count=30)
    live_reply = SimpleNamespace(prompt_token_count=100, response_token_count=20, thoughts_token_count=30)
    assert gemini.tokens(call) == gemini.tokens(live_reply) == (100, 50)
    assert gemini.tokens(SimpleNamespace(prompt_token_count=None, candidates_token_count=None)) == (0, 0)
    assert gemini.tokens(None) == (0, 0)


def test_a_spoken_reply_that_thinks_records_its_thinking(monkeypatch):
    audio = SimpleNamespace(data=b"RIFF....WAVE", mime_type="audio/wav")
    response = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=audio)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=250, thoughts_token_count=40))

    class FakeModels:
        async def generate_content(self, **_):
            return response

    monkeypatch.setattr(speech, "_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    before = (store.get("usage_days", core._today()) or {"agents": {}})["agents"].get("saathi.speech", {}).get("tokens_out", 0)
    asyncio.run(speech.speak("Hello"))
    assert store.get("usage_days", core._today())["agents"]["saathi.speech"]["tokens_out"] == before + 290


def test_only_gemini_py_reads_the_token_counts():
    """One place counts a call's tokens (gemini.tokens), so no call can leave thinking out."""
    fields = re.compile(r"(candidates|response|thoughts|prompt)_token_count")
    for path in APP.rglob("*.py"):
        if path.name != "gemini.py":
            assert not fields.search(path.read_text()), f"{path.name} counts tokens itself: use gemini.tokens"
