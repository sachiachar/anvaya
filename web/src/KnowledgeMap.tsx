import { useEffect, useMemo, useRef, useState } from "react";
import { api, type MapNode, type KnowledgeMap as Graph } from "./api";

// The knowledge map, for the demo: the owner's kept knowledge as a graph, filling in live as
// they tap Keep. Opened in its own window (/#knowledge-map) next to the conversation.

const POLL_MS = 2000;
const GLIDE_MS = 700;
const NEW_FOR_MS = 4000;

const RADIUS = { owner: 0, person: 170, mentioned: 170, story: 320, wish: 320, account: 320, institution: 450 } as const;
const SIZE = { owner: 40, person: 24, mentioned: 18, story: 16, wish: 16, account: 18, institution: 20 } as const;
const LEGEND: { group: MapNode["group"]; label: string }[] = [
  { group: "person", label: "Person kept" },
  { group: "mentioned", label: "Mentioned, not kept yet" },
  { group: "story", label: "Story, advice or fact" },
  { group: "wish", label: "Wish" },
  { group: "account", label: "Account or policy" },
  { group: "institution", label: "Bank or insurer" },
];

type Point = { x: number; y: number };

/** Where each node sits: rings by kind, in a stable order, institutions beside their accounts. */
function layout(g: Graph): Record<string, Point> {
  const at = (r: number, angle: number): Point => ({ x: r * Math.cos(angle), y: r * Math.sin(angle) });
  const pos: Record<string, Point> = { owner: { x: 0, y: 0 } };
  const ring = (nodes: MapNode[], r: number, offset: number) => {
    nodes.forEach((n, i) => { pos[n.id] = at(r, offset + (2 * Math.PI * i) / Math.max(nodes.length, 1)); });
  };
  const byKept = (a: MapNode, b: MapNode) => (a.kept_at ?? "").localeCompare(b.kept_at ?? "") || a.id.localeCompare(b.id);
  ring(g.nodes.filter((n) => n.group === "person" || n.group === "mentioned").sort((a, b) =>
    a.group.localeCompare(b.group) || a.id.localeCompare(b.id)), RADIUS.person, -Math.PI / 2);
  ring(g.nodes.filter((n) => ["story", "wish", "account"].includes(n.group)).sort((a, b) =>
    a.group.localeCompare(b.group) || byKept(a, b)), RADIUS.story, -Math.PI / 2 + 0.2);
  const institutions = g.nodes.filter((n) => n.group === "institution");
  institutions.forEach((n, i) => {
    const accounts = g.edges.filter((e) => e.to === n.id).map((e) => pos[e.from]).filter(Boolean);
    const angle = accounts.length
      ? Math.atan2(accounts.reduce((s, p) => s + p.y, 0), accounts.reduce((s, p) => s + p.x, 0))
      : (2 * Math.PI * i) / institutions.length;
    pos[n.id] = at(RADIUS.institution, angle);
  });
  return pos;
}

/** Positions that glide to their new places when the graph changes (or jump, for reduced motion). */
function useGlide(target: Record<string, Point>): Record<string, Point> {
  const [shown, setShown] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const origin = from.current;
    let frame = 0;
    let start: number | null = null;  // the first frame's own clock: performance.now() can differ from it
    const step = (now: number) => {
      start ??= now;
      const t = reduce ? 1 : Math.min(1, Math.max(0, (now - start) / GLIDE_MS));
      const ease = 1 - (1 - t) ** 3;
      const next: Record<string, Point> = {};
      for (const [id, p] of Object.entries(target)) {
        const o = origin[id] ?? { x: p.x * 0.6, y: p.y * 0.6 }; // new nodes grow out from the centre
        next[id] = { x: o.x + (p.x - o.x) * ease, y: o.y + (p.y - o.y) * ease };
      }
      from.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return shown;
}

const short = (s: string, n = 26) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function KnowledgeMap() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const firstSeen = useRef<Record<string, number>>({});
  const [, tick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.knowledgeMap().then((g) => {
        if (!alive) return;
        const now = Date.now();
        const initial = Object.keys(firstSeen.current).length === 0;
        for (const n of g.nodes) firstSeen.current[n.id] ??= initial ? 0 : now;
        setGraph(g);
        setError(null);
      }).catch((e) => alive && setError(String(e.message ?? e)));
    };
    load();
    const t = window.setInterval(load, POLL_MS);
    const glow = window.setInterval(() => tick((x) => x + 1), 1000);  // let the "new" glow fade
    return () => { alive = false; window.clearInterval(t); window.clearInterval(glow); };
  }, []);

  const target = useMemo(() => (graph ? layout(graph) : {}), [graph]);
  const pos = useGlide(target);
  if (!graph) return <p className="muted">{error ?? "Loading the knowledge map…"}</p>;

  const gapFor = new Map(graph.gaps.map((g) => [g.node, g.missing]));
  const isNew = (id: string) => Date.now() - (firstSeen.current[id] ?? 0) < NEW_FOR_MS;
  const chosen = graph.nodes.find((n) => n.id === selected);
  const owner = graph.nodes.find((n) => n.group === "owner");

  return (
    <div className="kmap">
      <header className="kmap-head">
        <div>
          <h1>{owner?.label}'s knowledge map</h1>
          <p className="muted">
            <span className="kmap-live" aria-hidden="true" /> Live · each item appears when it's kept · demo, synthetic data
          </p>
        </div>
        <dl className="kmap-counts">
          <div><dt>Kept</dt><dd>{graph.counts.concepts}</dd></div>
          <div><dt>Links</dt><dd>{graph.counts.links}</dd></div>
          <div><dt>Gaps</dt><dd className={graph.counts.gaps ? "is-gap" : undefined}>{graph.counts.gaps}</dd></div>
        </dl>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="kmap-body">
        {/* Clicking a circle is a mouse shortcut; keyboard and screen-reader users choose items
            from the "Everything kept" list beside the map, which does the same thing. */}
        <svg className="kmap-graph" viewBox="-600 -560 1200 1120" role="img"
          aria-label={`Knowledge map with ${graph.counts.concepts} kept items, ${graph.counts.links} links and ${graph.counts.gaps} gaps`}
          onClick={(ev) => {
            const id = (ev.target as Element).closest("[data-node]")?.getAttribute("data-node");
            if (id) setSelected(id === selected ? null : id);
          }}
          onKeyDown={(ev) => { if (ev.key === "Escape") setSelected(null); }}>
          {[RADIUS.person, RADIUS.story, RADIUS.institution].map((r) => <circle key={r} className="kmap-ring" r={r} />)}
          {graph.edges.map((e) => {
            const a = pos[e.from];
            const b = pos[e.to];
            if (!a || !b) return null;
            const lit = selected && (e.from === selected || e.to === selected);
            return (
              <g key={`${e.from}-${e.to}-${e.label}`} className={`kmap-edge${lit ? " is-lit" : ""}${e.derived ? "" : " is-typed"}`}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                {lit && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}>{e.label}</text>}
              </g>
            );
          })}
          {graph.nodes.map((n) => {
            const p = pos[n.id];
            if (!p) return null;
            const r = SIZE[n.group];
            const gap = gapFor.get(n.id);
            return (
              <g key={n.id} data-node={n.id} transform={`translate(${p.x} ${p.y})`}
                className={`kmap-node is-${n.group}${isNew(n.id) ? " is-new" : ""}${selected === n.id ? " is-selected" : ""}`}>
                {isNew(n.id) && <circle className="kmap-glow" r={r + 10} />}
                {gap && <circle className="kmap-gap" r={r + 6} />}
                <circle r={r} />
                <text y={r + 20}>{short(n.label)}</text>
                {gap && <text className="kmap-gap-text" y={r + 38}>No {gap} recorded</text>}
              </g>
            );
          })}
        </svg>
        <aside className="kmap-side">
          {chosen ? (
            <section>
              <h2>{chosen.label}</h2>
              <p className="muted">{chosen.type}{chosen.verified ? " · confirmed by the owner" : ""}</p>
              {chosen.description && <p>{chosen.description}</p>}
              <ul className="kmap-links">
                {graph.edges.filter((e) => e.from === chosen.id || e.to === chosen.id).map((e) => {
                  const other = graph.nodes.find((n) => n.id === (e.from === chosen.id ? e.to : e.from));
                  return <li key={`${e.from}-${e.to}-${e.label}`}><em>{e.label}</em> {other?.label}</li>;
                })}
              </ul>
              <button className="link" onClick={() => setSelected(null)}>Close</button>
            </section>
          ) : (
            <section>
              <h2>Just kept</h2>
              {graph.recent.length === 0 ? <p className="muted">Nothing yet. Talk to Saathi and tap Keep.</p> : (
                <ol className="kmap-feed" aria-live="polite">
                  {graph.recent.slice(0, 10).map((r) => (
                    <li key={`${r.at}-${r.id}-${r.action}`}>
                      <time>{time(r.at)}</time> {r.action === "Update" ? "Updated" : r.action === "Deprecation" ? "Replaced" : "Kept"}: {r.title}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
          <section>
            <h2>Everything kept</h2>
            <ul className="kmap-items">
              {graph.nodes.filter((n) => n.group !== "owner").map((n) => (
                <li key={n.id}>
                  <button className="link" aria-pressed={selected === n.id} onClick={() => setSelected(n.id === selected ? null : n.id)}>
                    {n.label}
                  </button>
                  <span className="muted"> · {n.type}{gapFor.get(n.id) ? ` · no ${gapFor.get(n.id)} recorded` : ""}</span>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2>Key</h2>
            <ul className="kmap-legend">
              {LEGEND.map((l) => <li key={l.group} className={`is-${l.group}`}><svg viewBox="-10 -10 20 20" aria-hidden="true"><circle r={7} /></svg>{l.label}</li>)}
              <li className="is-gapkey"><svg viewBox="-10 -10 20 20" aria-hidden="true"><circle r={7} /></svg>Something missing</li>
            </ul>
            <p className="muted">Lines show who and what each kept item mentions. Tap a circle to see its links.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
