"""The agent registry and the agent policies are separate files (PR-10.11): registry.yaml says
what each agent is, policies.yaml what it may do. Both are checked at start-up, and the gateway
enforces the policy, denying by default."""

import asyncio
import copy
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml

from app import gemini, model_access
from app.governance import core, files, portal
from app.governance.seed import clear_samples as seed
from app.schemas import DocumentCheck
from app.store import store

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
PARSED = DocumentCheck(document_type="x", is_expected_type=True, matches_expected_person=True, confidence=0.9)


@pytest.fixture(autouse=True)
def seeded():
    seed()


def real() -> tuple[dict, dict]:
    return (copy.deepcopy(yaml.safe_load(core.REGISTRY_PATH.read_text())),
            copy.deepcopy(yaml.safe_load(core.POLICIES_PATH.read_text())))


def policy_of(pol: dict, agent: str) -> dict:
    return next(p for p in pol["policies"] if p["agent"] == agent)


def test_both_files_pass_their_checks():
    core.check_files()
    files.check(*real())


def test_every_agent_has_exactly_one_policy_and_every_policy_an_agent():
    agents = [a["id"] for a in core.registry()]
    governed = [p["agent"] for p in core.policies()]
    assert sorted(agents) == sorted(governed) and len(set(governed)) == len(governed)


def test_each_part_lives_in_one_file_only():
    reg, pol = real()
    for a in reg["agents"]:  # what it may do is never in the registry
        assert not {"autonomy", "tools", "limits", "budget_class", "thinking", "role"} & set(a), a["id"]
    for p in pol["policies"]:  # what it is (purpose, model order) is never in a policy
        assert not {"purpose", "status", "kind"} & set(p) and "primary" not in p["models"], p["agent"]


def broken(change) -> tuple[dict, dict]:
    reg, pol = real()
    change(reg, pol)
    return reg, pol


@pytest.mark.parametrize("change, problem", [
    (lambda r, p: p["policies"].pop(), "has no policy"),
    (lambda r, p: r["agents"].pop(), "isn't registered"),
    (lambda r, p: p["policies"].append(copy.deepcopy(p["policies"][0])), "listed twice"),
    (lambda r, p: policy_of(p, "saathi.chat")["models"]["allow"].remove("gemini-3.5-flash-lite"),
     "which its policy doesn't allow"),
    (lambda r, p: policy_of(p, "saathi.chat")["models"]["allow"].append("gemini-9-ultra"), "model catalogue"),
    (lambda r, p: policy_of(p, "fraud.dreamer")["tools"].append({"tool": "send_email", "scope": "write"}),
     "tool catalogue"),
    (lambda r, p: policy_of(p, "fraud.researcher")["tools"][0].update(scope="read"), "its effect is external"),
    (lambda r, p: policy_of(p, "saathi.chat")["consumption"]["per_user"].update(pool="judges"), "has no allowance"),
])
def test_a_broken_file_is_refused_with_the_reason(change, problem):
    with pytest.raises(ValueError, match=problem):
        files.check(*broken(change))


@pytest.mark.parametrize("change", [
    lambda r, p: r["agents"][0].update(autonomy="act_within_limits"),       # a permission in the registry
    lambda r, p: policy_of(p, "saathi.chat")["consumption"]["call"].update(max_input_tokns=9),  # a typo
    lambda r, p: policy_of(p, "saathi.chat")["role"].update(autonomy="decide"),
    lambda r, p: policy_of(p, "fraud.dreamer")["models"].update(thinking="lots"),
    lambda r, p: policy_of(p, "saathi.chat")["models"].update(allow=[]),
])
def test_an_unknown_field_or_value_is_refused(change):
    with pytest.raises(ValueError):
        files.check(*broken(change))


def test_no_app_code_takes_a_model_from_settings_or_passes_one():
    for path in APP.rglob("*.py"):
        source = path.read_text()
        assert not re.search(r"settings\.\w*_models?\b", source), f"{path.name} reads a model from settings"
        assert "model=settings" not in source, path.name
    config = (APP / "config.py").read_text()
    assert not re.search(r"GEMINI_\w*MODEL", config)


def test_the_gateway_follows_the_policy_not_the_registry(monkeypatch):
    core.check_call("saathi.chat", "gemini-3.5-flash-lite")
    with pytest.raises(core.Refused):  # in the catalogue, but not allowed for this agent
        core.check_call("saathi.chat", "gemini-3.8-flash")
    rules = copy.deepcopy(core.policies())
    monkeypatch.setattr(core, "policies", lambda: [p for p in rules if p["agent"] != "saathi.chat"])
    with pytest.raises(core.Refused):  # no policy, no call
        core.check_call("saathi.chat", "gemini-3.1-flash-lite")
    assert any(b["agent"] == "saathi.chat" and b["kind"] == "agent" for b in store.list("gateway_blocked"))


def test_an_unknown_agent_is_refused_and_recorded_not_a_crash():
    with pytest.raises(core.Refused):
        asyncio.run(gemini.generate_json(schema=DocumentCheck, prompt="p", agent="saathi.typo"))
    assert any(b["agent"] == "saathi.typo" and b["kind"] == "agent" for b in store.list("gateway_blocked"))


def test_an_agent_that_isnt_active_may_not_call_a_model():
    assert core.agent_entry("guardian.detectors")["status"] == "planned"
    with pytest.raises(core.Refused, match="not active"):
        core.check_call("guardian.detectors", "gemini-3.1-flash-lite")


def test_tools_come_from_the_policy():
    core.check_tool("fraud.researcher", "web_search")
    with pytest.raises(core.Refused):
        core.check_tool("fraud.dreamer", "web_search")


def test_limits_pool_and_thinking_come_from_the_policy():
    assert core.limit("handover.assistant", "max_output_tokens") == 1500           # per call
    assert core.limit("handover.assistant", "max_model_calls_per_run") == 6        # per run
    assert core.limit("saathi.chat", "max_model_calls_per_run", 99) == 99           # not set
    assert core._pool("handover.assistant") == "family" and core._pool("activation.checker") == "safety"
    assert core.thinking("fraud.dreamer") == "low" and core.thinking("saathi.chat") is None
    assert core.allowances()["owner"]["tokens_per_day"] == 300000


def test_a_call_uses_the_registrys_primary_then_its_fallback(monkeypatch):
    from google.genai import errors

    calls = []

    class FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs["model"])
            if len(calls) == 1:
                raise errors.APIError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
            return SimpleNamespace(parsed=PARSED, text=None, usage_metadata=None)

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    asyncio.run(gemini.generate_json(schema=DocumentCheck, prompt="p", agent="fraud.dreamer"))
    assert calls == core.models_in_order("fraud.dreamer")[:2] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_the_key_tester_tests_the_registrys_models_and_may_call_them():
    allowed = set(core.policy("access.tester")["models"]["allow"])
    for _feature, _label, models, _need in model_access.features():
        assert models and set(models) <= allowed, _feature
    tested = {m for f in model_access.features() for m in f[2]}
    assert "gemini-3.8-live" in tested and core.model_kind("gemini-3.8-live") == "live"


def test_the_portal_shows_what_each_agent_is_and_may_do():
    view = {a["id"]: a for a in portal.agents_view()}
    assert set(view) == {a["id"] for a in core.registry()}
    handover = view["handover.assistant"]
    assert handover["purpose"].startswith("Answers the family's questions")
    assert handover["autonomy"] == "act_within_limits" and handover["budget_class"] == "family"
    assert handover["tools"] == ["read_bundle_index", "read_concept", "list_accounts"]
    assert handover["limits"]["max_model_calls_per_run"] == 6
    assert handover["models"]["primary"] in handover["allowed_models"]
