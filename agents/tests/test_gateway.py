"""The agent gateway covers every model call (PR-10.1): structured calls, spoken replies,
the live call and the ADK handover agent all go through the registry, the policies and the ledger."""

import asyncio
import pathlib
import re
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
