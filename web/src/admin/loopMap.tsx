import { useEffect, useState } from "react";
import { fmt, platform, type Agent, type AgentLive, type AgentState, type AgentsLive } from "./api";

// ---- The loop map --------------------------------------------------------------------
//
// Each circle is a loop: an AI step that takes something in and gives something back. Blue shapes
// are code and people only. A gate is a point where a person must act. Which agents each loop
// holds comes from the API (portal.LOOPS); the layout and the words are here.

type Shape = "loop" | "rule" | "store";
type MapNode = { id: string; title: string[]; shape: Shape; x: number; y: number; about: string };
type Edge = { from: string; to: string; label?: string; gate?: string };

const R = 56; // circle radius
const BOX = { w: 150, h: 104 }; // store and rule boxes
const SMALL_BOX = { w: 108, h: 72 };

const NODES: MapNode[] = [
  { id: "fraud_dreamer", title: ["Fraud", "dreamer"], shape: "loop", x: 150, y: 120,
    about: "Imagines new ways someone could attack our safeguards, starting from the guide's tactics. It has no web access." },
  { id: "fraud_researcher", title: ["Fraud", "researcher"], shape: "loop", x: 430, y: 120,
    about: "Checks imagined scenarios and new tactics against web pages, 2 subjects each hour. What 2 independent websites confirm goes into the guide on its own." },
  { id: "fraud_guide", title: ["Fraud", "guide"], shape: "store", x: 730, y: 120,
    about: "The fraud reference guide: tactics, scenarios and warning signs, each with its sources. Each change is a new version, and a reviewer can remove anything added on its own." },
  { id: "guardian", title: ["Guardian"], shape: "loop", x: 1160, y: 120,
    about: "Watches for pressure, probing, synthetic audio and hidden instructions, and records an event for the team." },
  { id: "keep", title: ["Keep or", "discard"], shape: "loop", x: 390, y: 330,
    about: "Turns each exchange into suggestion cards. Nothing is kept until the owner says Keep." },
  { id: "talk", title: ["Talk with", "Saathi"], shape: "loop", x: 130, y: 500,
    about: "Saathi talks with the owner by text, photo or voice, and can speak its replies." },
  { id: "gap_finder", title: ["Gap", "finder"], shape: "rule", x: 390, y: 500,
    about: "Code finds what is still missing in the kept knowledge and picks one gap for Saathi to ask about." },
  { id: "kept", title: ["Kept", "knowledge"], shape: "store", x: 660, y: 500,
    about: "What the owner chose to keep: stories, accounts and wishes." },
  { id: "step_guard", title: ["Step", "guard"], shape: "rule", x: 270, y: 720,
    about: "Code checks each next step against known fraud tactics before Saathi brings it up." },
  { id: "readiness", title: ["Readiness"], shape: "loop", x: 500, y: 720,
    about: "Suggests next steps and the most important gap to ask about." },
  { id: "planning", title: ["Planning"], shape: "loop", x: 730, y: 720,
    about: "Finds wishes that need a legal document. Sharing with a legal partner needs the owner's consent." },
  { id: "certificate", title: ["Certificate", "check"], shape: "loop", x: 960, y: 340,
    about: "Pre-checks a certificate or proof of authority, sign by sign. It only advises: 2 reviewers decide." },
  { id: "release", title: ["Release"], shape: "rule", x: 1220, y: 340,
    about: "Code opens the vault after 2 reviewers approve and the waiting period ends. The guardian can hold it." },
  { id: "ask", title: ["Family", "assistant"], shape: "loop", x: 1090, y: 540,
    about: "Answers the family's questions from the kept knowledge, and cites its sources." },
  { id: "claims", title: ["Claims"], shape: "loop", x: 1090, y: 750,
    about: "Drafts a claims checklist and letters. The family reviews them." },
];

const EDGES: Edge[] = [
  { from: "fraud_dreamer", to: "fraud_researcher", label: "scenarios" },
  { from: "fraud_researcher", to: "fraud_guide", gate: "2 sources" },
  { from: "fraud_guide", to: "certificate", label: "warning signs", gate: "Reviewer" },
  { from: "talk", to: "keep", label: "cards" },
  { from: "keep", to: "kept", gate: "Keep" },
  { from: "kept", to: "gap_finder", label: "what is kept" },
  { from: "gap_finder", to: "talk", label: "one gap to ask" },
  { from: "kept", to: "readiness" },
  { from: "kept", to: "planning", label: "wishes" },
  { from: "readiness", to: "step_guard", label: "next steps" },
  { from: "step_guard", to: "talk", label: "today's topic" },
  { from: "certificate", to: "release", gate: "2 reviewers" },
  { from: "release", to: "ask", label: "vault opens" },
  { from: "kept", to: "ask", label: "sources" },
  { from: "kept", to: "claims", label: "accounts" },
];

const GROUPS = [
  { label: "WATCHING FOR FRAUD · ALWAYS ON", x: 20, y: 20, w: 1300, h: 210, right: false },
  { label: "WHILE THE OWNER IS HERE", x: 20, y: 250, w: 830, h: 590, right: false },
  { label: "WHEN THE TIME COMES", x: 870, y: 250, w: 450, h: 590, right: true }, // clear of the warning signs line
];
const GATEWAY = { x: 20, y: 862, w: 1300, h: 52 };

const AUTONOMY: Record<Agent["autonomy"], { tag: string; css: string }> = {
  act_within_limits: { tag: "ACTS", css: "acts" },
  propose: { tag: "PROPOSES", css: "proposes" },
  assist: { tag: "ASSISTS", css: "assists" },
};
const STATE_WORDS: Record<AgentState, string> = {
  working: "Working now", active: "Active today", idle: "Idle", paused: "Paused", planned: "Planned",
};
// A loop shows its busiest agent: one working agent makes the whole loop working.
const STATE_ORDER: AgentState[] = ["working", "active", "paused", "idle", "planned"];

const size = (n: MapNode) => (n.shape === "loop" ? null : n.id === "step_guard" ? SMALL_BOX : n.shape === "rule" ? null : BOX);

/** The point where a line from the centre towards (dx, dy) leaves the node, plus a small gap. */
function edgePoint(n: MapNode, dx: number, dy: number, gap = 6): [number, number] {
  const len = Math.hypot(dx, dy) || 1;
  const box = size(n);
  if (!box) return [n.x + (dx / len) * (R + gap), n.y + (dy / len) * (R + gap)];
  const t = Math.min(box.w / 2 / Math.abs(dx || 1e-9), box.h / 2 / Math.abs(dy || 1e-9));
  return [n.x + dx * t + (dx / len) * gap, n.y + dy * t + (dy / len) * gap];
}

function loopState(ids: string[], live: AgentsLive | null): AgentState | null {
  if (!live || ids.length === 0) return null;
  const states = ids.map((id) => live.agents[id]?.state).filter(Boolean) as AgentState[];
  return STATE_ORDER.find((s) => states.includes(s)) ?? null;
}

const POLL_MS = 15000;
const W = 1340, H = 930; // the drawing's own units

/** A button's place over the drawing, in percent, so it follows the drawing at any width. */
const place = (x: number, y: number, w: number, h: number) =>
  ({ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`, width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%` });

export function LoopMap({ agents }: { agents: Agent[] }) {
  const [live, setLive] = useState<AgentsLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    const load = () => {
      if (document.hidden) return; // no polling in a background tab
      platform.agentsLive().then((d) => { if (!stopped) { setLive(d); setError(null); } })
        .catch((e) => { if (!stopped) setError(String(e.message ?? e)); });
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    document.addEventListener("visibilitychange", load);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, []);

  const byId = new Map(agents.map((a) => [a.id, a]));
  const loops = live?.loops ?? {};
  const node = (id: string) => NODES.find((n) => n.id === id) as MapNode;
  const pick = (id: string) => setSelected((s) => (s === id ? null : id));
  const working = Object.values(live?.agents ?? {}).filter((a) => a.state === "working").length;

  return (
    <div className="lm">
      <div className="lm-map">
        <div className="lm-bar">
          <span>{live ? (working ? `${working} agent${working > 1 ? "s" : ""} working now` : "No agent is working right now") : "Loading live status…"}</span>
          {live && <span className="pa-muted">Updated {new Date(live.at * 1000).toLocaleTimeString()} · every 15 seconds</span>}
          {error && <span className="pa-error" role="alert">{error}</span>}
        </div>
        <div className="lm-scroll"><div className="lm-canvas">
          <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true" focusable="false">
            <defs>
              <marker id="lm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" className="lm-arrowhead" />
              </marker>
            </defs>
            {GROUPS.map((g) => (
              <g key={g.label}>
                <rect x={g.x} y={g.y} width={g.w} height={g.h} rx="18" className="lm-group" />
                <text x={g.right ? g.x + g.w - 22 : g.x + 22} y={g.y + 32} className="lm-group-label"
                  textAnchor={g.right ? "end" : "start"}>{g.label}</text>
              </g>
            ))}
            {EDGES.map((e) => {
              const a = node(e.from), b = node(e.to);
              const [x1, y1] = edgePoint(a, b.x - a.x, b.y - a.y);
              const [x2, y2] = edgePoint(b, a.x - b.x, a.y - b.y);
              const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
              const w = e.gate ? e.gate.length * 8.4 + 40 : 0;
              return (
                <g key={`${e.from}-${e.to}`}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} className="lm-edge" markerEnd="url(#lm-arrow)" />
                  {e.label && <text x={mx} y={e.gate ? my - 24 : my + 4} className="lm-edge-label">{e.label}</text>}
                  {e.gate && (
                    <g className="lm-gate">
                      <rect x={mx - w / 2} y={my - 14} width={w} height={28} rx="14" />
                      <text x={mx} y={my + 5}>✋ {e.gate}</text>
                    </g>
                  )}
                </g>
              );
            })}
            <g className="lm-gate">
              <rect x={730 - 128} y={805} width={256} height={30} rx="15" />
              <text x={730} y={825}>✋ Consent → legal partner</text>
            </g>
            {NODES.map((n) => {
              const ids = loops[n.id] ?? [];
              const first = ids.map((id) => byId.get(id)).find(Boolean);
              const kind = n.shape === "loop" ? AUTONOMY[first?.autonomy ?? "propose"] : null;
              const state = n.shape === "loop" ? loopState(ids, live) : null;
              const box = size(n);
              const css = `lm-node lm-${kind?.css ?? "rule"}${selected === n.id ? " lm-selected" : ""}${state ? ` lm-state-${state}` : ""}`;
              const bottom = box ? n.y + box.h / 2 : n.y + R;
              return (
                <g key={n.id} className={css}>
                  {state === "working" && <circle cx={n.x} cy={n.y} r={R + 9} className="lm-pulse" />}
                  {box ? <rect x={n.x - box.w / 2} y={n.y - box.h / 2} width={box.w} height={box.h} rx="14" className="lm-shape" />
                    : <circle cx={n.x} cy={n.y} r={R} className="lm-shape" />}
                  <text x={n.x} y={n.y - (n.title.length - 1) * 10 + 6} className="lm-title">
                    {n.title.map((line, i) => <tspan key={line} x={n.x} dy={i ? 21 : 0}>{line}</tspan>)}
                  </text>
                  <text x={n.x} y={bottom + 22} className="lm-tag">{kind?.tag ?? (n.shape === "store" ? "STORE" : "NO AI")}</text>
                  {state && <text x={n.x} y={bottom + 40} className="lm-state">{STATE_WORDS[state]}</text>}
                </g>
              );
            })}
            <g className={`lm-gateway${selected === "gateway" ? " lm-selected" : ""}`}>
              <rect x={GATEWAY.x} y={GATEWAY.y} width={GATEWAY.w} height={GATEWAY.h} rx="12" />
              <text x={GATEWAY.x + GATEWAY.w / 2} y={GATEWAY.y + 32}>
                Gateway: every model call passes here. Registered agent? Allowed model? Budget left?
                {live && live.kill_switch !== "off" ? ` · Kill switch: ${fmt.label(live.kill_switch)}` : ""}
                {live && live.ceiling !== "ok" ? ` · Daily ceiling: ${fmt.label(live.ceiling)}` : ""}
              </text>
            </g>
          </svg>
          {/* Real buttons over the drawing, so the map works with a keyboard and a screen reader. */}
          {NODES.map((n) => {
            const box = size(n) ?? { w: 2 * R, h: 2 * R };
            const state = n.shape === "loop" ? loopState(loops[n.id] ?? [], live) : null;
            return (
              <button key={n.id} type="button" className={`lm-hit${box.w === box.h ? " lm-hit-round" : ""}`}
                style={place(n.x - box.w / 2, n.y - box.h / 2, box.w, box.h)} aria-pressed={selected === n.id}
                aria-label={`${n.title.join(" ")}${state ? `, ${STATE_WORDS[state]}` : ""}`} onClick={() => pick(n.id)} />
            );
          })}
          <button type="button" className="lm-hit" style={place(GATEWAY.x, GATEWAY.y, GATEWAY.w, GATEWAY.h)}
            aria-pressed={selected === "gateway"} aria-label="Gateway" onClick={() => pick("gateway")} />
        </div></div>
      </div>
      <aside className="lm-panel" aria-live="polite">
        {selected ? <Snapshot id={selected} ids={loops[selected] ?? []} byId={byId} live={live} onClose={() => setSelected(null)} />
          : <Legend />}
      </aside>
    </div>
  );
}

function Legend() {
  return (
    <>
      <p className="lm-eyebrow">HOW TO READ THE MAP</p>
      <h3>Select any loop</h3>
      <p>The colour of a loop tells how much it may do on its own.</p>
      <dl className="lm-legend">
        <dt className="lm-chip lm-acts">Acts within limits</dt><dd>It does the job itself, inside fixed limits. Nothing it makes is final.</dd>
        <dt className="lm-chip lm-proposes">Proposes</dt><dd>It suggests. A person says yes before anything happens.</dd>
        <dt className="lm-chip lm-assists">Assists</dt><dd>It only gives advice. A person makes the decision.</dd>
        <dt className="lm-chip lm-rule">No AI</dt><dd>Fixed rules in code, and people. No model is called.</dd>
        <dt className="lm-chip lm-gatechip">✋ Gate</dt><dd>A person must act before anything moves past this point.</dd>
      </dl>
      <p className="pa-muted">A pulsing ring means a model call is running now, or a fraud run is in progress. Select a loop to see its agents, its last call and today's numbers. Select the dark bar to see the gateway.</p>
    </>
  );
}

function Snapshot({ id, ids, byId, live, onClose }: {
  id: string; ids: string[]; byId: Map<string, Agent>; live: AgentsLive | null; onClose: () => void;
}) {
  const n = NODES.find((x) => x.id === id);
  const title = n ? n.title.join(" ") : "Gateway";
  const about = n?.about ?? "Every model call passes here: is the agent registered, is the model allowed, and is there budget left? Anything else is refused and recorded under Blocked calls below.";
  return (
    <>
      <div className="lm-panel-head">
        <h3>{title}</h3>
        <button type="button" className="pa-link" onClick={onClose} aria-label="Close and show how to read the map">Close</button>
      </div>
      <p>{about}</p>
      {id === "gateway" && live && (
        <dl className="lm-figures">
          <dt>Kill switch</dt><dd>{fmt.label(live.kill_switch)}</dd>
          <dt>Daily ceiling</dt><dd>{fmt.label(live.ceiling)}</dd>
        </dl>
      )}
      {id === "guardian" && live && (
        <p><a href="#/guardian">{live.guardian_open} open event{live.guardian_open === 1 ? "" : "s"} for the team</a></p>
      )}
      {n && n.shape !== "loop" && <p className="pa-muted">{n.shape === "store" ? "A store: it holds data and calls no model." : "No model is called here."}</p>}
      {ids.map((aid) => <AgentCard key={aid} agent={byId.get(aid)} id={aid} live={live?.agents[aid] ?? null} />)}
    </>
  );
}

const count = (n: number, word: string) => `${fmt.num(n)} ${word}${n === 1 ? "" : "s"}`;

const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return fmt.when(t);
};

function AgentCard({ id, agent, live }: { id: string; agent: Agent | undefined; live: AgentLive | null }) {
  const u = agent?.usage_30d;
  return (
    <section className="lm-agent">
      <div className="lm-agent-head">
        <span className="pa-mono">{id}</span>
        {live && <span className={`pa-status pa-status-${live.state === "active" ? "ok" : live.state}`}>{STATE_WORDS[live.state]}</span>}
      </div>
      {agent && <p className="pa-sub">{agent.purpose}</p>}
      {agent && <p className="pa-sub">{fmt.label(agent.autonomy)} · <span className="pa-mono">{agent.models.primary}</span>{agent.safety_critical ? " · safety-critical" : ""}</p>}
      {live && (
        <dl className="lm-figures">
          {live.running_calls > 0 && <><dt>Running now</dt><dd>{count(live.running_calls, "call")}</dd></>}
          {live.paused_by && <><dt>Paused by</dt><dd>{live.paused_by === "kill_switch" ? "the kill switch" : "the daily ceiling"}</dd></>}
          <dt>Last call</dt>
          <dd>{live.last ? <>{ago(live.last.at)} · {live.last.result === "failed" ? "failed" : "done"}<div className="pa-sub pa-mono">{live.last.model}</div></> : "none yet"}</dd>
          <dt>Today</dt>
          <dd>{count(live.today.calls, "call")} · {count(live.today.tokens, "token")}{live.today.minutes > 0 ? ` · ${live.today.minutes} min` : ""}
            {(live.today.errors > 0 || live.today.blocked > 0) && <div className="pa-sub">{count(live.today.errors, "error")} · {live.today.blocked} blocked</div>}</dd>
          {u && <><dt>30 days</dt><dd>{count(u.calls, "call")} · {count(u.tokens_in + u.tokens_out, "token")}
            <div className="pa-sub">{fmt.pct(u.error_rate)} errors · {u.blocked} blocked</div></dd></>}
          {live.last_run && <><dt>Last run</dt><dd>
            <span className={`pa-status pa-status-${live.last_run.status}`}>{fmt.label(live.last_run.status)}</span> {ago(live.last_run.started_at)}
            {live.last_run.counts && Object.keys(live.last_run.counts).length > 0 && (
              <div className="pa-sub">{Object.entries(live.last_run.counts).map(([k, v]) => `${fmt.label(k)} ${v}`).join(" · ")}</div>)}
            <div><a href="#/fraud">Open the Fraud page</a></div>
          </dd></>}
        </dl>
      )}
    </section>
  );
}
