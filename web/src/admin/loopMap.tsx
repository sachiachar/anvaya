import { useEffect, useState } from "react";
import { fmt, platform, type Agent, type AgentLive, type AgentState, type AgentsLive } from "./api";

// ---- The loop map --------------------------------------------------------------------
//
// Guardian-led conversations (PR-2.12, docs/proposals/guardian-led-conversations.md): the guardian
// is the central control, fed by fixed rules and the fraud module; the conversation planner is the
// only source of what Saathi talks about; gaps and readiness feed only the planner. Parts not built
// yet are drawn as "coming", with the requirement that brings them. Which agents each loop holds
// comes from the API (portal.LOOPS); the layout and the words are here.

type Shape = "loop" | "rule" | "store" | "control";
type Kind = "acts" | "proposes" | "assists" | "rule";
type MapNode = {
  id: string; title: string[]; shape: Shape; x: number; y: number; w?: number; h?: number;
  kind?: Kind; // for shapes with no agent: how the map colours it
  does: string; gets: string; gives: string; decides: string; coming?: string;
};
type Edge = { from: string; to: string; label?: string; gate?: string; bend?: number; coming?: boolean;
  quiet?: boolean; both?: boolean };

const W = 1340, H = 1005; // the drawing's own units
const R = 56; // circle radius

const NODES: MapNode[] = [
  { id: "fraud_dreamer", title: ["Fraud", "dreamer"], shape: "loop", x: 140, y: 110,
    does: "Imagines new attacks on our safeguards, starting from each tactic in turn, every hour.",
    gets: "The fraud guide's tactics and recent guardian events", gives: "Scenarios, each mapped to the safeguard that catches it, or a gap",
    decides: "It runs on its own. A gap that sources confirm becomes a guardian event for the team." },
  { id: "fraud_researcher", title: ["Fraud", "researcher"], shape: "loop", x: 400, y: 110,
    does: "Researches 2 subjects each hour on the web and checks each scenario. A source counts only if its quote is on the page.",
    gets: "Scenarios and research subjects", gives: "Confirmed tactics and scenarios",
    decides: "What 2 independent websites confirm goes into the guide on its own, add-only. A reviewer can remove it; 2 removals in a week pause adding." },
  { id: "fraud_guide", title: ["Fraud", "guide"], shape: "store", x: 680, y: 110, w: 156, h: 110,
    does: "Tactics, scenarios and warning signs for each document and country, each with its sources. Every change is a new version.",
    gets: "Confirmed tactics and scenarios", gives: "The fraud module for the guardian, and warning signs for the certificate check",
    decides: "A new warning sign waits for a reviewer before any check uses it." },
  { id: "rules", title: ["Fixed", "rules"], shape: "rule", x: 1010, y: 110, w: 170, h: 96,
    does: "Rules in code: passing on a secret, paying or changing something because someone else asked, being rushed, and words aimed at Saathi.",
    gets: "Nothing: they change only through code review", gives: "The first input to every guardian decision",
    decides: "The policy file can make a response stricter, never looser." },
  { id: "guardian", title: ["Guardian · central control"], shape: "control", x: 670, y: 270, w: 440, h: 104,
    does: "Everything new the owner says, every next step, and every family question passes here first.",
    gets: "Fixed rules and the fraud module", gives: "Allow, deflect or block, linked by id to the turn and the card",
    decides: "It never keeps a card or opens the vault: people do. Any response but allow becomes an event for a risk reviewer." },
  { id: "initial_plan", title: ["Initial", "plan"], shape: "store", x: 230, y: 440, w: 150, h: 100, coming: "PR-2.12.2",
    does: "Where a new owner starts: welcome, the people who matter, a first story, where money matters are kept, wishes.",
    gets: "A file reviewed like code", gives: "The first conversation plan", decides: "Changed only through code review." },
  { id: "planner", title: ["Conversation", "planner"], shape: "rule", x: 450, y: 590, w: 190, h: 112, coming: "PR-2.12.2",
    does: "The only source of what Saathi talks about. It updates after each guardian decision, each step outcome and each Keep.",
    gets: "The initial plan, gaps, readiness suggestions, guardian decisions and step outcomes", gives: "The current step for Saathi",
    decides: "Code orders the steps, and every step passes the guardian." },
  { id: "talk", title: ["Talk with", "Saathi"], shape: "loop", x: 140, y: 650,
    does: "Saathi talks with the owner by text, photo or voice. It carries out the planner's current step and never picks a topic (PR-2.12.3).",
    gets: "The current step, and what the owner says", gives: "A reply, and what the owner said, for the guardian",
    decides: "Nothing is saved here: the guardian sees each new piece of information first." },
  { id: "keep", title: ["Cards for", "Keep"], shape: "loop", x: 720, y: 435,
    does: "Cards made only from what the guardian allowed. A repeat adds nothing; a new detail becomes an update card.",
    gets: "Allowed information", gives: "Cards waiting for the owner", decides: "The owner taps Keep. The guardian checks again at Keep." },
  { id: "kept", title: ["Kept", "knowledge"], shape: "store", x: 720, y: 655, w: 156, h: 110,
    does: "Only what the owner kept, each with its source. The vault is never here.",
    gets: "Kept cards", gives: "The source for gaps, readiness, planning, the family assistant and claims", decides: "Keep is the commit." },
  { id: "gap_finder", title: ["Gap", "finder"], shape: "loop", kind: "rule", x: 330, y: 790,
    does: "Fixed rules find what is missing, such as a nominee, a will or where papers are, ranked by harm.",
    gets: "Kept knowledge", gives: "Gaps, to the planner only", decides: "Saathi never sees gaps directly." },
  { id: "readiness", title: ["Readiness"], shape: "loop", x: 560, y: 790,
    does: "Suggests next steps from what is kept.", gets: "Kept knowledge", gives: "Suggestions, to the planner only",
    decides: "The planner decides whether and when a suggestion becomes a step." },
  { id: "planning", title: ["Planning"], shape: "loop", x: 790, y: 780,
    does: "Finds wishes that need a legal document, such as a will for the house.", gets: "Wishes and accounts",
    gives: "A list of legal needs", decides: "The owner ticks consent before anything goes to the legal partner." },
  { id: "certificate", title: ["Certificate", "check"], shape: "loop", x: 990, y: 480,
    does: "Pre-checks a certificate or proof of authority, sign by sign, against the fraud guide.", gets: "A photo of the document",
    gives: "A verdict and a result for each warning sign", decides: "It only advises. Two reviewers decide." },
  { id: "release", title: ["Release"], shape: "loop", kind: "rule", x: 1220, y: 480,
    does: "Code opens the vault after 2 reviewers approve and the waiting period ends.", gets: "Two confirmations",
    gives: "The vault opens for the named recipient", decides: "The guardian can hold it for a risk reviewer." },
  { id: "ask", title: ["Family", "assistant"], shape: "loop", x: 1110, y: 640,
    does: "Answers the family's questions from kept knowledge, with sources. The guardian checks each question, and each answer from PR-2.12.4.",
    gets: "A family question, after release", gives: "An answer with sources", decides: "The guardian decides whether a question is answered." },
  { id: "claims", title: ["Claims"], shape: "loop", x: 1110, y: 815,
    does: "Drafts a claims checklist and letters for each bank and insurer.", gets: "Kept accounts and policies",
    gives: "A checklist and draft letters", decides: "The family reviews and sends each letter." },
];

const EDGES: Edge[] = [
  { from: "fraud_dreamer", to: "fraud_researcher", label: "scenarios" },
  { from: "fraud_researcher", to: "fraud_guide", gate: "2 sources" },
  { from: "fraud_guide", to: "guardian", label: "fraud module" },
  { from: "rules", to: "guardian", label: "fixed rules" },
  { from: "fraud_guide", to: "certificate", label: "warning signs", gate: "Reviewer", bend: -190 },
  { from: "talk", to: "guardian", label: "what the owner said" },
  { from: "guardian", to: "keep", label: "allow" },
  { from: "guardian", to: "planner", label: "deflect · block", coming: true },
  { from: "keep", to: "kept", gate: "Keep" },
  { from: "initial_plan", to: "planner", label: "cold start", coming: true },
  { from: "planner", to: "talk", label: "current step", coming: true },
  { from: "kept", to: "gap_finder" },
  { from: "kept", to: "readiness" },
  { from: "gap_finder", to: "planner", label: "gaps", coming: true },
  { from: "readiness", to: "planner", label: "suggestions", coming: true },
  { from: "kept", to: "planning", label: "wishes", bend: -30 },
  { from: "certificate", to: "release", gate: "2 reviewers" },
  { from: "release", to: "ask", label: "vault opens", bend: 25 },
  { from: "kept", to: "ask", label: "sources" },
  { from: "kept", to: "claims", label: "accounts" },
  { from: "ask", to: "guardian", label: "question · answer", bend: -80, both: true },
  // Shown when a loop is selected or in the story, so the map stays readable.
  { from: "planner", to: "guardian", label: "each step checked", quiet: true, bend: 40 },
  { from: "certificate", to: "guardian", label: "signs found", quiet: true, bend: 30 },
  { from: "release", to: "guardian", label: "holds", quiet: true, bend: 40 },
  { from: "guardian", to: "fraud_dreamer", label: "events", quiet: true, bend: 60 },
];
const eid = (e: Edge) => `${e.from}>${e.to}`;

const GROUPS = [
  { label: "WATCHING FOR FRAUD · EVERY HOUR", x: 20, y: 20, w: 1300, h: 178, right: false },
  { label: "WHILE THE OWNER IS HERE", x: 20, y: 345, w: 860, h: 575, right: false },
  { label: "WHEN THE TIME COMES", x: 900, y: 345, w: 420, h: 575, right: true },
];
const GATEWAY = { x: 20, y: 938, w: 1300, h: 52 };

type Step = { who: string; text: string; nodes: string[]; edges: string[]; gateway?: boolean };
const STORY: Step[] = [
  { who: "A new owner · cold start", nodes: ["initial_plan", "planner", "talk"], edges: ["initial_plan>planner", "planner>talk"],
    text: "Vikram opens Talk for the first time. The planner starts from the initial plan, and Saathi asks its first step: the people who matter to him." },
  { who: "Vikram speaks", nodes: ["talk", "guardian", "rules", "fraud_guide"], edges: ["talk>guardian", "rules>guardian", "fraud_guide>guardian"],
    text: "He mentions his LIC policy. Before anything is made of it, what he said goes to the guardian, which checks it against the fixed rules and the fraud module." },
  { who: "The guardian allows it", nodes: ["guardian", "keep", "kept"], edges: ["guardian>keep", "keep>kept"],
    text: "Nothing risky: a card appears, linked to the guardian's decision. Only when Vikram taps Keep does the policy enter kept knowledge." },
  { who: "Gaps feed the planner", nodes: ["kept", "gap_finder", "readiness", "planner", "talk"],
    edges: ["kept>gap_finder", "kept>readiness", "gap_finder>planner", "readiness>planner", "planner>talk", "planner>guardian"],
    text: "The gap finder sees the policy has no nominee; readiness suggests adding one. Both feed only the planner. The planner makes it the next step, the guardian checks the step, and Saathi asks about it." },
  { who: "Something risky", nodes: ["talk", "guardian", "planner"], edges: ["talk>guardian", "guardian>planner"],
    text: "Vikram says a man called asking for a fee to release his deposit. The guardian deflects: no card. The planner adds a safe-route step, so Saathi says the bank should be called directly." },
  { who: "Every hour · the safety team", nodes: ["fraud_dreamer", "fraud_researcher", "fraud_guide", "guardian"],
    edges: ["guardian>fraud_dreamer", "fraud_dreamer>fraud_researcher", "fraud_researcher>fraud_guide", "fraud_guide>guardian"],
    text: "The dreamer imagines new attacks; the researcher checks them on the web. What 2 independent websites confirm joins the fraud guide, and the guardian uses it from then on." },
  { who: "A wish", nodes: ["kept", "planning"], edges: ["kept>planning"],
    text: "Vikram wishes the house to go to Priya. Planning says this needs a will; only after he ticks consent does a summary go to the legal partner." },
  { who: "When the time comes", nodes: ["certificate", "fraud_guide", "release", "guardian"],
    edges: ["fraud_guide>certificate", "certificate>release", "certificate>guardian", "release>guardian"],
    text: "Priya uploads the certificate. The check reads it against the guide's reviewed warning signs, two reviewers decide, and the guardian can hold the release." },
  { who: "Priya asks", nodes: ["ask", "guardian", "kept"], edges: ["ask>guardian", "kept>ask"],
    text: "Priya asks who the nominee is. The guardian checks the question, and the assistant answers only from what Vikram kept, with sources. A question for a password gets a fixed reply." },
  { who: "Underneath everything", nodes: NODES.map((n) => n.id), edges: [], gateway: true,
    text: "Every model call passed through one gateway, and every decision is linked by id: turn, guardian decision, card, Keep, plan step. No record holds what anyone said." },
];

const AUTONOMY: Record<Agent["autonomy"], { tag: string; css: Kind }> = {
  act_within_limits: { tag: "ACTS", css: "acts" },
  propose: { tag: "PROPOSES", css: "proposes" },
  assist: { tag: "ASSISTS", css: "assists" },
};
const STATE_WORDS: Record<AgentState, string> = {
  working: "Working now", active: "Active today", idle: "Idle", paused: "Paused", planned: "Planned",
};
// A loop shows its busiest agent: one working agent makes the whole loop working.
const STATE_ORDER: AgentState[] = ["working", "active", "paused", "idle", "planned"];

const box = (n: MapNode) => (n.shape === "loop" ? null : { w: n.w ?? 150, h: n.h ?? 92 });

/** How far from a node's centre its edge is, in the direction of an angle, plus a small gap. */
function reach(n: MapNode, angle: number) {
  const b = box(n);
  if (!b) return R + 6;
  const c = Math.abs(Math.cos(angle)), s = Math.abs(Math.sin(angle));
  return Math.min(c ? b.w / 2 / c : 1e9, s ? b.h / 2 / s : 1e9) + 6;
}

/** A gently curved line between two nodes, and the point for its label. */
function geometry(a: MapNode, b: MapNode, bend = 0) {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const mx = (a.x + b.x) / 2 - uy * bend, my = (a.y + b.y) / 2 + ux * bend;
  const sa = Math.atan2(my - a.y, mx - a.x), sb = Math.atan2(my - b.y, mx - b.x);
  const p1 = [a.x + Math.cos(sa) * reach(a, sa), a.y + Math.sin(sa) * reach(a, sa)];
  const p2 = [b.x + Math.cos(sb) * reach(b, sb), b.y + Math.sin(sb) * reach(b, sb)];
  return { d: `M${p1[0]},${p1[1]} Q${mx},${my} ${p2[0]},${p2[1]}`,
    lx: 0.25 * p1[0] + 0.5 * mx + 0.25 * p2[0], ly: 0.25 * p1[1] + 0.5 * my + 0.25 * p2[1] };
}

function loopState(ids: string[], live: AgentsLive | null): AgentState | null {
  if (!live || ids.length === 0) return null;
  const states = ids.map((id) => live.agents[id]?.state).filter(Boolean) as AgentState[];
  return STATE_ORDER.find((s) => states.includes(s)) ?? null;
}

/** The loop and its neighbours, for highlighting. */
function around(id: string): [string[], string[]] {
  const es = EDGES.filter((e) => e.from === id || e.to === id);
  return [[id, ...es.flatMap((e) => [e.from, e.to])], es.map(eid)];
}

/** A button's place over the drawing, in percent, so it follows the drawing at any width. */
const place = (x: number, y: number, w: number, h: number) =>
  ({ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`, width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%` });

const POLL_MS = 15000;
type Mode = "live" | "story";

export function LoopMap({ agents }: { agents: Agent[] }) {
  const [live, setLive] = useState<AgentsLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("live");
  const [step, setStep] = useState(0);

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

  useEffect(() => {
    if (mode !== "story") return;
    const keys = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "ArrowRight") setStep((s) => Math.min(STORY.length - 1, s + 1));
      if (e.key === "ArrowLeft") setStep((s) => Math.max(0, s - 1));
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [mode]);

  const byId = new Map(agents.map((a) => [a.id, a]));
  const loops = live?.loops ?? {};
  const node = (id: string) => NODES.find((n) => n.id === id) as MapNode;
  const pick = (id: string) => { setMode("live"); setSelected((s) => (s === id ? null : id)); };
  const working = Object.values(live?.agents ?? {}).filter((a) => a.state === "working").length;

  // What is lit: the story's step, or the selected (or hovered) loop and its neighbours.
  const focus = selected && selected !== "gateway" ? selected : hover;
  const [litNodes, litEdges]: [string[] | null, string[]] = mode === "story" ? [STORY[step].nodes, STORY[step].edges]
    : focus ? around(focus) : [null, []];
  const gatewayLit = mode === "story" ? !!STORY[step].gateway : selected === "gateway";

  const kindOf = (n: MapNode): Kind => n.kind ?? (n.shape === "loop" ? AUTONOMY[byId.get((loops[n.id] ?? [])[0])?.autonomy ?? "propose"].css
    : n.shape === "control" ? "assists" : "rule");
  const tagOf = (n: MapNode) => n.shape === "store" ? "STORE" : n.shape === "control" ? "" :
    n.kind === "rule" || n.shape === "rule" ? "NO AI" : AUTONOMY[byId.get((loops[n.id] ?? [])[0])?.autonomy ?? "propose"].tag;

  return (
    <div className="lm">
      <div className="lm-map">
        <div className="lm-bar">
          <span>{live ? (working ? `${working} agent${working > 1 ? "s" : ""} working now` : "No agent is working right now") : "Loading live status…"}</span>
          {live && <span className="pa-muted">Updated {new Date(live.at * 1000).toLocaleTimeString()} · every 15 seconds</span>}
          {error && <span className="pa-error" role="alert">{error}</span>}
          <span className="lm-modes">
            <button type="button" aria-pressed={mode === "live"} onClick={() => setMode("live")}>Live</button>
            <button type="button" aria-pressed={mode === "story"} onClick={() => { setMode("story"); setSelected(null); }}>Walk a story</button>
          </span>
        </div>
        <div className="lm-scroll"><div className="lm-canvas">
          <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true" focusable="false" className={litNodes ? "lm-dim" : ""}>
            <defs>
              {["lm-arrow", "lm-arrow-hot"].map((id) => (
                <marker key={id} id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" className={id === "lm-arrow" ? "lm-arrowhead" : "lm-arrowhead-hot"} />
                </marker>
              ))}
            </defs>
            {GROUPS.map((g) => (
              <g key={g.label}>
                <rect x={g.x} y={g.y} width={g.w} height={g.h} rx="18" className="lm-group" />
                <text x={g.right ? g.x + g.w - 22 : g.x + 22} y={g.y + 32} className="lm-group-label"
                  textAnchor={g.right ? "end" : "start"}>{g.label}</text>
              </g>
            ))}
            {EDGES.map((e) => {
              const hot = litEdges.includes(eid(e));
              if (e.quiet && !hot) return null;
              const { d, lx, ly } = geometry(node(e.from), node(e.to), e.bend);
              const w = e.gate ? e.gate.length * 8.4 + 40 : 0;
              const marker = `url(#${hot ? "lm-arrow-hot" : "lm-arrow"})`;
              return (
                <g key={eid(e)} className={`lm-edge${hot ? " lm-hot" : ""}${e.coming ? " lm-coming" : ""}`}>
                  <path d={d} markerEnd={marker} markerStart={e.both ? marker : undefined} />
                  {e.label && <text x={lx} y={e.gate ? ly - 22 : ly + 4} className="lm-edge-label">{e.label}</text>}
                  {e.gate && (
                    <g className="lm-gate">
                      <rect x={lx - w / 2} y={ly - 14} width={w} height={28} rx="14" />
                      <text x={lx} y={ly + 5}>✋ {e.gate}</text>
                    </g>
                  )}
                </g>
              );
            })}
            <g className={`lm-gate${litNodes && !litNodes.includes("planning") ? " lm-faded" : ""}`}>
              <rect x={790 - 92} y={888} width={184} height={26} rx="13" />
              <text x={790} y={906}>✋ Consent → partner</text>
            </g>
            {NODES.map((n) => {
              const ids = loops[n.id] ?? [];
              const state = n.shape === "loop" && !n.kind ? loopState(ids, live) : null;
              const b = box(n);
              const lit = !litNodes || litNodes.includes(n.id);
              const css = `lm-node lm-${kindOf(n)}${selected === n.id ? " lm-selected" : ""}${state ? ` lm-state-${state}` : ""}`
                + `${n.coming ? " lm-node-coming" : ""}${lit ? "" : " lm-faded"}${litNodes?.includes(n.id) ? " lm-lit" : ""}`;
              const bottom = b ? n.y + b.h / 2 : n.y + R;
              const status = n.id === "guardian" && live ? `Live · ${live.guardian_open} open event${live.guardian_open === 1 ? "" : "s"}`
                : n.coming ? `Coming · ${n.coming}` : state && state !== "planned" ? STATE_WORDS[state] : state === "planned" ? "Planned" : null;
              return (
                <g key={n.id} className={css}>
                  {state === "working" && <circle cx={n.x} cy={n.y} r={R + 9} className="lm-pulse" />}
                  {b ? <rect x={n.x - b.w / 2} y={n.y - b.h / 2} width={b.w} height={b.h} rx={n.shape === "control" ? 46 : 14} className="lm-shape" />
                    : <circle cx={n.x} cy={n.y} r={R} className="lm-shape" />}
                  {n.shape === "control" ? (
                    <>
                      <text x={n.x} y={n.y - 18} className="lm-title">{n.title[0]}</text>
                      <text x={n.x} y={n.y + 6} className="lm-sub">fixed rules + fraud module → allow · deflect · block</text>
                      {status && <text x={n.x} y={n.y + 30} className="lm-state">{status}</text>}
                    </>
                  ) : b ? (
                    // Boxes carry their tag and status inside, clear of the lines that meet them.
                    <>
                      <text x={n.x} y={n.y - b.h / 2 + 30} className="lm-title">
                        {n.title.map((line, i) => <tspan key={line} x={n.x} dy={i ? 20 : 0}>{line}</tspan>)}
                      </text>
                      <text x={n.x} y={n.y + b.h / 2 - (status ? 28 : 14)} className="lm-tag">{tagOf(n)}</text>
                      {status && <text x={n.x} y={n.y + b.h / 2 - 11} className="lm-state">{status}</text>}
                    </>
                  ) : (
                    <>
                      <text x={n.x} y={n.y - (n.title.length - 1) * 10 + 6} className="lm-title">
                        {n.title.map((line, i) => <tspan key={line} x={n.x} dy={i ? 21 : 0}>{line}</tspan>)}
                      </text>
                      <text x={n.x} y={bottom + 22} className="lm-tag">{tagOf(n)}</text>
                      {status && <text x={n.x} y={bottom + 40} className="lm-state">{status}</text>}
                    </>
                  )}
                </g>
              );
            })}
            <g className={`lm-gateway${gatewayLit ? " lm-selected" : ""}`}>
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
            const b = box(n) ?? { w: 2 * R, h: 2 * R };
            const state = n.shape === "loop" && !n.kind ? loopState(loops[n.id] ?? [], live) : null;
            return (
              <button key={n.id} type="button" className={`lm-hit${n.shape === "loop" ? " lm-hit-round" : ""}`}
                style={place(n.x - b.w / 2, n.y - b.h / 2, b.w, b.h)} aria-pressed={selected === n.id}
                aria-label={`${n.title.join(" ")}${state ? `, ${STATE_WORDS[state]}` : ""}${n.coming ? ", coming" : ""}`}
                onClick={() => pick(n.id)} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(n.id)} onBlur={() => setHover(null)} />
            );
          })}
          <button type="button" className="lm-hit" style={place(GATEWAY.x, GATEWAY.y, GATEWAY.w, GATEWAY.h)}
            aria-pressed={selected === "gateway"} aria-label="Gateway" onClick={() => pick("gateway")} />
        </div></div>
      </div>
      <aside className="lm-panel" aria-live="polite">
        {mode === "story" ? <Story step={step} setStep={setStep} />
          : selected ? <Snapshot id={selected} ids={loops[selected] ?? []} byId={byId} live={live} onClose={() => setSelected(null)} />
            : <Legend />}
      </aside>
    </div>
  );
}

function Story({ step, setStep }: { step: number; setStep: (n: number) => void }) {
  const s = STORY[step];
  const last = step === STORY.length - 1;
  return (
    <>
      <p className="lm-eyebrow">A DAY IN THE LIFE OF THE LOOPS</p>
      <div className="lm-steps" aria-hidden="true">{STORY.map((x, i) => <i key={x.who} className={i <= step ? "on" : ""} />)}</div>
      <p className="lm-who">{s.who}</p>
      <p className="lm-story">{s.text}</p>
      <div className="lm-story-ctl">
        <button type="button" className="pa-link" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        <button type="button" onClick={() => setStep(last ? 0 : step + 1)}>{last ? "Start again" : "Next"}</button>
        <span className="pa-muted">{step + 1} / {STORY.length}</span>
      </div>
      <p className="pa-muted">Use the arrow keys too. Select a loop to go back to live numbers.</p>
    </>
  );
}

function Legend() {
  return (
    <>
      <p className="lm-eyebrow">HOW TO READ THE MAP</p>
      <h3>Select any loop</h3>
      <p>The guardian in the middle is the central control. Everything new the owner says, and every family question, passes it first.</p>
      <dl className="lm-legend">
        <dt className="lm-chip lm-acts">Acts within limits</dt><dd>It does the job itself, inside fixed limits. Nothing it makes is final.</dd>
        <dt className="lm-chip lm-proposes">Proposes</dt><dd>It suggests. A person says yes before anything happens.</dd>
        <dt className="lm-chip lm-assists">Assists</dt><dd>It only gives advice. A person makes the decision.</dd>
        <dt className="lm-chip lm-rule">No AI</dt><dd>Fixed rules in code, and people. No model is called.</dd>
        <dt className="lm-chip lm-gatechip">✋ Gate</dt><dd>A person must act before anything moves past this point.</dd>
        <dt className="lm-chip lm-comingchip">Coming</dt><dd>Agreed and on its way (issue #185). Dashed lines are not live yet.</dd>
      </dl>
      <p className="pa-muted">A turning ring means a model call is running now, or a fraud run is in progress. Hover over a loop to see what it connects to. Select it for its numbers.</p>
    </>
  );
}

function Snapshot({ id, ids, byId, live, onClose }: {
  id: string; ids: string[]; byId: Map<string, Agent>; live: AgentsLive | null; onClose: () => void;
}) {
  const n = NODES.find((x) => x.id === id);
  const title = n ? n.title.join(" ") : "Gateway";
  const name = (nid: string) => (NODES.find((x) => x.id === nid)?.title.join(" ") ?? nid).replace(" · central control", "");
  const from = EDGES.filter((e) => e.to === id).map((e) => name(e.from));
  const to = EDGES.filter((e) => e.from === id).map((e) => name(e.to));
  return (
    <>
      <div className="lm-panel-head">
        <h3>{title}</h3>
        <button type="button" className="pa-link" onClick={onClose} aria-label="Close and show how to read the map">Close</button>
      </div>
      {n?.coming && <p className="lm-coming-note">Coming in {n.coming} (issue #185). Not live yet.</p>}
      {n ? (
        <>
          <p>{n.does}</p>
          <dl className="lm-figures">
            <dt>Gets</dt><dd>{n.gets}</dd>
            <dt>Gives</dt><dd>{n.gives}</dd>
            {from.length > 0 && <><dt>From</dt><dd>{[...new Set(from)].join(", ")}</dd></>}
            {to.length > 0 && <><dt>To</dt><dd>{[...new Set(to)].join(", ")}</dd></>}
          </dl>
          <p className="lm-decides"><b>Who decides:</b> {n.decides}</p>
        </>
      ) : (
        <p>Every model call passes here: is the agent registered, is the model allowed, and is there budget left? Anything else is refused and recorded under Blocked calls below.</p>
      )}
      {id === "gateway" && live && (
        <dl className="lm-figures">
          <dt>Kill switch</dt><dd>{fmt.label(live.kill_switch)}</dd>
          <dt>Daily ceiling</dt><dd>{fmt.label(live.ceiling)}</dd>
        </dl>
      )}
      {id === "guardian" && live && (
        <p><a href="#/guardian">{live.guardian_open} open event{live.guardian_open === 1 ? "" : "s"} for the team</a></p>
      )}
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
