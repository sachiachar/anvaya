"""Limits per request (PR-10.2): output caps, retries, input size, call length, reply length."""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from google.genai import errors

from app import executor_agent, gemini, live, speech
from app.governance import core, files
from app.governance.seed import clear_samples as seed
from app.schemas import DocumentCheck
from app.store import store

PARSED = DocumentCheck(document_type="x", is_expected_type=True, matches_expected_person=True, confidence=0.9)


@pytest.fixture(autouse=True)
def seeded():
    seed()


def fake_client(monkeypatch, calls, fail_first=False):
    class FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            if fail_first and len(calls) == 1:
                raise errors.APIError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
            return SimpleNamespace(parsed=PARSED, text=None, usage_metadata=None)

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))


def blocked(agent, what):
    return any(b["agent"] == agent and b["attempted"] == what for b in store.list("gateway_blocked"))


def test_output_cap_and_retries_come_from_the_registry(monkeypatch):
    calls = []
    fake_client(monkeypatch, calls)
    asyncio.run(gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt="p",
                                     agent="activation.checker"))
    config = calls[0]["config"]
    assert config.max_output_tokens == core.limit("activation.checker", "max_output_tokens")
    assert config.http_options.retry_options.attempts == 1 + core.limit("activation.checker", "max_retries")


def test_fallback_model_gets_a_single_attempt(monkeypatch):
    calls = []
    fake_client(monkeypatch, calls, fail_first=True)
    asyncio.run(gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt="p",
                                     agent="activation.checker"))
    assert calls[1]["model"] == core.models_in_order("activation.checker")[1]
    assert calls[1]["config"].http_options.retry_options.attempts == 1


def test_oversized_request_is_refused_before_any_call(monkeypatch):
    calls = []
    fake_client(monkeypatch, calls)
    huge = "x" * (core.limit("activation.checker", "max_input_tokens") * 4 + 400)
    with pytest.raises(core.Refused):
        asyncio.run(gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt=huge,
                                         agent="activation.checker"))
    assert calls == [] and blocked("activation.checker", "max_input_tokens")


def test_spoken_replies_are_limited_in_length():
    with pytest.raises(core.Refused):
        asyncio.run(speech.speak("a" * 1001))
    assert blocked("saathi.speech", "max_characters")


def test_handover_agent_requests_carry_the_output_cap_and_big_inputs_are_refused():
    ctx = SimpleNamespace(state={})
    config = SimpleNamespace(labels=None, max_output_tokens=None)
    executor_agent.gateway_check(ctx, SimpleNamespace(model=core.primary_model("saathi.chat"), config=config, contents=[]))
    assert config.max_output_tokens == core.limit("handover.assistant", "max_output_tokens")
    big = [SimpleNamespace(parts=[SimpleNamespace(text="x" * 300_000)])]
    with pytest.raises(core.Refused):
        executor_agent.gateway_check(ctx, SimpleNamespace(model=core.primary_model("saathi.chat"), config=config, contents=big))


def test_live_call_ends_at_its_time_limit(monkeypatch):
    sent = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, data): sent.append(data)
        async def send_bytes(self, data): pass
        async def receive(self):
            await asyncio.sleep(3600)
        async def close(self): pass

    class FakeSession:
        async def receive(self):
            await asyncio.sleep(3600)
            yield

    @contextlib.asynccontextmanager
    async def connect(**_):
        yield FakeSession()

    monkeypatch.setattr(live, "client_for",
                        lambda kind, key: SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect))))
    original = core.limit
    monkeypatch.setattr(core, "limit", lambda a, k, d=None: 0.001 if k == "max_call_minutes" else original(a, k, d))
    monkeypatch.setattr(live.loops, "live_instructions", lambda *a, **k: "")
    asyncio.run(live.call(FakeWS(), {"id": "o1", "name": "Vikram"}, lambda t: t))
    assert any(m.get("type") == "error" and "limit" in m["detail"] for m in sent)
    assert blocked("saathi.voice", "max_call_minutes")


def test_live_call_records_its_tokens_once_per_reply(monkeypatch):
    def usage(t_in, t_out, thoughts=None):
        return SimpleNamespace(server_content=None, usage_metadata=SimpleNamespace(
            prompt_token_count=t_in, response_token_count=t_out, thoughts_token_count=thoughts))

    interrupted = SimpleNamespace(usage_metadata=None, server_content=SimpleNamespace(
        input_transcription=None, output_transcription=None, interrupted=True, model_turn=None, turn_complete=False))
    # Turn one: a reply that reports twice (only the latest counts), is interrupted, and a second
    # reply that thinks. Turn two: one reply.
    turns = [[usage(80, 300), usage(100, 400), interrupted, usage(120, 200, thoughts=50)], [usage(150, 300)], []]

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, data): pass
        async def send_bytes(self, data): pass
        async def receive(self):
            await asyncio.sleep(3600)
        async def close(self): pass

    class FakeSession:
        async def receive(self):
            for message in turns.pop(0):
                yield message

    @contextlib.asynccontextmanager
    async def connect(**_):
        yield FakeSession()

    def voice():
        day = store.get("usage_days", core._today()) or {}
        return day.get("agents", {}).get("saathi.voice", {"tokens_in": 0, "tokens_out": 0, "calls": 0})

    monkeypatch.setattr(live, "client_for",
                        lambda kind, key: SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect))))
    monkeypatch.setattr(live.loops, "live_instructions", lambda *a, **k: "")
    before = voice()
    asyncio.run(live.call(FakeWS(), {"id": "o1", "name": "Vikram"}, lambda t: t))
    after = voice()
    assert after["calls"] - before["calls"] == 1
    assert after["tokens_in"] - before["tokens_in"] == 100 + 120 + 150
    assert after["tokens_out"] - before["tokens_out"] == 400 + 250 + 300


# ---- Thinking and cut-off answers (#129) -------------------------------------------------


def test_the_thinking_level_comes_from_the_policy(monkeypatch):
    calls = []
    fake_client(monkeypatch, calls)
    asyncio.run(gemini.generate_json(model=core.primary_model("fraud.researcher"), schema=DocumentCheck, prompt="p",
                                     agent="fraud.researcher"))
    asyncio.run(gemini.generate_json(model=core.primary_model("saathi.chat"), schema=DocumentCheck, prompt="p",
                                     agent="activation.checker"))
    assert calls[0]["config"].thinking_config.thinking_level == "LOW"
    assert calls[1]["config"].thinking_config is None  # not set: the model's own default


def test_every_thinking_level_in_the_policies_is_valid():
    levels = {p["agent"]: p["models"]["thinking"] for p in core.policies() if "thinking" in p["models"]}
    assert levels and set(levels.values()) <= set(files.THINKING_LEVELS)


def test_an_answer_cut_off_at_the_output_limit_says_so(monkeypatch):
    from google.genai import types

    class FakeModels:
        async def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=None, text='{"document_type": "death cert', usage_metadata=None,
                                   candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)])

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    with pytest.raises(gemini.CutOff, match="cut off at fraud.researcher's output limit"):
        asyncio.run(gemini.generate_json(model=core.primary_model("fraud.researcher"), schema=DocumentCheck, prompt="p",
                                         agent="fraud.researcher"))


def test_both_models_busy_is_said_plainly(monkeypatch):
    class FakeModels:
        async def generate_content(self, **kwargs):
            raise errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    with pytest.raises(gemini.ModelBusy, match="busy or out of quota"):
        asyncio.run(gemini.generate_json(model=core.primary_model("fraud.researcher"), schema=DocumentCheck, prompt="p",
                                         agent="fraud.researcher"))
    day = store.list("usage_days")[0]  # the fallback's failed call is in the ledger
    assert day["agents"]["fraud.researcher"]["errors"] == 1 and "gemini-3.5-flash-lite" in day["model_use"]


def test_saathi_speaks_first_introducing_itself_once_then_greeting(monkeypatch):
    texts = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, data): pass
        async def send_bytes(self, data): pass
        async def receive(self):
            await asyncio.sleep(3600)
        async def close(self): pass

    class FakeSession:
        async def send_realtime_input(self, **kw):
            texts.append(kw.get("text"))

        async def receive(self):
            await asyncio.sleep(3600)
            yield

    @contextlib.asynccontextmanager
    async def connect(**_):
        yield FakeSession()

    monkeypatch.setattr(live, "client_for",
                        lambda kind, key: SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect))))
    original = core.limit
    monkeypatch.setattr(core, "limit", lambda a, k, d=None: 0.001 if k == "max_call_minutes" else original(a, k, d))
    monkeypatch.setattr(live.loops, "live_instructions", lambda *a, **k: "")
    store.put("owners", {"id": "o_intro", "name": "Vikram"})
    asyncio.run(live.call(FakeWS(), store.get("owners", "o_intro"), lambda t: t))
    asyncio.run(live.call(FakeWS(), store.get("owners", "o_intro"), lambda t: t))
    assert texts == [live.INTRODUCE, live.GREET]
    assert "Keep" in live.INTRODUCE and "current_step" in live.GREET  # the plan still decides what comes next
    assert store.get("owners", "o_intro")["introduced_at"]
