// Platform admin portal API (/api/platform). Roles are checked on the server;
// the portal only hides buttons a role can't use.
import { json, request, type KeyFeature, type KeyKind, type ModelAccess } from "../api";

/** demo_admin: a visitor with their own demo copy, who decides that copy's requests only (PR-12.9). */
export type Role = "viewer" | "demo_admin" | "budget_approver" | "risk_reviewer" | "platform_admin";
export type Permission = "approve_budget" | "review_guardian" | "review_fraud" | "manage_demos" | "kill_switch" | "manage_privacy"
  | "manage_model_access";
export type KillSwitchState = "off" | "optional_paused" | "all_paused";

export type Me = { email: string | null; key: string; role: Role; permissions: Permission[] };

export type Use = { calls: number; tokens: number; minutes: number };

// Actual spend from the Cloud Billing export; never estimated.
export type Spend =
  | {
      available: true;
      source: string;
      through: string;
      fetched_at: number;
      currency: "USD";
      budget_usd: number;
      month_to_date_usd: number;
      forecast_usd: number;
      days_counted: number;
      daily: { day: string; cost_usd: number }[];
      by_service: Record<string, number>;
      top_skus: { service: string; sku: string; cost_usd: number }[];
    }
  | { available: false; reason: "not_configured" | "no_data" | "error"; message: string };

type CeilingUse = { used: number; limit: number; share: number };
/** Today's use of the daily ceiling for the whole service (PR-15.1). Empty measures: no ceiling set. */
export type ServiceCeiling = {
  state: "ok" | "optional_paused" | "paused";
  optional_pause_at: number | null;
  safety_reserve: number | null;
  tokens?: CeilingUse;
  voice_minutes?: CeilingUse;
};

export type Overview = {
  spend: Spend;
  usage: {
    daily: ({ day: string } & Use)[];
    by_agent: Record<string, Use>;
    by_model: Record<string, Use>;
    by_tenant: Record<string, Use & { active_users_today: number | null }>;
  };
  kill_switch: KillSwitchState;
  service_ceiling: ServiceCeiling;
  quota: { rpm_limit: number; rpm_peak: number; tpm_limit: number; tpm_peak: number } | null;
  pending_approvals: number;
  open_interventions: number;
  steps_not_pursued_30d: number;
  blocked_30d: number;
};

/** What an agent is (registry.yaml) beside what it may do (policies.yaml). */
export type Agent = {
  id: string;
  purpose: string;
  status: "active" | "planned";
  autonomy: "assist" | "propose" | "act_within_limits";
  models: { primary: string; fallback: string[] };
  allowed_models: string[];
  tools: string[];
  limits: Record<string, number>;
  budget_class: string;
  safety_critical: boolean;
  usage_30d: { calls: number; tokens_in: number; tokens_out: number; minutes: number;
    error_rate: number; blocked: number };
};

export type Blocked = { id: string; at: number; agent: string; kind: string; attempted: string; reason: string;
  user_key: string | null };

export type BudgetRequest = { id: string; at: number; tenant: string; user_key: string; kind: string; amount: number;
  hours: number; reason: string; status: "pending" | "approved" | "rejected"; decided_at?: number;
  decided_by?: string; decision_reason?: string; expires_at?: number };

export type GuardianEvent = { id: string; at: number; response: "add_check" | "reduce_autonomy" | "pause" | "block";
  agent: string; reason: string; status: "open" | "confirmed" | "reversed"; case_key: string | null;
  decided_by?: string; decision_reason?: string };

export type Source = { title: string; url: string };

/** Knowledge labels from docs/proposals/fraud-foresight.md §3. */
export type Label = "observed" | "derived" | "simulated" | "validated" | "invalidated";

export type Scenario = { id: string; at: number; scenario: string; safeguard: string; caught_by: string | null;
  coverage: "covered" | "partly" | "gap"; status: "verified" | "dreamt";
  /** pending: a source confirms it and it waits for a reviewer; unconfirmed: no source yet (it fades after 3 runs). */
  review: "pending" | "unconfirmed" | "approved" | "discarded" | "faded";
  evidence: number; label?: Label; variant?: "close" | "far"; steps?: string[]; claim?: string;
  sources?: Source[]; source_note?: string; coverage_reason?: string; run_id?: string;
  /** The common gap in kept knowledge it starts from: a pattern and how many owners have it (never who). */
  cue?: { pattern: string; asset_class: string; owners: number; means: string } | null;
  /** If approved, this becomes a warning sign the certificate check uses, so a reviewer sees both parts. */
  document_sign?: { look_for: string; genuine: string } | null };

export type Draft = { id: string; at: number; guide_key: string; kind: "tactic"; tactic: string;
  steps: { step: string; caught_by: string }[]; sign: { look_for: string; genuine: string } | null;
  claim: string; sources: Source[]; source_note: string; review: "pending" | "approved" | "discarded" };

export type RunStage = "imagine" | "check" | "map" | "save" | "research" | "skip";

/** One line of a run's live view. */
export type RunStep = { at: number; stage: RunStage; line: string; title?: string; confirmed?: boolean;
  coverage?: "covered" | "partly" | "gap" };

export type Run = { id: string; kind: "dream" | "research"; guide_key: string;
  status: "running" | "done" | "failed" | "stopped"; started_at: number; finished_at?: number;
  counts: Record<string, number>; error?: string | null; stage?: RunStage; steps?: RunStep[];
  /** Model calls used so far, per agent, and the caps from the registry. */
  calls?: Record<string, number>; limits?: Record<string, number | null>;
  stop_requested?: boolean; stopped_reason?: string | null };

/** How many owners must share a gap before it's a cue for dreaming: a platform admin's setting, never below the floor. */
export type CuePrivacy = { min_owners: number; floor: number; ceiling: number; changed_at: number | null; reason: string | null };

export type Fraud = { scenarios: Scenario[]; drafts: Draft[]; runs: Run[]; guides: Guide[]; privacy?: CuePrivacy;
  coverage: { covered: number; partly: number; gaps: number; faded: number } };

export type Guide = { id: string; title: string; version: number; trust_tier: string; sources: number;
  signals: number; researched_at: number | null; status: "current" | "research_requested" };

export type DemoCopy = { id: string; user_key: string; created_at: number; last_used_at: number;
  allowance_used: number; status: string; reset_at?: number | null; is_you: boolean;
  /** Only for platform admins: the visitor's optional nickname. */
  nickname?: string | null };

/** Model keys (PR-10.10): usage by whose key answered. "platform" is ours, as failover. */
export type KeySource = "platform" | "judge_studio" | "judge_vertex";
export type KeyUse = { calls: number; tokens: number; minutes: number; errors: number; failovers: number };
export type KeyPolicy = { enabled: boolean; kinds: KeyKind[]; set_by_admin?: boolean };
export type KeyPerson = { user_key: string; is_you: boolean; nickname?: string | null; policy: KeyPolicy;
  access: ModelAccess | null; usage_30d: Partial<Record<KeySource, KeyUse>> };
export type ModelKeys = { days: number; totals: Partial<Record<KeySource, KeyUse>>; people: KeyPerson[];
  default: KeyPolicy; platform_key: { failed_at?: number; code?: number }; ready: boolean; kinds: KeyKind[];
  can_manage: boolean };

export type AuditEntry = { id: string; at: number; actor: string; role: string; action: string; target: string;
  reason: string | null };

const p = (path: string) => `/platform${path}`;

export const platform = {
  me: () => request<Me>(p("/me")),
  overview: (fresh = false) => request<Overview>(p(fresh ? "/overview?fresh=true" : "/overview")),
  agents: () => request<{ agents: Agent[]; blocked: Blocked[] }>(p("/agents")),
  approvals: () => request<{ pending: BudgetRequest[]; active: BudgetRequest[]; history: BudgetRequest[] }>(p("/approvals")),
  decideApproval: (id: string, decision: "approve" | "reject", reason: string, hours?: number) =>
    request<BudgetRequest>(p(`/approvals/${id}/decision`), json({ decision, reason, hours })),
  guardian: () => request<{ events: GuardianEvent[]; by_response: Record<string, number>; false_alarm_rate: number | null }>(p("/guardian")),
  decideGuardian: (id: string, decision: "confirm" | "reverse", reason: string) =>
    request<GuardianEvent>(p(`/guardian/${id}/decision`), json({ decision, reason })),
  fraud: () => request<Fraud>(p("/fraud")),
  setCueMinOwners: (min_owners: number, reason: string) =>
    request<CuePrivacy>(p("/privacy/cue-min-owners"), json({ min_owners, reason })),
  decideScenario: (id: string, decision: "approve" | "discard", reason: string) =>
    request<Scenario>(p(`/fraud/scenarios/${id}/decision`), json({ decision, reason })),
  decideDraft: (id: string, decision: "approve" | "discard", reason: string) =>
    request<Draft>(p(`/fraud/drafts/${id}/decision`), json({ decision, reason })),
  researchNow: (id: string) => request<{ status: string; message: string }>(p(`/fraud/guides/${id}/research`), { method: "POST" }),
  stopRun: (id: string) => request<{ message: string }>(p(`/fraud/runs/${id}/stop`), { method: "POST" }),
  dreamNow: (id: string) => request<{ status: string; message: string }>(p(`/fraud/guides/${id}/dream`), { method: "POST" }),
  demos: () => request<{ copies: DemoCopy[]; cleanup_after_days: number; nicknames_visible: boolean }>(p("/demos")),
  resetDemo: (id: string, reason: string) => request<DemoCopy>(p(`/demos/${id}/reset`), json({ reason })),
  setKillSwitch: (state: KillSwitchState, reason: string) =>
    request<{ kill_switch: KillSwitchState }>(p("/kill-switch"), json({ state, reason })),
  audit: () => request<{ entries: AuditEntry[] }>(p("/audit")),
  modelKeys: () => request<ModelKeys>(p("/model-keys")),
  setKeyDefault: (enabled: boolean, kinds: KeyKind[], reason: string) =>
    request<KeyPolicy>(p("/model-keys/default"), json({ enabled, kinds, reason })),
  setKeyPolicy: (userKey: string, enabled: boolean, kinds: KeyKind[], reason: string) =>
    request<KeyPolicy>(p(`/model-keys/${userKey}/policy`), json({ enabled, kinds, reason })),
  testKey: (userKey: string, reason: string) =>
    request<{ features: KeyFeature[]; usable: boolean }>(p(`/model-keys/${userKey}/test`), json({ reason })),
  removeKey: (userKey: string, reason: string) => request<{ access: null }>(p(`/model-keys/${userKey}/remove`), json({ reason })),
};

// ---- Formatting ----------------------------------------------------------------

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-US");

export const fmt = {
  usd: (v: number) => usd.format(v),
  num: (v: number) => num.format(Math.round(v)),
  pct: (v: number) => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`,
  when: (t: number) => new Date(t * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
  label: (s: string) => s.replace(/[_-]/g, " ").replace(/^./, (c) => c.toUpperCase()),
};
