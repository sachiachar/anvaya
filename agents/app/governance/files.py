"""The agent registry and agent policies files: strict schemas and the checks between them.

registry.yaml says what each agent is; policies.yaml says what it may do
(docs/proposals/agent-security.md §2). Both are read through these schemas, so a typo or an
unknown field fails at start-up instead of loosening a rule quietly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Autonomy = Literal["assist", "propose", "act_within_limits"]
Effect = Literal["read", "write", "external"]
THINKING_LEVELS = ("minimal", "low", "medium", "high")
# Pools with no per-person limit: certificate checks, the guardian and platform work always run.
UNLIMITED_POOLS = ("safety", "platform")


class Strict(BaseModel):
    # strict: a quoted number ("6") or a "yes" is refused, never converted.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---- registry.yaml ---------------------------------------------------------------------


class Model(Strict):
    id: str
    kind: Literal["text", "live", "speech"]


class Tool(Strict):
    id: str
    kind: Literal["function", "mcp"]
    effect: Effect
    data: str


class ModelOrder(Strict):
    primary: str
    fallback: list[str] = []


class Agent(Strict):
    id: str = Field(pattern=r"^[a-z_]+\.[a-z_]+$")
    purpose: str
    owner: str
    status: Literal["active", "planned"]
    kind: Literal["model_step", "adk_agent", "live", "speech"]
    models: ModelOrder
    safety_critical: bool
    optional: bool = False


class Registry(Strict):
    version: Literal[2]
    models: list[Model]
    tools: list[Tool]
    mcp_servers: list[dict]
    agents: list[Agent]


# ---- policies.yaml ---------------------------------------------------------------------


class Role(Strict):
    autonomy: Autonomy


class ModelPolicy(Strict):
    allow: list[str] = Field(min_length=1)
    thinking: Literal[THINKING_LEVELS] | None = None


class ToolGrant(Strict):
    tool: str
    scope: Effect


class CallLimits(Strict):
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_retries: int | None = None
    max_call_minutes: int | None = None
    max_characters: int | None = None


class RunLimits(Strict):
    max_model_calls_per_run: int | None = None
    max_new_patterns_per_run: int | None = None
    max_scenarios_per_sequence: int | None = None
    max_sequences_per_run: int | None = None
    max_subjects_per_run: int | None = None  # research subjects one fraud research run covers


class PerUser(Strict):
    pool: str


class Consumption(Strict):
    call: CallLimits = CallLimits()
    run: RunLimits = RunLimits()
    per_user: PerUser


class Policy(Strict):
    agent: str
    role: Role
    models: ModelPolicy
    tools: list[ToolGrant]
    consumption: Consumption


class Allowance(Strict):
    tokens_per_day: int | None = None
    voice_minutes_per_day: int | None = None
    spoken_replies_per_day: int | None = None


class ServiceCeiling(Strict):
    """One daily ceiling on everything our key spends, for all demo copies together (PR-15.1)."""
    tokens_per_day: int = Field(gt=0)
    voice_minutes_per_day: int = Field(gt=0)
    optional_pause_at: float = Field(gt=0, le=1)   # share where optional agents stop
    safety_reserve: float = Field(ge=0, lt=1)      # share kept for safety-critical agents
    # The most of the day's tokens the hourly fraud cycle may use; it skips the hour after that, so
    # scheduled runs never crowd out people (fraud_pipeline.cycle).
    scheduled_fraud_share: float = Field(default=0.2, gt=0, le=1)

    @model_validator(mode="after")
    def _optional_stop_first(self) -> ServiceCeiling:
        if self.optional_pause_at > 1 - self.safety_reserve:
            raise ValueError("optional_pause_at must leave the safety reserve: optional agents stop first")
        return self


class Policies(Strict):
    version: Literal[1]
    user_allowances: dict[str, Allowance]
    service_ceiling: ServiceCeiling | None = None
    policies: list[Policy]


def check(registry: dict, policies: dict) -> tuple[Registry, Policies]:
    """Parse both files and check them against each other. Raises ValueError on any problem."""
    reg, pol = Registry.model_validate(registry), Policies.model_validate(policies)
    problems = []

    def dupes(ids: list[str], what: str) -> None:
        problems.extend(f"{what} {i} is listed twice" for i in sorted({i for i in ids if ids.count(i) > 1}))

    dupes([m.id for m in reg.models], "model")
    dupes([t.id for t in reg.tools], "tool")
    dupes([a.id for a in reg.agents], "agent")
    dupes([p.agent for p in pol.policies], "policy for")

    models, tools = {m.id for m in reg.models}, {t.id: t for t in reg.tools}
    agents, policy = {a.id: a for a in reg.agents}, {p.agent: p for p in pol.policies}
    problems += [f"{a} has no policy" for a in agents if a not in policy]
    problems += [f"policy for {a}, which isn't registered" for a in policy if a not in agents]
    for a in reg.agents:
        for m in [a.models.primary, *a.models.fallback]:
            if m not in models:
                problems.append(f"{a.id} tries {m}, which isn't in the model catalogue")
            elif a.id in policy and m not in policy[a.id].models.allow:
                problems.append(f"{a.id} tries {m}, which its policy doesn't allow")
    for p in pol.policies:
        problems += [f"policy for {p.agent} allows {m}, which isn't in the model catalogue"
                     for m in p.models.allow if m not in models]
        for g in p.tools:
            if g.tool not in tools:
                problems.append(f"policy for {p.agent} grants {g.tool}, which isn't in the tool catalogue")
            elif g.scope != tools[g.tool].effect:
                problems.append(f"policy for {p.agent} grants {g.tool} as {g.scope}, but its effect is "
                                f"{tools[g.tool].effect}")
        pool = p.consumption.per_user.pool
        if pool not in pol.user_allowances and pool not in UNLIMITED_POOLS:
            problems.append(f"policy for {p.agent} uses pool {pool}, which has no allowance")
    if problems:
        raise ValueError("Agent registry or policies are wrong: " + "; ".join(problems))
    return reg, pol
