// Platform admin portal API (/api/platform). Roles are checked on the server;
// the portal only hides buttons a role can't use.
import { json, request } from "../api";

export type Role = "viewer" | "budget_approver" | "risk_reviewer" | "platform_admin";
export type Permission = "approve_budget" | "review_guardian" | "review_fraud" | "manage_demos" | "kill_switch";
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

export type Overview = {
  spend: Spend;
  usage: {
    daily: ({ day: string } & Use)[];
    by_agent: Record<string, Use>;
    by_model: Record<string, Use>;
    by_tenant: Record<string, Use & { active_users_today: number | null }>;
  };
  kill_switch: KillSwitchState;
  quota: { rpm_limit: number; rpm_peak: number; tpm_limit: number; tpm_peak: number } | null;
  pending_approvals: number;
  open_interventions: number;
  blocked_30d: number;
};

export type Agent = {
  id: string;
  role: string;
  autonomy: "assist" | "propose" | "act_within_limits";
  models: { primary: string; fallback: string[] };
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

export type Scenario = { id: string; at: number; scenario: string; safeguard: string; caught_by: string | null;
  coverage: "covered" | "gap"; status: "verified" | "dreamt"; review: "pending" | "approved" | "discarded";
  evidence: number };

export type Guide = { id: string; title: string; version: number; trust_tier: string; sources: number;
  signals: number; researched_at: number; status: "current" | "research_requested" };

export type DemoCopy = { id: string; user_key: string; created_at: number; last_used_at: number;
  allowance_used: number; status: string; reset_at?: number | null; is_you: boolean;
  /** Only for platform admins: the visitor's optional nickname. */
  nickname?: string | null };

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
  fraud: () => request<{ scenarios: Scenario[]; coverage: { covered: number; gaps: number }; guides: Guide[] }>(p("/fraud")),
  decideScenario: (id: string, decision: "approve" | "discard", reason: string) =>
    request<Scenario>(p(`/fraud/scenarios/${id}/decision`), json({ decision, reason })),
  researchNow: (id: string) => request<{ status: string; message: string }>(p(`/fraud/guides/${id}/research`), { method: "POST" }),
  demos: () => request<{ copies: DemoCopy[]; cleanup_after_days: number; nicknames_visible: boolean }>(p("/demos")),
  resetDemo: (id: string, reason: string) => request<DemoCopy>(p(`/demos/${id}/reset`), json({ reason })),
  setKillSwitch: (state: KillSwitchState, reason: string) =>
    request<{ kill_switch: KillSwitchState }>(p("/kill-switch"), json({ state, reason })),
  audit: () => request<{ entries: AuditEntry[] }>(p("/audit")),
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
