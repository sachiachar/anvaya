import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  fmt, platform, type Added, type Agent, type AuditEntry, type Blocked, type BudgetRequest, type DemoCopy, type GuardianEvent,
  type Chain, type ChainRow, type CuePrivacy, type Draft, type Guide, type KillSwitchState, type Me, type Permission, type Run, type RunStage, type Scenario,
  type CeilingState, type ServiceCeiling, type Source,
  type Spend, type Use, type KeyPerson, type KeyPolicy, type KeySource, type KeyUse,
} from "./api";
import type { KeyFeature, KeyKind } from "../api";
import { type FilterDef, ListView, useList } from "./listControls";
import { LoopMap } from "./loopMap";
import { POLL_MS, usePoll } from "./usePoll";

// The charts library loads only when the dashboard opens.
const UsageCharts = lazy(() => import("./usageCharts"));
const FraudGraphView = lazy(() => import("./fraudGraph"));

// ---- Shared pieces --------------------------------------------------------------

function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    load().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message ?? e)));
  }, [load]);
  useEffect(reload, [reload]);
  return { data, error, reload };
}

const can = (me: Me, p: Permission) => me.permissions.includes(p);

function Loading({ error }: { error: string | null }) {
  return error ? <p className="pa-error" role="alert">{error}</p> : <p className="pa-muted">Loading…</p>;
}

// The portal shows only real data. An empty section says what will fill it.
function Empty({ children }: { children: ReactNode }) {
  return <p className="pa-muted">{children}</p>;
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="pa-card">
      <div className="pa-card-head"><h2>{title}</h2>{aside}</div>
      {children}
    </section>
  );
}

function Status({ value, label }: { value: string; label?: string }) {
  return <span className={`pa-status pa-status-${value}`}>{label ?? fmt.label(value)}</span>;
}

/** A decision with a required reason (and optional hours). Every decision is logged. */
function DecisionForm({ actions, withHours, defaultHours, onDecide, reasonLabel = "Reason (required, logged)" }: {
  actions: { value: string; label: string; tone?: "danger" }[];
  withHours?: boolean;
  defaultHours?: number;
  reasonLabel?: string;
  onDecide: (value: string, reason: string, hours?: number) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [hours, setHours] = useState(defaultHours ?? 24);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ok = reason.trim().length >= 3;
  const act = (value: string) => {
    setBusy(true);
    setError(null);
    onDecide(value, reason.trim(), withHours ? hours : undefined)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };
  return (
    <div className="pa-decide">
      <label>
        <span>{reasonLabel}</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
      </label>
      {withHours && (
        <label className="pa-hours">
          <span>Hours</span>
          <input type="number" min={1} max={168} value={hours} onChange={(e) => setHours(Number(e.target.value))} />
        </label>
      )}
      {actions.map((a) => (
        <button key={a.value} disabled={!ok || busy} className={a.tone === "danger" ? "pa-danger" : ""} onClick={() => act(a.value)}>
          {a.label}
        </button>
      ))}
      {error && <p className="pa-error" role="alert">{error}</p>}
    </div>
  );
}

// ---- Overview -----------------------------------------------------------------

function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="pa-bar" role="img" aria-label={label}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

function DailyChart({ daily, budgetPerDay }: { daily: { day: string; cost_usd: number }[]; budgetPerDay: number }) {
  const max = Math.max(budgetPerDay * 1.2, ...daily.map((d) => d.cost_usd));
  const w = 720, h = 180, gap = 4;
  const bw = (w - gap * (daily.length - 1)) / daily.length;
  const y = (v: number) => h - (v / max) * h;
  const total = daily.reduce((s, d) => s + d.cost_usd, 0);
  return (
    <figure className="pa-chart">
      <svg viewBox={`0 0 ${w} ${h + 24}`} role="img"
        aria-label={`Daily spend from Google Cloud billing for the last ${daily.length} days, ${fmt.usd(total)} in total, against a daily budget of ${fmt.usd(budgetPerDay)}.`}>
        {daily.map((d, i) => (
          <rect key={d.day} x={i * (bw + gap)} y={y(d.cost_usd)} width={bw} height={h - y(d.cost_usd)}
            className={d.cost_usd > budgetPerDay ? "over" : "under"} rx={2}>
            <title>{`${d.day}: ${fmt.usd(d.cost_usd)}${d.cost_usd > budgetPerDay ? " (over the daily budget)" : ""}`}</title>
          </rect>
        ))}
        <line x1={0} x2={w} y1={y(budgetPerDay)} y2={y(budgetPerDay)} className="budget" />
        <text x={w} y={y(budgetPerDay) - 6} textAnchor="end" className="budget-label">Daily budget {fmt.usd(budgetPerDay)}</text>
        <text x={0} y={h + 20} className="axis">{daily[0]?.day}</text>
        <text x={w} y={h + 20} textAnchor="end" className="axis">{daily[daily.length - 1]?.day}</text>
      </svg>
      <figcaption className="pa-muted">Bars above the line are days over the daily budget (shown in a darker shade).</figcaption>
    </figure>
  );
}

const KILL_LABELS: Record<KillSwitchState, string> = {
  off: "Everything running",
  optional_paused: "Optional features paused (spoken replies, live voice)",
  all_paused: "All non-safety agents paused",
};

// What each measure's state stops: live voice is limited by voice minutes, everything else by tokens.
const CEILING_LABELS: Record<"tokens" | "voice_minutes", Record<CeilingState, string>> = {
  tokens: {
    ok: "Running",
    optional_paused: "Spoken replies and summaries paused for today",
    paused: "Everything but certificate checks and the guardian paused for today",
  },
  voice_minutes: { ok: "Running", optional_paused: "Live voice paused for today", paused: "Live voice paused for today" },
};

/** Today's use of the daily ceiling for the whole service (PR-15.1), counted on our key only. */
function CeilingSection({ ceiling }: { ceiling: ServiceCeiling }) {
  const rows = ([["tokens", "Tokens"], ["voice_minutes", "Voice minutes"]] as const)
    .flatMap(([k, label]) => { const use = ceiling[k]; return use ? [{ k, label, use }] : []; });
  if (!rows.length) return null;
  return (
    <Section title="Daily ceiling for the whole service" aside={<Status value={ceiling.state} />}>
      <p>
        Optional features stop at {fmt.pct(ceiling.optional_pause_at ?? 0)}; everything else stops when{" "}
        {fmt.pct(ceiling.safety_reserve ?? 0)} is left. Certificate checks and the guardian always keep running. Calls on
        a person's own key don't count. Resets at midnight UTC.
      </p>
      {rows.map(({ k, label, use }) => (
        <div key={k}>
          <p>{label}: {fmt.num(use.used)} of {fmt.num(use.limit)} today. {CEILING_LABELS[k][use.state]}.</p>
          <Bar value={use.used} max={use.limit} label={`${label}: ${fmt.pct(use.share)} of today's ceiling`} />
        </div>
      ))}
    </Section>
  );
}

function describeUse(u: Use) {
  return [`${fmt.num(u.calls)} calls`, u.tokens ? `${fmt.num(u.tokens)} tokens` : "", u.minutes ? `${fmt.num(u.minutes)} min` : ""]
    .filter(Boolean).join(", ");
}

/** Tokens, and live-call minutes when there are any: "12,400 · 3 min". */
function tokensAndMinutes(u: Use) {
  const minutes = u.minutes ? `${fmt.num(u.minutes)} min` : "";
  return u.tokens ? [fmt.num(u.tokens), minutes].filter(Boolean).join(" · ") : minutes || "0";
}

function SpendSection({ spend }: { spend: Spend }) {
  if (!spend.available) {
    return (
      <Section title="Actual spend, from Google Cloud billing">
        <p><strong>Billing data not available yet.</strong> {spend.message}</p>
        <p className="pa-muted">The portal never estimates costs. Usage below is counted by the agent gateway.</p>
      </Section>
    );
  }
  return (
    <>
      <Section title="Actual spend, from Google Cloud billing">
        <p className="pa-muted">Up to {spend.through}; Google's billing export runs about a day behind, so the latest day may still be filling up. Checked {fmt.when(spend.fetched_at)}.</p>
        <DailyChart daily={spend.daily} budgetPerDay={spend.budget_usd / 30} />
      </Section>
      <div className="pa-grid2">
        <Section title={`Spend by service, last ${spend.daily.length} days`}>
          <table>
            <thead><tr><th>Service</th><th className="num">Spend</th></tr></thead>
            <tbody>{Object.entries(spend.by_service).map(([s, c]) => <tr key={s}><td>{s}</td><td className="num">{fmt.usd(c)}</td></tr>)}</tbody>
          </table>
        </Section>
        <Section title="Largest items">
          <table>
            <thead><tr><th>Item</th><th className="num">Spend</th></tr></thead>
            <tbody>{spend.top_skus.map((k) => (
              <tr key={`${k.service}/${k.sku}`}><td>{k.sku}<div className="pa-muted">{k.service}</div></td><td className="num">{fmt.usd(k.cost_usd)}</td></tr>
            ))}</tbody>
          </table>
        </Section>
      </div>
    </>
  );
}

export function OverviewPage({ me, fresh }: { me: Me; fresh: boolean }) {
  // Only the load right after a Refresh click asks for fresh billing; later reloads use the cache.
  const freshOnce = useRef(fresh);
  const load = useCallback(() => {
    const f = freshOnce.current;
    freshOnce.current = false;
    return platform.overview(f);
  }, []);
  const { data: o, error, reload } = useLoad(load);
  const [switchTo, setSwitchTo] = useState<KillSwitchState>("off");
  if (!o) return <Loading error={error} />;
  const spend = o.spend;
  const used = spend.available ? spend.month_to_date_usd / spend.budget_usd : 0;
  const forecastPct = spend.available ? spend.forecast_usd / spend.budget_usd : 0;
  const u = o.usage;
  const agentMax = Math.max(1, ...Object.values(u.by_agent).map((a) => a.tokens));
  return (
    <>
      <div className="pa-kpis">
        {spend.available ? (
          <>
            <div className="pa-kpi">
              <span>Spend this month, up to {spend.through}</span>
              <strong>{fmt.usd(spend.month_to_date_usd)}</strong>
              <Bar value={spend.month_to_date_usd} max={spend.budget_usd} label={`${fmt.pct(used)} of the monthly budget`} />
              <em>{fmt.pct(used)} of {fmt.usd(spend.budget_usd)} budget</em>
            </div>
            <div className="pa-kpi">
              <span>Forecast for the month</span>
              <strong>{fmt.usd(spend.forecast_usd)}</strong>
              <em className={forecastPct > 1 ? "pa-warn" : ""}>{forecastPct > 1 ? `Over budget by ${fmt.pct(forecastPct - 1)}` : `${fmt.pct(forecastPct)} of budget`}</em>
            </div>
          </>
        ) : (
          <div className="pa-kpi">
            <span>Spend this month</span>
            <strong>Not available yet</strong>
            <em>Waiting for Google Cloud billing</em>
          </div>
        )}
        <a className="pa-kpi" href="#/approvals"><span>Budget requests waiting</span><strong>{o.pending_approvals}</strong><em>Review in Approvals</em></a>
        <a className="pa-kpi" href="#/guardian"><span>Open interventions</span><strong>{o.open_interventions}</strong><em>Review in Guardian</em></a>
        <a className="pa-kpi" href="#/agents"><span>Blocked calls, 30 days</span><strong>{o.blocked_30d}</strong><em>Not allowed by the registry or a limit</em></a>
        <div className="pa-kpi"><span>Next steps not pursued, 30 days</span><strong>{o.steps_not_pursued_30d}</strong><em>Could be part of a fraud; counts only</em></div>
      </div>

      <Section title="Kill switch" aside={<Status value={o.kill_switch} />}>
        <p>{KILL_LABELS[o.kill_switch]}. The agent gateway reads this before every model call; certificate checks and the guardian always keep running.</p>
        {can(me, "kill_switch") ? (
          <div className="pa-row">
            <label>
              <span className="pa-muted">Change to</span>
              <select value={switchTo} onChange={(e) => setSwitchTo(e.target.value as KillSwitchState)}>
                {(Object.keys(KILL_LABELS) as KillSwitchState[]).map((k) => <option key={k} value={k}>{KILL_LABELS[k]}</option>)}
              </select>
            </label>
            <DecisionForm actions={[{ value: "set", label: "Apply", tone: switchTo === "off" ? undefined : "danger" }]}
              onDecide={(_, reason) => platform.setKillSwitch(switchTo, reason).then(reload)} />
          </div>
        ) : <p className="pa-muted">Only a platform admin can change the kill switch.</p>}
      </Section>

      <CeilingSection ceiling={o.service_ceiling} />

      <Section title="Use over time">
        <Suspense fallback={<p className="pa-muted">Loading the charts…</p>}>
          <UsageCharts usage={u} ceiling={o.service_ceiling} />
        </Suspense>
      </Section>

      <SpendSection spend={spend} />

      <div className="pa-grid2">
        <Section title="Use by agent, 30 days">
          <ul className="pa-bars">
            {Object.entries(u.by_agent).map(([agent, a]) => (
              <li key={agent}>
                <span className="pa-mono">{agent}</span>
                <Bar value={a.tokens} max={agentMax} label={`${agent}: ${describeUse(a)}`} />
                <span>{tokensAndMinutes(a)}</span>
              </li>
            ))}
          </ul>
          <p className="pa-muted">Tokens counted by the agent gateway, and minutes for live calls. Dollars come only from Google Cloud billing, above.</p>
        </Section>
        <div>
          <Section title="Use by model, 30 days">
            <table>
              <thead><tr><th>Model</th><th className="num">Calls</th><th className="num">Tokens</th></tr></thead>
              <tbody>{Object.entries(u.by_model).map(([m, v]) => (
                <tr key={m}><td className="pa-mono">{m}</td><td className="num">{fmt.num(v.calls)}</td>
                  <td className="num">{tokensAndMinutes(v)}</td></tr>
              ))}</tbody>
            </table>
          </Section>
          <Section title="Tenants">
            <table>
              <thead><tr><th>Tenant</th><th className="num">Calls, 30 days</th><th className="num">Tokens, 30 days</th><th className="num">Active today</th></tr></thead>
              <tbody>{Object.entries(u.by_tenant).map(([t, v]) => (
                <tr key={t}><td>{fmt.label(t)}</td><td className="num">{fmt.num(v.calls)}</td><td className="num">{fmt.num(v.tokens)}</td>
                  <td className="num">{v.active_users_today ?? "fewer than 5"}</td></tr>
              ))}</tbody>
            </table>
          </Section>
          {o.quota && (
            <Section title="Gemini quota headroom (peak use, sample figures until read from Cloud Monitoring)">
              <p>Requests per minute: {fmt.num(o.quota.rpm_peak)} of {fmt.num(o.quota.rpm_limit)}</p>
              <Bar value={o.quota.rpm_peak} max={o.quota.rpm_limit} label="Requests per minute used at peak" />
              <p>Tokens per minute: {fmt.num(o.quota.tpm_peak)} of {fmt.num(o.quota.tpm_limit)}</p>
              <Bar value={o.quota.tpm_peak} max={o.quota.tpm_limit} label="Tokens per minute used at peak" />
            </Section>
          )}
        </div>
      </div>
    </>
  );
}

// ---- Agents ----------------------------------------------------------------------

export function AgentsPage() {
  const load = useCallback(() => platform.agents(), []);  // the registry table; blocked calls page on their own
  const { data, error } = useLoad(load);
  if (!data) return <Loading error={error} />;
  return (
    <>
      <p className="pa-lede">Each circle is a loop: an AI step that takes something in and gives something back. People stand at every gate where something becomes real. The map shows which agents are working now; select a loop for its numbers.</p>
      <LoopMap agents={data.agents} />
      <RecentSteps />
      <p className="pa-lede">Every agent is registered with what it is (<span className="pa-mono">registry.yaml</span>) and has one policy for what it may do: its autonomy level, and only its allowed models and tools (<span className="pa-mono">policies.yaml</span>). The gateway refuses anything else and records it below.</p>
      <Section title="Agent registry and use, 30 days">
        <div className="pa-scroll">
          <table className="pa-agents">
            <thead><tr><th>Agent and role</th><th>Autonomy and pool</th><th>Models</th><th>Tools</th>
              <th className="num">Calls</th><th className="num">Tokens</th><th className="num">Errors</th><th className="num">Blocked</th></tr></thead>
            <tbody>{data.agents.map((a: Agent) => (
              <tr key={a.id}>
                <td>
                  <span className="pa-mono">{a.id}</span>{a.safety_critical && <span className="pa-tag">Safety</span>}
                  {a.status !== "active" && <span className="pa-tag">{fmt.label(a.status)}</span>}
                  <div className="pa-sub">{a.purpose}</div>
                </td>
                <td>{fmt.label(a.autonomy)}<div className="pa-sub">{fmt.label(a.budget_class)} pool</div></td>
                <td className="pa-mono">
                  {a.models.primary}
                  {a.models.fallback.map((m) => <div key={m} className="pa-sub">{m}</div>)}
                  {a.allowed_models.filter((m) => m !== a.models.primary && !a.models.fallback.includes(m))
                    .map((m) => <div key={m} className="pa-sub">{m} (allowed, not tried)</div>)}
                </td>
                <td className="pa-mono">{a.tools.length ? a.tools.map((x) => <div key={x}>{x}</div>) : <span className="pa-muted">none</span>}</td>
                <td className="num">{fmt.num(a.usage_30d.calls)}</td>
                <td className="num">
                  {fmt.num(a.usage_30d.tokens_in + a.usage_30d.tokens_out)}
                  {a.usage_30d.minutes > 0 && <div className="pa-sub">{fmt.num(a.usage_30d.minutes)} min</div>}
                </td>
                <td className="num">{fmt.pct(a.usage_30d.error_rate)}</td>
                <td className="num">{a.usage_30d.blocked}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>
      <BlockedCalls />
    </>
  );
}

/** Calls the gateway refused, a page at a time. */
function BlockedCalls() {
  const list = useList<Blocked>("bl", platform.blockedCalls);
  return (
    <Section title="Blocked calls">
      <ListView list={list} search="Search agent, kind or reason" empty="No blocked calls. The gateway records each call it refuses here."
        filters={[{ key: "agent", label: "Agent", show: (v) => v }, { key: "kind", label: "Kind" }]}>
        {(items) => (
          <table>
            <thead><tr><th>When</th><th>Agent</th><th>Kind</th><th>Attempted</th><th>Why</th></tr></thead>
            <tbody>{items.map((b) => (
              <tr key={b.id}><td>{fmt.when(b.at)}</td><td className="pa-mono">{b.agent}</td><td>{fmt.label(b.kind)}</td>
                <td className="pa-mono">{b.attempted}</td><td>{b.reason}</td></tr>
            ))}</tbody>
          </table>
        )}
      </ListView>
    </Section>
  );
}

/** "Inside the agents" (PR-14.5): the last AI steps of your own demo copy, newest first, as they
 *  happen. Every role sees only its own copy; the table below has the service's totals. */
function RecentSteps() {
  const { data, error, refresh } = usePoll(platform.agentSteps);
  return (
    <Section title="Your demo's recent steps"
      aside={<button type="button" className="pa-link" onClick={refresh}>Refresh</button>}>
      <p className="pa-muted">Each AI step in your own demo copy over the last 24 hours, newest first. It updates every {POLL_MS / 1000} seconds.
        Talk to Saathi as Vikram, then look here. Ids and numbers only: what was said stays in your copy.</p>
      {data && error && <p className="pa-error" role="alert">Couldn't update just now: {error}</p>}
      {!data ? <Loading error={error} /> : data.steps.length === 0 ? (
        <Empty>No steps yet. Each AI step in your demo appears here.</Empty>
      ) : (
        <div className="pa-scroll">
          <table>
            <thead><tr><th>When</th><th>Agent</th><th>Autonomy</th><th>Model</th><th className="num">Tokens</th><th>Result</th></tr></thead>
            <tbody>{data.steps.map((s) => (
              <tr key={s.id}>
                <td>{fmt.when(s.at)}</td>
                <td><span className="pa-mono">{s.agent}</span>{s.purpose && <div className="pa-sub">{s.purpose}</div>}</td>
                <td>{fmt.label(s.autonomy)}</td>
                <td className="pa-mono">{s.model ?? <span className="pa-muted">none</span>}
                  {s.key === "yours" && <div className="pa-sub">your key</div>}</td>
                <td className="num">{fmt.num(s.tokens)}{s.minutes > 0 && <div className="pa-sub">{s.minutes < 10 ? s.minutes.toFixed(1) : fmt.num(s.minutes)} min</div>}</td>
                <td><Status value={s.result} />{s.reason && <div className="pa-sub">{s.reason}</div>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {data && <p className="pa-muted">Next steps Saathi didn't pursue in the last 30 days: {data.steps_not_pursued_30d}.
        The step guard drops a step that could be part of a fraud.</p>}
    </Section>
  );
}

// ---- Approvals -------------------------------------------------------------------

const APPROVAL_STATUS: [string, string][] = [["pending", "Waiting"], ["active", "Active grant"], ["approved", "Approved"], ["rejected", "Rejected"]];

export function ApprovalsPage({ me }: { me: Me }) {
  const [version, setVersion] = useState(0);
  const decided = () => setVersion((n) => n + 1);
  const pending = useList<BudgetRequest>("wait", platform.approvalRequests, { status: "pending" }, version);
  const all = useList<BudgetRequest>("req", platform.approvalRequests, {}, version);
  const row = (r: BudgetRequest) => (
    <>
      <td>{fmt.when(r.at)}</td><td>{fmt.label(r.tenant)}</td><td className="pa-mono">{r.user_key}</td>
      <td>{fmt.label(r.kind)}: +{r.amount}</td><td>{r.hours} h</td><td>{r.reason}</td>
    </>
  );
  const waiting = pending.data?.page.total;
  return (
    <>
      <p className="pa-lede">Extra allowance needs a person's approval. Grants are time-limited, and nobody can approve their own request.</p>
      {me.role === "demo_admin" && (
        <p className="pa-note">You're the platform admin of your own demo copy: you can approve or reject its requests. In the app, ask for more as Vikram, then decide here.</p>
      )}
      <Section title={waiting === undefined ? "Waiting for a decision" : `Waiting for a decision (${waiting})`}>
        <ListView list={pending} search="Search key, kind or tenant" empty="Nothing waiting." filters={[]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((r) => (
                <li key={r.id}>
                  <table><tbody><tr>{row(r)}</tr></tbody></table>
                  {can(me, "approve_budget") ? (
                    <DecisionForm withHours defaultHours={r.hours}
                      actions={[{ value: "approve", label: "Approve" }, { value: "reject", label: "Reject", tone: "danger" }]}
                      onDecide={(v, reason, hours) => platform.decideApproval(r.id, v as "approve" | "reject", reason, hours).then(decided)} />
                  ) : <p className="pa-muted">Your role can view requests but not decide them.</p>}
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </Section>
      <Section title="All requests">
        <ListView list={all} search="Search key, kind or tenant" empty="No requests yet."
          filters={[{ key: "status", label: "Status", options: APPROVAL_STATUS }]}>
          {(items) => (
            <table>
              <thead><tr><th>Requested</th><th>Tenant</th><th>User</th><th>What</th><th>For</th><th>Why</th><th>Decision</th><th>By</th><th>Reason</th><th>Expires</th></tr></thead>
              <tbody>{items.map((r) => (
                <tr key={r.id}>{row(r)}<td><Status value={r.status} /></td><td>{r.decided_by ?? "—"}</td><td>{r.decision_reason ?? "—"}</td>
                  <td>{r.expires_at ? fmt.when(r.expires_at) : "—"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </ListView>
      </Section>
    </>
  );
}

// ---- Guardian --------------------------------------------------------------------

/** Why a plan version was made, in a few words: its kind, then the ids and labels behind it. */
function causeWords(cause: Record<string, string>): string {
  const extras = Object.entries(cause).filter(([k]) => k !== "kind" && k !== "turn").map(([k, v]) => `${k} ${v}`);
  return `${fmt.label(cause.kind ?? "change")}${extras.length ? ` (${extras.join(", ")})` : ""}`;
}

/** One conversation's audit chain (PR-2.12.5): turn → guardian decision → card → Keep → plan → next step. */
function ChainView({ chain, onClose }: { chain: Chain; onClose: () => void }) {
  const t = chain.turn;
  return (
    <Section title="One conversation, step by step" aside={<button type="button" className="pa-link" onClick={onClose}>Close</button>}>
      <p className="pa-muted">Ids and decisions only: what was said stays in the owner's own copy.</p>
      <ol className="pa-chain">
        <li><strong>Owner's turn</strong> · {fmt.when(t.at)} · {t.channel}{t.user_key ? <> · <span className="pa-mono">{t.user_key}</span></> : null}
          <div className="pa-sub pa-mono">{t.id}</div></li>
        <li><strong>Guardian</strong> · {chain.statements.length} decision{chain.statements.length === 1 ? "" : "s"}{t.held_back ? `, ${t.held_back} held back` : ""}
          <ul>{chain.statements.map((s) => (
            <li key={s.id}><Status value={s.response === "continue" ? "ok" : s.response} /> {fmt.label(s.response)}
              {s.card ? ` · card ${s.card}` : " · what was said"}{s.detectors.length ? ` · ${s.detectors.join(", ")}` : ""}
              <div className="pa-sub pa-mono">{s.id}{s.event_id ? ` · event ${s.event_id}` : ""}</div></li>
          ))}</ul></li>
        <li><strong>Cards and Keep</strong>{chain.cards.length === 0 ? " · none made" : ""}
          <ul>{chain.cards.map((c) => (
            <li key={c.id}>{fmt.label(c.collection)} card · <Status value={c.status === "kept" ? "ok" : c.status === "waiting" ? "pending" : "failed"} /> {c.status}
              <div className="pa-sub pa-mono">{c.id}{c.statement ? ` · allowed by ${c.statement}` : ""}
                {c.keep ? ` · Keep ${c.keep.id}${c.keep.kept ? ` → ${c.keep.kept}` : ""}` : ""}</div></li>
          ))}</ul></li>
        <li><strong>Plan</strong>{chain.plans.length === 0 ? " · unchanged by this turn" : ""}
          <ul>{chain.plans.map((pl) => (
            <li key={pl.version}>Version {pl.version} · {fmt.when(pl.at)} · because of {causeWords(pl.cause)}
              <div className="pa-sub">Current step: <span className="pa-mono">{pl.current ?? "none"}</span>
                {pl.steps[0] ? ` · ${pl.steps[0].opens === "saathi" ? "Saathi opens" : "waits for the owner"} · ${pl.steps[0].tone}` : ""}</div></li>
          ))}</ul></li>
        <li><strong>Next step</strong> · {chain.reply ? <>Saathi carried out <span className="pa-mono">{chain.reply.step ?? "no step"}</span> · {fmt.when(chain.reply.at)}</> : "no reply yet"}</li>
      </ol>
    </Section>
  );
}

// The Guardian page in plain words: what it is, what's waiting, and one question for each event.

/** Who found it: the agent's job in a few words, never its id first. */
const FOUND_BY: Record<string, string> = {
  "saathi.chat": "Saathi, in typed chat", "saathi.voice": "Saathi, on a call", "saathi.speech": "Saathi's spoken replies",
  "capture.extractor": "Picking up what was said", "saathi.summary": "The call summary", "documents.reader": "Reading a document",
  "readiness.planner": "Next steps", "legal.needs": "The legal needs check", "activation.checker": "The certificate check",
  "handover.assistant": "The family's assistant", "settlement.planner": "The settlement plan",
  "guardian.detectors": "The guardian's own checks", "fraud.researcher": "Fraud research", "fraud.dreamer": "Fraud dreaming",
  "access.tester": "The key test", "person:owner": "Something the owner did",
};
const foundBy = (agent: string) => FOUND_BY[agent] ?? agent;

/** What the guardian did, as an action. */
const DID: Record<string, string> = {
  add_check: "Asked for a check", reduce_autonomy: "Asked a person first", deflect: "Steered to a safer route",
  pause: "Paused it", block: "Stopped it",
};
const did = (response: string) => DID[response] ?? fmt.label(response);

const OUTCOME: Record<GuardianEvent["status"], string> = { open: "Waiting", confirmed: "Guardian was right", reversed: "Flagged by mistake" };

const GUARDIAN_STATUS: [string, string][] = [
  ["open", "Waiting for a person"], ["decided", "Decided"], ["confirmed", "Guardian was right"], ["reversed", "Flagged by mistake"],
];

export function GuardianPage({ me }: { me: Me }) {
  const [version, setVersion] = useState(0);
  const events = useList<GuardianEvent>("ev", platform.guardianEvents, {}, version);
  const trails = useList<ChainRow>("tr", platform.guardianTrails);
  const [chain, setChain] = useState<Chain | null>(null);
  const [chainError, setChainError] = useState<string | null>(null);
  const follow = (get: Promise<Chain>) => {
    setChainError(null);
    get.then(setChain).catch((e) => setChainError(String(e.message ?? e)));
  };
  // The numbers cover all of the viewer's events, whatever the filters.
  const counts = events.data?.counts;
  const show = (status: string) => events.set("status", events.params.status === status ? "" : status);
  return (
    <>
      <p className="pa-lede">The guardian is Anvaya's safety check. It watches every step and steps in when something looks risky:
        it asks for a check, steers to a safer route, pauses or stops it. It never decides a case. A person looks at each time it stepped in and says whether it was right.</p>
      {counts && (
        <div className="pa-kpis">
          <button type="button" className="pa-kpi" aria-pressed={events.params.status === "open"} onClick={() => show("open")}>
            <span>Waiting for a person</span><strong className={counts.waiting ? "pa-warn" : ""}>{counts.waiting}</strong><em>Show them</em></button>
          <button type="button" className="pa-kpi" aria-pressed={events.params.status === "decided"} onClick={() => show("decided")}>
            <span>Decided</span><strong>{counts.decided}</strong><em>Show them</em></button>
          <button type="button" className="pa-kpi" aria-pressed={events.params.status === "reversed"} onClick={() => show("reversed")}>
            <span>Flagged by mistake</span><strong>{counts.mistake}</strong>
            <em>{counts.decided ? `${fmt.pct(counts.mistake / counts.decided)} of those decided` : "None decided yet"}</em></button>
        </div>
      )}
      {chainError && <p className="pa-error" role="alert">{chainError}</p>}
      {chain && <ChainView chain={chain} onClose={() => setChain(null)} />}
      <Section title="Each time the guardian stepped in">
        <ListView list={events} search="Search what happened"
          empty="Nothing yet. Each time the guardian steps in, it appears here for a person to look at."
          filters={[{ key: "status", label: "Show", options: GUARDIAN_STATUS },
            { key: "response", label: "What it did", show: did }, { key: "agent", label: "Found by", show: foundBy }]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((e) => (
                <li key={e.id}>
                  <div className="pa-row">
                    <Status value={e.status} label={OUTCOME[e.status]} />
                    <strong>{did(e.response)}</strong>
                    <span className="pa-muted">{fmt.when(e.at)} · found by {foundBy(e.agent)}{e.case_key ? ` · case ${e.case_key}` : ""}</span>
                  </div>
                  <p>{e.reason}</p>
                  {e.id.startsWith("gev_st_") && (
                    <button type="button" className="pa-link" onClick={() => follow(platform.eventChain(e.id))}>See this conversation step by step</button>
                  )}
                  {e.status === "open" ? (
                    can(me, "review_guardian") ? (
                      <>
                        <p><strong>Was the guardian right?</strong> <span className="pa-muted">{e.holds
                          ? "\"No\" lets the held change or release go ahead. The owner is told either way."
                          : `"No" marks it a false alarm. Your answer is recorded; it changes nothing for the owner.${
                            e.id.startsWith("gev_voicecheck_") ? " The cards from that exchange wait for the owner's \"Yes, this was me\"." : ""}`}</span></p>
                        <DecisionForm reasonLabel="Why? (required, logged)"
                          actions={[{ value: "confirm", label: "Yes, it was right" },
                            e.holds ? { value: "reverse", label: "No, release it", tone: "danger" } : { value: "reverse", label: "No, a false alarm" }]}
                          onDecide={(v, reason) => platform.decideGuardian(e.id, v as "confirm" | "reverse", reason).then(() => setVersion((n) => n + 1))} />
                      </>
                    ) : <p className="pa-muted">Waiting for a risk reviewer.</p>
                  ) : <p className="pa-muted">{e.status === "confirmed" ? "Right" : "A mistake"}, said {e.decided_by}{e.decision_reason ? `: ${e.decision_reason}` : ""}</p>}
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </Section>
      <details className="pa-card pa-fold">
        <summary><h2>Follow one conversation, step by step</h2>
          <span className="pa-muted">For checking: what the guardian decided at each turn, as ids and counts only.</span></summary>
        <ListView list={trails} search="Search key or turn id" empty="No conversation turns yet. Each turn the guardian decides on appears here."
          filters={[{ key: "held", label: "Held back", options: [["true", "Something held back"], ["false", "Nothing held back"]] }]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((c) => (
                <li key={c.turn} className="pa-row">
                  <span className="pa-muted">{fmt.when(c.at)}</span>
                  <span className="pa-mono">{c.user_key ?? "unknown"}</span>
                  <span>{c.decisions} decision{c.decisions === 1 ? "" : "s"}{c.held ? `, ${c.held} held back` : ""}</span>
                  <button type="button" className="pa-link" onClick={() => follow(platform.chain(c.turn))}>Follow</button>
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </details>
    </>
  );
}

// ---- Fraud -------------------------------------------------------------------------

const RUN_KIND = { dream: "Dreaming", research: "Research" } as const;

/** The stages of each kind of run, with the live count under each. */
const PIPELINE = {
  dream: [
    { key: "imagine", label: "Imagine", hint: "fraud.dreamer, no web access", count: (c: Record<string, number>) => c.imagined ?? 0, unit: "imagined" },
    { key: "check", label: "Check sources", hint: "fraud.researcher, Exa web search", count: (c: Record<string, number>) => c.confirmed ?? 0, unit: "confirmed" },
    { key: "map", label: "Map to safeguards", hint: "Which one catches it?", count: (c: Record<string, number>) => c.kept ?? 0, unit: "mapped" },
    { key: "save", label: "Review", hint: "A person decides", count: (c: Record<string, number>) => c.confirmed ?? 0, unit: "waiting" },
  ],
  research: [
    { key: "research", label: "Search", hint: "fraud.researcher, Exa web search", count: (c: Record<string, number>) => c.found ?? 0, unit: "new tactics" },
    { key: "check", label: "Check sources", hint: "A source must confirm it", count: (c: Record<string, number>) => c.confirmed ?? 0, unit: "confirmed" },
    { key: "skip", label: "Not kept", hint: "Known, or no source", count: (c: Record<string, number>) => c.unconfirmed ?? 0, unit: "dropped" },
    { key: "save", label: "Review", hint: "A person decides", count: (c: Record<string, number>) => c.confirmed ?? 0, unit: "waiting" },
  ],
} as const;

const STAGE_LABEL: Record<RunStage, string> = {
  imagine: "Imagine", check: "Check", map: "Map", save: "Saved", research: "Search", skip: "Skipped",
};

/** The live view: which stage the run is in, what it's doing, and what it has spent. */
function LiveRun({ run, reviewer, onStop }: { run: Run; reviewer: boolean; onStop: () => void }) {
  const queued = run.status === "queued";  // waiting for the hourly job, asked to run now
  const running = run.status === "running" || queued;
  const active = running ? (run.stage === "skip" && run.kind === "dream" ? "check" : run.stage) : null;
  const steps = [...(run.steps ?? [])].reverse();
  const calls = Object.entries(run.calls ?? {});
  return (
    <Section title={queued ? `Live: ${RUN_KIND[run.kind].toLowerCase()} starting` : running
      ? `Live: ${RUN_KIND[run.kind].toLowerCase()} in progress` : `Last run: ${RUN_KIND[run.kind].toLowerCase()}`}
      aside={running ? (reviewer && (
        <button className="pa-small" disabled={run.stop_requested} onClick={onStop}>
          {run.stop_requested ? "Stopping…" : "Stop"}
        </button>
      )) : <Status value={run.status} />}>
      <div className="pa-live">
        <ol className="pa-pipeline" aria-label="Pipeline stages">
          {PIPELINE[run.kind].map((st) => (
            <li key={st.key} className={`pa-stage${active === st.key ? " is-active" : ""}`} aria-current={active === st.key ? "step" : undefined}>
              <span>{st.label}</span>
              <strong>{st.count(run.counts)}</strong>
              <em>{st.unit} · {st.hint}</em>
            </li>
          ))}
        </ol>
        <p className="pa-meter">
          {calls.length === 0 ? <span>No model calls yet</span> : calls.map(([agent, used]) => (
            <span key={agent}>{agent}: <b>{used}</b> of {run.limits?.[`${agent}.max_model_calls_per_run`] ?? "?"} calls</span>
          ))}
          {(run.counts?.not_checked ?? 0) > 0 && <span>Couldn't check: <b>{run.counts.not_checked}</b> (see below)</span>}
          {run.stopped_reason && <span>Stopped: {run.stopped_reason}</span>}
          {run.error && <span>Failed: {run.error}</span>}
        </p>
        {steps.length === 0 ? <Empty>Starting…</Empty> : (
          <ul className="pa-feed" aria-live="polite">
            {steps.map((st) => (
              <li key={`${st.at}-${st.line}`}>
                <span className="pa-chip">{STAGE_LABEL[st.stage]}</span>
                <span className={st.coverage === "gap" ? "is-gap" : st.confirmed ? "is-confirmed" : undefined}>{st.line}</span>
                <time>{new Date(st.at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

/** Links to the sources a scenario or tactic cites. They open in a new tab, without a referrer. */
function Sources({ sources }: { sources?: Source[] }) {
  if (!sources?.length) return null;
  return (
    <p className="pa-muted">Sources: {sources.map((s, i) => (
      <span key={s.url}>{i > 0 && " · "}<a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || "source"}</a></span>
    ))}</p>
  );
}

/** Both parts of a warning sign, in full: on approval, both go into the certificate check's instructions. */
function SignPreview({ sign }: { sign: { look_for: string; genuine: string } }) {
  return (
    <div className="pa-note">
      <p><strong>Adds a warning sign to the certificate check</strong></p>
      <p>Look for: {sign.look_for}</p>
      <p>A genuine document: {sign.genuine}</p>
    </div>
  );
}

/** The minimum number of owners a common gap needs before dreaming may start from it. */
function CuePrivacySection({ privacy, canChange, onChange }: { privacy: CuePrivacy; canChange: boolean; onChange: () => void }) {
  const [value, setValue] = useState(privacy.min_owners);
  return (
    <Section title="Privacy for common gaps">
      <p>Dreaming may start from a gap only when at least <strong>{privacy.min_owners} owners</strong> share it; smaller counts are dropped, and real owners are counted in steps of that size, never one by one.</p>
      {privacy.changed_at && <p className="pa-muted">Last changed {fmt.when(privacy.changed_at)}: {privacy.reason}</p>}
      {canChange ? (
        <div className="pa-row">
          <label>
            <span className="pa-muted">Minimum owners ({privacy.floor} to {privacy.ceiling})</span>
            <input type="number" min={privacy.floor} max={privacy.ceiling} value={value}
              onChange={(e) => setValue(Number(e.target.value))} />
          </label>
          <DecisionForm actions={[{ value: "set", label: "Save" }]}
            onDecide={(_, reason) => platform.setCueMinOwners(value, reason).then(onChange)} />
        </div>
      ) : <p className="pa-muted">Only a platform admin can change this.</p>}
      <p className="pa-muted">It can't go below {privacy.floor}: fewer owners could point at one person.</p>
    </Section>
  );
}

export function FraudPage({ me }: { me: Me }) {
  const load = useCallback(() => platform.fraud(), []);
  const { data, error, reload: reloadSummary } = useLoad(load);
  const [note, setNote] = useState<string | null>(null);
  // The four lists page on their own; a new version loads the page each one shows again.
  const [version, setVersion] = useState(0);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => { reloadSummary(); setVersion((n) => n + 1); }, [reloadSummary]);
  const scenarios = useList<Scenario>("sc", platform.fraudScenarios, {}, version + tick);
  const added = useList<Added>("ad", platform.fraudAdded, {}, version);
  const drafts = useList<Draft>("dr", platform.fraudDrafts, {}, version);
  const runs = useList<Run>("run", platform.fraudRuns, {}, version + tick);
  const running = data?.runs.some((r) => r.status === "running" || r.status === "queued") ?? false;
  // Live mode: while a run is going, check every 2 seconds, so each step and each new
  // scenario appears as it happens. Only the run and its scenarios change while it runs.
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => { reloadSummary(); setTick((n) => n + 1); }, 2000);
    return () => window.clearInterval(t);
  }, [running, reloadSummary]);
  if (!data) return <Loading error={error} />;
  const guideTitle = (id: string) => data.guides.find((g) => g.id === id)?.title ?? id;
  const guideFilter: FilterDef = { key: "guide", label: "Guide", show: guideTitle };
  const start = (call: Promise<{ message: string }>) =>
    call.then((r) => setNote(r.message)).catch((e) => setNote(String(e.message ?? e))).finally(reload);
  const reviewer = can(me, "review_fraud");
  const live = data.runs[0];
  return (
    <>
      <p className="pa-lede">Every hour the research agent looks into the next subjects, and the dreaming agent imagines attacks on our safeguards from each new tactic. What two independent websites confirm is added to the guide on its own; anything with one source waits for a reviewer. The pipeline only ever adds: you can remove anything it added, and two removals in a week pause it for that guide.</p>
      <div className="pa-kpis">
        <div className="pa-kpi"><span>Scenarios covered</span><strong>{data.coverage.covered}</strong><em>A safeguard catches it</em></div>
        <div className="pa-kpi"><span>Partly covered</span><strong>{data.coverage.partly}</strong><em>Caught only sometimes</em></div>
        <div className="pa-kpi"><span>Gaps</span><strong className={data.coverage.gaps ? "pa-warn" : ""}>{data.coverage.gaps}</strong><em>No safeguard catches it yet</em></div>
        <div className="pa-kpi"><span>Faded</span><strong>{data.coverage.faded}</strong><em>No source after 3 runs</em></div>
      </div>
      {live && <LiveRun run={live} reviewer={reviewer} onStop={() => start(platform.stopRun(live.id))} />}
      {data.privacy && <CuePrivacySection privacy={data.privacy} canChange={can(me, "manage_privacy")} onChange={reload} />}
      <Section title="Fraud reference guides">
        {note && <p className="pa-note" role="status">{note}</p>}
        {data.guides.length === 0 ? <Empty>No guides yet.</Empty> : <table>
          <thead><tr><th>Guide</th><th className="num">Version</th><th>Trust</th><th className="num">Sources</th><th className="num">Signals</th><th>Researched</th><th></th></tr></thead>
          <tbody>{data.guides.map((g: Guide) => (
            <tr key={g.id}>
              <td>{g.title}</td><td className="num">{g.version}</td><td>{fmt.label(g.trust_tier)}</td>
              <td className="num">{g.sources}</td><td className="num">{g.signals}</td><td>{g.researched_at ? fmt.when(g.researched_at) : "Built in"}</td>
              <td>{running ? <Status value="running" /> : reviewer && (
                <span className="pa-row">
                  <button className="pa-small" onClick={() => start(platform.dreamNow(g.id))}>Dream now</button>
                  <button className="pa-small" onClick={() => start(platform.researchNow(g.id))}>Research now</button>
                </span>
              )}</td>
            </tr>
          ))}</tbody>
        </table>}
      </Section>
      <Section title="Knowledge graph">
        <Suspense fallback={<p className="pa-muted">Loading the graph…</p>}>
          <FraudGraphView />
        </Suspense>
      </Section>
      <Section title="Safeguard coverage map">
        <ListView list={scenarios} search="Search scenario, steps or safeguard" empty={'No scenarios yet. "Dream now" adds them.'}
          filters={[{ key: "coverage", label: "Coverage" },
            { key: "review", label: "Review", show: (v) => (v === "faded" ? "Faded (no source after 3 runs)" : fmt.label(v)) },
            { key: "safeguard", label: "Aims at", show: (v) => v }, guideFilter]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((s) => (
                <li key={s.id} className={live && s.run_id === live.id ? "is-new" : undefined}>
                  <div className="pa-row">
                    {live && s.run_id === live.id && <Status value="new" />}
                    <Status value={s.coverage} />
                    <Status value={s.review === "unconfirmed" ? "unconfirmed" : s.status} />
                    {s.variant === "far" && <Status value="far-fetched" />}
                    <span className="pa-muted">Aims at: {s.safeguard}</span>
                  </div>
                  <p><strong>{s.scenario}</strong></p>
                  {s.steps && s.steps.length > 0 && <ol className="pa-muted">{s.steps.map((x) => <li key={x}>{x}</li>)}</ol>}
                  <p className="pa-muted">{s.caught_by ? `Caught by: ${s.caught_by}` : "Not caught by any safeguard yet."}{s.coverage_reason ? ` ${s.coverage_reason}` : ""}</p>
                  {s.cue && <p className="pa-muted">Starts from a common gap: {s.cue.owners} owners have {s.cue.means} (counted, never read).</p>}
                  {s.document_sign && s.review === "pending" && <SignPreview sign={s.document_sign} />}
                  {s.source_note && <p className="pa-muted">What sources say: {s.source_note}</p>}
                  <Sources sources={s.sources} />
                  {s.review === "pending" || s.review === "unconfirmed" ? (
                    reviewer ? (
                      <DecisionForm
                        actions={s.review === "pending"
                          ? [{ value: "approve", label: "Approve scenario" }, { value: "discard", label: "Discard", tone: "danger" }]
                          : [{ value: "discard", label: "Discard", tone: "danger" }]}
                        onDecide={(v, reason) => platform.decideScenario(s.id, v as "approve" | "discard", reason).then(reload)} />
                    ) : <p className="pa-muted">{s.review === "pending" ? "Waiting for a risk reviewer." : "No source confirms it yet."}</p>
                  ) : <p className="pa-muted">Review: {fmt.label(s.review)}</p>}
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </Section>
      <Section title="Added on its own" aside={data.adding_paused?.length ? <Status value="paused" /> : undefined}>
        {(data.adding_paused ?? []).map((g) => (
          <div key={g} className="pa-row">
            <p className="pa-warn">Adding is paused for {g} after two removals in a week. Until it resumes, new findings wait for a reviewer.</p>
            {reviewer && <DecisionForm actions={[{ value: "resume", label: "Resume adding" }]}
              onDecide={(_, reason) => platform.resumeAdding(g, reason).then(reload)} />}
          </div>
        ))}
        <ListView list={added} search="Search name" empty="Nothing yet. Tactics and scenarios two independent websites confirm appear here, added to the guide."
          filters={[guideFilter, { key: "kind", label: "Kind" },
            { key: "spot_check", label: "Spot check", options: [["true", "Spot checks only"], ["false", "No spot check"]] }]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((a) => (
                <li key={a.id}>
                  <p><strong>{a.name}</strong> {a.spot_check && <Status value="spot_check" />}</p>
                  <p className="pa-muted">{fmt.label(a.kind)} · {guideTitle(a.guide_key)} · {a.sources} independent sources · added {fmt.when(a.at)}</p>
                  {reviewer && <DecisionForm actions={[
                    ...(a.spot_check ? [{ value: "keep", label: "Looks right" }] : []),
                    { value: "remove", label: "Remove from the guide", tone: "danger" as const }]}
                    onDecide={(v, reason) => (v === "keep" ? platform.keepAdded(a.id, reason) : platform.removeAdded(a.id, reason)).then(reload)} />}
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </Section>
      <Section title="New tactics and warning signs to review">
        <ListView list={drafts} search="Search tactic, steps or sign"
          empty="None waiting. Tactics only one website confirms, and the warning signs of tactics added on their own, appear here."
          filters={[guideFilter, { key: "kind", label: "Kind", show: (v) => (v === "sign" ? "Warning sign" : fmt.label(v)) }]}>
          {(items) => (
            <ul className="pa-list">
              {items.map((d) => (
                <li key={d.id}>
                  <p><strong>{d.kind === "sign" ? `Warning sign for “${d.tactic}”` : d.tactic}</strong></p>
                  {d.kind === "sign" && <p className="pa-muted">The tactic is already in the guide. Adding this sign puts it into every certificate check.</p>}
                  <ol className="pa-muted">{d.steps.map((x) => <li key={x.step}>{x.step} <em>(caught by: {x.caught_by})</em></li>)}</ol>
                  {d.sign && <SignPreview sign={d.sign} />}
                  {d.source_note && <p className="pa-muted">What sources say: {d.source_note}</p>}
                  <Sources sources={d.sources} />
                  {reviewer ? (
                    <DecisionForm actions={[{ value: "approve", label: d.kind === "sign" ? "Add the sign" : "Add to the guide" }, { value: "discard", label: "Discard", tone: "danger" }]}
                      onDecide={(v, reason) => platform.decideDraft(d.id, v as "approve" | "discard", reason).then(reload)} />
                  ) : <p className="pa-muted">Waiting for a risk reviewer.</p>}
                </li>
              ))}
            </ul>
          )}
        </ListView>
      </Section>
      <Section title="Runs">
        <ListView list={runs} search="Search kind, status or guide" empty="No runs yet."
          filters={[{ key: "kind", label: "Kind", show: (v) => RUN_KIND[v as Run["kind"]] ?? v },
            { key: "status", label: "Status" }, guideFilter]}>
          {(items) => (
            <table>
              <thead><tr><th>Run</th><th>Guide</th><th>Started</th><th>Status</th><th>What happened</th></tr></thead>
              <tbody>{items.map((r) => (
                <tr key={r.id}>
                  <td>{RUN_KIND[r.kind]}</td><td>{guideTitle(r.guide_key)}</td><td>{fmt.when(r.started_at)}</td><td><Status value={r.status} /></td>
                  <td>{r.error ? `Failed: ${r.error}` : r.stopped_reason ? `Stopped: ${r.stopped_reason}` : Object.entries(r.counts).map(([k, v]) => `${fmt.label(k)} ${v}`).join(" · ")}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </ListView>
      </Section>
    </>
  );
}

// ---- Demos ---------------------------------------------------------------------------

export function DemosPage({ me }: { me: Me }) {
  const [version, setVersion] = useState(0);
  const list = useList<DemoCopy, { cleanup_after_days: number; nicknames_visible: boolean }>("demo", platform.demoCopies, {}, version);
  const [resetting, setResetting] = useState<string | null>(null);
  const data = list.data;
  if (!data) return <Loading error={list.error} />;
  return (
    <>
      <p className="pa-lede">Everyone who signs in gets their own private copy of the demo, shown here without names.
        {data.nicknames_visible ? " A nickname appears when the visitor chose one." : ""} Unused copies are removed after {data.cleanup_after_days} days.</p>
      <Section title="Demo copies">
        <ListView list={list} search={data.nicknames_visible ? "Search key or nickname" : "Search key"}
          empty="Nobody has opened the demo yet." filters={[{ key: "status", label: "Status" }]}>
          {(items) => (
            <table>
              <thead><tr><th>Copy</th><th>Created</th><th>Last used</th><th className="num">Allowance used today</th><th></th></tr></thead>
              <tbody>{items.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.nickname ? <strong>{c.nickname}</strong> : <span className="pa-mono">{c.user_key}</span>}
                    {c.is_you && <span className="pa-tag">You</span>}
                    {c.nickname && <div className="pa-sub pa-mono">{c.user_key}</div>}
                  </td>
                  <td>{fmt.when(c.created_at)}</td><td>{fmt.when(c.last_used_at)}</td>
                  <td className="num">{fmt.pct(c.allowance_used)}</td>
                  <td>{can(me, "manage_demos") && (resetting === c.id ? (
                    <DecisionForm actions={[{ value: "reset", label: "Restore this copy to the start", tone: "danger" }]}
                      onDecide={(_, reason) => platform.resetDemo(c.id, reason).then(() => { setResetting(null); setVersion((n) => n + 1); })} />
                  ) : <button className="pa-small" onClick={() => setResetting(c.id)}>Reset…</button>)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </ListView>
      </Section>
    </>
  );
}

// ---- Model keys (PR-10.10) -----------------------------------------------------------------

const SOURCE_LABEL: Record<KeySource, string> = {
  judge_studio: "Their own key: AI Studio", judge_vertex: "Their own key: Vertex AI", platform: "Ours (failover)",
};
const KIND_LABEL: Record<KeyKind, string> = { studio: "AI Studio", vertex_express: "Vertex AI express" };

function sumUse(u: Partial<Record<KeySource, KeyUse>>, own: boolean) {
  return Object.entries(u).filter(([s]) => (s === "platform") !== own).reduce((n, [, v]) => n + (v?.calls ?? 0), 0);
}

function KindChoice({ kinds, value, onChange }: { kinds: KeyKind[]; value: KeyKind[]; onChange: (k: KeyKind[]) => void }) {
  return (
    <span className="pa-row">
      {kinds.map((k) => (
        <label key={k} className="pa-check">
          <input type="checkbox" checked={value.includes(k)}
            onChange={(e) => onChange(e.target.checked ? [...value, k] : value.filter((x) => x !== k))} /> {KIND_LABEL[k]}
        </label>
      ))}
    </span>
  );
}

function PolicyForm({ kinds, policy, onSave }: { kinds: KeyKind[]; policy: KeyPolicy;
  onSave: (enabled: boolean, kinds: KeyKind[], reason: string) => Promise<unknown> }) {
  const [enabled, setEnabled] = useState(policy.enabled);
  const [chosen, setChosen] = useState<KeyKind[]>(policy.kinds);
  return (
    <div className="pa-row">
      <label className="pa-check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Allowed</label>
      <KindChoice kinds={kinds} value={chosen} onChange={setChosen} />
      <DecisionForm actions={[{ value: "save", label: "Save" }]} onDecide={(_, reason) => onSave(enabled, chosen, reason)} />
    </div>
  );
}

function KeyFeatures({ features }: { features: KeyFeature[] }) {
  return <span>{features.map((f) => <span key={f.id} className="pa-sub">{f.label}: {fmt.label(f.result)}</span>)}</span>;
}

function KeyRow({ person, kinds, manage, onChange }: { person: KeyPerson; kinds: KeyKind[]; manage: boolean; onChange: () => void }) {
  const [open, setOpen] = useState<"policy" | "test" | "remove" | null>(null);
  const [tested, setTested] = useState<KeyFeature[] | null>(null);
  const a = person.access;
  return (
    <tr>
      <td>
        {person.nickname ? <strong>{person.nickname}</strong> : <span className="pa-mono">{person.user_key}</span>}
        {person.is_you && <span className="pa-tag">You</span>}
        {person.nickname && <div className="pa-sub pa-mono">{person.user_key}</div>}
      </td>
      <td>
        {a ? <>{KIND_LABEL[a.kind]} <span className="pa-mono">{a.last4}</span> <Status value={a.status} />
          {a.failed_at && <div className="pa-sub">Last failed {fmt.when(a.failed_at)} ({a.failure})</div>}
          {(tested ?? a.features)?.length > 0 && <KeyFeatures features={tested ?? a.features} />}</>
          : <span className="pa-muted">None: uses ours</span>}
      </td>
      <td>{person.policy.enabled ? person.policy.kinds.map((k) => KIND_LABEL[k]).join(", ") || "No kinds" : "Turned off"}
        {person.policy.set_by_admin && <div className="pa-sub">Set for this person</div>}</td>
      <td className="num">{fmt.num(sumUse(person.usage_30d, true))}</td>
      <td className="num">{fmt.num(sumUse(person.usage_30d, false))}</td>
      <td className="num">{fmt.num(Object.values(person.usage_30d).reduce((n, v) => n + (v?.failovers ?? 0), 0))}</td>
      <td>{manage && (open === null ? (
        <span className="pa-row">
          <button className="pa-small" onClick={() => setOpen("policy")}>Settings…</button>
          {a && <button className="pa-small" onClick={() => setOpen("test")}>Test…</button>}
          {a && <button className="pa-small" onClick={() => setOpen("remove")}>Remove…</button>}
        </span>
      ) : (
        <div>
          {open === "policy" && <PolicyForm kinds={kinds} policy={person.policy}
            onSave={(enabled, k, reason) => platform.setKeyPolicy(person.user_key, enabled, k, reason).then(() => { setOpen(null); onChange(); })} />}
          {open === "test" && <DecisionForm actions={[{ value: "test", label: "Test their key" }]}
            onDecide={(_, reason) => platform.testKey(person.user_key, reason).then((r) => { setTested(r.features); setOpen(null); onChange(); })} />}
          {open === "remove" && <DecisionForm actions={[{ value: "remove", label: "Remove their key", tone: "danger" }]}
            onDecide={(_, reason) => platform.removeKey(person.user_key, reason).then(() => { setOpen(null); onChange(); })} />}
          <button className="pa-small" onClick={() => setOpen(null)}>Cancel</button>
        </div>
      ))}</td>
    </tr>
  );
}

export function ModelKeysPage({ me }: { me: Me }) {
  const load = useCallback(() => platform.modelKeys(), []);
  const { data, error, reload } = useLoad(load);
  if (!data) return <Loading error={error} />;
  const manage = data.can_manage && can(me, "manage_model_access");
  const sources = Object.keys(SOURCE_LABEL) as KeySource[];
  return (
    <>
      <p className="pa-lede">Anyone can add their own Gemini key, optionally. Everything they do then runs on their key first,
        and on ours only if theirs fails. Keys are never shown here: only their kind, last 4 characters and status.</p>
      {!data.ready && <p className="pa-error" role="alert">Keys can't be saved yet: MODEL_KEYS_KMS_KEY_NAME isn't set on the API service.</p>}
      <Section title={`Whose key answered (last ${data.days} days)`}>
        <table>
          <thead><tr><th>Key</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Voice minutes</th>
            <th className="num">Errors</th><th className="num">Failovers</th></tr></thead>
          <tbody>{sources.map((s) => {
            const u = data.totals[s];
            return (
              <tr key={s}><td>{SOURCE_LABEL[s]}</td><td className="num">{fmt.num(u?.calls ?? 0)}</td>
                <td className="num">{fmt.num(u?.tokens ?? 0)}</td><td className="num">{(u?.minutes ?? 0).toFixed(1)}</td>
                <td className="num">{fmt.num(u?.errors ?? 0)}</td><td className="num">{fmt.num(u?.failovers ?? 0)}</td></tr>
            );
          })}</tbody>
        </table>
        <p className="pa-muted">{data.platform_key.failed_at
          ? `Our key last failed ${fmt.when(data.platform_key.failed_at)} (${data.platform_key.code}).`
          : "Our key hasn't failed."} Spend on people's own keys is billed to them, so it isn't in our Cloud Billing figures.</p>
        {(data.service.platform?.calls ?? 0) > 0 && (
          <p className="pa-muted">Ours includes our own scheduled work, the hourly fraud cycle: {fmt.num(data.service.platform?.calls ?? 0)} calls
            and {fmt.num(data.service.platform?.tokens ?? 0)} tokens. It isn't a person, so it isn't listed under People.</p>
        )}
      </Section>
      <Section title={`People (${data.people.length})`}>
        {data.people.length === 0 ? <Empty>Nobody has added a key or used one yet.</Empty> : <table>
          <thead><tr><th>Person</th><th>Their key</th><th>Allowed</th><th className="num">Calls on their key</th>
            <th className="num">Calls on ours</th><th className="num">Failovers</th><th></th></tr></thead>
          <tbody>{data.people.map((person) => (
            <KeyRow key={person.user_key} person={person} kinds={data.kinds} manage={manage} onChange={reload} />
          ))}</tbody>
        </table>}
      </Section>
      <Section title="Default for everyone">
        <p>{data.default.enabled ? `Allowed: ${data.default.kinds.map((k) => KIND_LABEL[k]).join(", ") || "no kinds"}.` : "Turned off."}
          {" "}A setting for one person, above, takes its place for them.</p>
        {manage ? <PolicyForm kinds={data.kinds} policy={data.default}
          onSave={(enabled, k, reason) => platform.setKeyDefault(enabled, k, reason).then(reload)} />
          : <p className="pa-muted">Only a platform admin can change this.</p>}
      </Section>
    </>
  );
}

// ---- Audit log -------------------------------------------------------------------------

export function AuditPage() {
  const list = useList<AuditEntry>("log", platform.auditEntries);
  return (
    <>
      <p className="pa-lede">Every decision in this portal: who, what, when and why.</p>
      <Section title="Audit log">
        <ListView list={list} search="Search action, target or key" empty="No decisions recorded yet."
          filters={[{ key: "action", label: "Action", show: (v) => v }, { key: "role", label: "Role" }]}>
          {(items) => (
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Role</th><th>Action</th><th>Target</th><th>Reason</th></tr></thead>
              <tbody>{items.map((e) => (
                <tr key={e.id}><td>{fmt.when(e.at)}</td><td>{e.actor}</td><td>{fmt.label(e.role)}</td>
                  <td className="pa-mono">{e.action}</td><td className="pa-mono">{e.target}</td><td>{e.reason ?? "—"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </ListView>
      </Section>
    </>
  );
}
