import { useEffect, useRef, useState } from "react";
import { api, type MapNode, type KnowledgeMap as Graph } from "./api";

// The knowledge map, for the demo: the owner's kept knowledge as a graph that fills in live as
// they tap Keep, drawn like Obsidian's graph view: a force layout on a dark canvas, nodes sized by
// how connected they are, scroll to zoom, drag to pan or move a node, hover to light up its links.
// Opened in its own window (/#knowledge-map) next to the conversation. No graph library: the
// small force simulation below is enough for a few hundred nodes.

const POLL_MS = 2000;
const NEW_FOR_MS = 4000;
const LINK_LENGTH = 120;
const REPULSION = 7000;
const VIEW = { w: 1000, h: 760 };  // the canvas's viewBox, centred on 0,0

const LEGEND: { group: MapNode["group"]; label: string }[] = [
  { group: "owner", label: "Owner" },
  { group: "person", label: "Person kept" },
  { group: "mentioned", label: "Mentioned, not kept yet" },
  { group: "story", label: "Story, advice or fact" },
  { group: "wish", label: "Wish" },
  { group: "account", label: "Account or policy" },
  { group: "institution", label: "Bank or insurer" },
];

type Body = { x: number; y: number; vx: number; vy: number; held?: boolean };
type View = { x: number; y: number; k: number };

const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Moves the bodies one step: nodes push apart, links pull together, everything drifts to the centre. */
function tick(bodies: Map<string, Body>, g: Graph, alpha: number) {
  const list = [...bodies.entries()];
  for (let i = 0; i < list.length; i++) {
    const [, a] = list[i];
    for (let j = i + 1; j < list.length; j++) {
      const [, b] = list[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = Math.max(dx * dx + dy * dy, 30);
      const f = (REPULSION * alpha) / d2;
      const d = Math.sqrt(d2);
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
  }
  for (const e of g.edges) {
    const a = bodies.get(e.from);
    const b = bodies.get(e.to);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.max(Math.hypot(dx, dy), 1);
    const f = ((d - LINK_LENGTH) / d) * 0.08 * alpha;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }
  for (const [id, b] of list) {
    const pull = id === "owner" ? 0.06 : 0.012;
    b.vx -= b.x * pull * alpha;
    b.vy -= b.y * pull * alpha;
    if (b.held) { b.vx = 0; b.vy = 0; continue; }
    b.vx *= 0.82; b.vy *= 0.82;
    b.x += b.vx; b.y += b.vy;
  }
}

export default function KnowledgeMap() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);  // kept until the next try: refreshes don't clear it
  const [hovered, setHovered] = useState<string | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [, redraw] = useState(0);
  const bodies = useRef(new Map<string, Body>());
  const alpha = useRef(1);
  const firstSeen = useRef<Record<string, number>>({});
  const svgRef = useRef<SVGSVGElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const userMoved = useRef(false);  // once someone zooms or pans, the view stays where they put it

  // Live data: one request at a time; a new layout only when something changed.
  useEffect(() => {
    let alive = true;
    let busy = false;
    let last = "";
    const load = () => {
      if (document.hidden || busy) return;
      busy = true;
      api.knowledgeMap().then((g) => {
        if (!alive) return;
        setError(null);
        const text = JSON.stringify(g);
        if (text === last) return;
        last = text;
        const now = Date.now();
        const initial = Object.keys(firstSeen.current).length === 0;
        for (const n of g.nodes) {
          firstSeen.current[n.id] ??= initial ? 0 : now;
          if (bodies.current.has(n.id)) continue;
          // A new node starts beside something it links to, so it grows out of its neighbours.
          const edge = g.edges.find((e) => (e.from === n.id && bodies.current.has(e.to)) || (e.to === n.id && bodies.current.has(e.from)));
          const near = edge ? bodies.current.get(edge.from === n.id ? edge.to : edge.from) : undefined;
          const angle = Math.random() * 2 * Math.PI;
          const r = n.id === "owner" ? 0 : near ? 30 : 60 + Math.random() * 60;
          bodies.current.set(n.id, { x: (near?.x ?? 0) + r * Math.cos(angle), y: (near?.y ?? 0) + r * Math.sin(angle), vx: 0, vy: 0 });
        }
        for (const id of [...bodies.current.keys()]) if (!g.nodes.some((n) => n.id === id)) bodies.current.delete(id);
        graphRef.current = g;
        alpha.current = Math.max(alpha.current, initial ? 1 : 0.5);
        setGraph(g);
      }).catch((e) => alive && setError(String(e.message ?? e))).finally(() => { busy = false; });
    };
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  // Zoom to fit: the view eases towards the whole graph, with room for labels.
  const fit = (ease: number) => {
    const all = [...bodies.current.values()];
    if (all.length === 0) return;
    const xs = all.map((b) => b.x);
    const ys = all.map((b) => b.y);
    const [x0, x1, y0, y1] = [Math.min(...xs) - 90, Math.max(...xs) + 90, Math.min(...ys) - 50, Math.max(...ys) + 60];
    const k = Math.min(2.2, Math.max(0.3, Math.min(VIEW.w / (x1 - x0), VIEW.h / (y1 - y0))));
    const target = { k, x: -k * (x0 + x1) / 2, y: -k * (y0 + y1) / 2 };
    const v = viewRef.current;
    if (Math.abs(v.k - target.k) < 0.002 && Math.abs(v.x - target.x) < 0.5 && Math.abs(v.y - target.y) < 0.5) return;
    setView({ k: v.k + (target.k - v.k) * ease, x: v.x + (target.x - v.x) * ease, y: v.y + (target.y - v.y) * ease });
  };

  // The simulation: runs while it has energy (or a node is held), then rests. Reduced motion
  // settles it at once.
  useEffect(() => {
    let frame = 0;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const loop = () => {
      const g = graphRef.current;
      const held = [...bodies.current.values()].some((b) => b.held);
      if (g && (alpha.current > 0.01 || held)) {
        const steps = reduce ? 300 : 1;
        for (let i = 0; i < steps; i++) {
          tick(bodies.current, g, Math.max(alpha.current, held ? 0.15 : 0));
          alpha.current *= 0.985;
        }
        redraw((x) => x + 1);
      }
      if (g && !userMoved.current) fit(reduce ? 1 : 0.12);
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    const glow = window.setInterval(() => redraw((x) => x + 1), 1000);  // let the "new" glow fade
    return () => { cancelAnimationFrame(frame); window.clearInterval(glow); };
  }, []);

  // Zoom, pan, drag and hover, as native listeners on the canvas (wheel must not scroll the page).
  const ready = graph !== null;
  useEffect(() => {
    const svg = svgRef.current;
    if (!ready || !svg) return;
    const point = (ev: { clientX: number; clientY: number }) => {
      const m = svg.getScreenCTM();
      if (!m) return { x: 0, y: 0 };
      const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
      return { x: p.x, y: p.y };
    };
    const world = (p: { x: number; y: number }) => {
      const v = viewRef.current;
      return { x: (p.x - v.x) / v.k, y: (p.y - v.y) / v.k };
    };
    const nodeOf = (target: EventTarget | null) => (target as Element | null)?.closest?.("[data-node]")?.getAttribute("data-node") ?? null;
    let drag: { id: string | null; start: { x: number; y: number }; view: View; moved: boolean } | null = null;

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      userMoved.current = true;
      const p = point(ev);
      setView((v) => {
        const k = Math.min(3, Math.max(0.3, v.k * Math.exp(-ev.deltaY * 0.0015)));
        return { k, x: p.x - ((p.x - v.x) * k) / v.k, y: p.y - ((p.y - v.y) * k) / v.k };
      });
    };
    const onDown = (ev: PointerEvent) => {
      const id = nodeOf(ev.target);
      drag = { id, start: point(ev), view: viewRef.current, moved: false };
      if (id) { const b = bodies.current.get(id); if (b) b.held = true; }
      svg.setPointerCapture(ev.pointerId);
    };
    const onMove = (ev: PointerEvent) => {
      if (!drag) { setHovered(nodeOf(ev.target)); return; }
      const p = point(ev);
      if (Math.hypot(p.x - drag.start.x, p.y - drag.start.y) > 3) drag.moved = true;
      if (drag.id) {
        const b = bodies.current.get(drag.id);
        if (b) { const w = world(p); b.x = w.x; b.y = w.y; }
        alpha.current = Math.max(alpha.current, 0.2);
      } else if (drag.moved) {
        userMoved.current = true;
        const d = drag;
        setView({ ...d.view, x: d.view.x + p.x - d.start.x, y: d.view.y + p.y - d.start.y });
      }
    };
    const onUp = () => {
      if (!drag) return;
      if (drag.id) {
        const b = bodies.current.get(drag.id);
        if (b) b.held = false;
        const id = drag.id;
        if (!drag.moved) setSelected((s) => (s === id ? null : id));
      } else if (!drag.moved) {
        setSelected(null);
      }
      drag = null;
    };
    const onLeave = () => setHovered(null);
    svg.addEventListener("wheel", onWheel, { passive: false });
    svg.addEventListener("pointerdown", onDown);
    svg.addEventListener("pointermove", onMove);
    svg.addEventListener("pointerup", onUp);
    svg.addEventListener("pointercancel", onUp);
    svg.addEventListener("pointerleave", onLeave);
    return () => {
      svg.removeEventListener("wheel", onWheel);
      svg.removeEventListener("pointerdown", onDown);
      svg.removeEventListener("pointermove", onMove);
      svg.removeEventListener("pointerup", onUp);
      svg.removeEventListener("pointercancel", onUp);
      svg.removeEventListener("pointerleave", onLeave);
    };
  }, [ready]);

  if (!graph) return <p className="muted">{error ?? "Loading the knowledge map…"}</p>;

  const gapFor = new Map(graph.gaps.map((g) => [g.node, g.missing]));
  const degree = new Map<string, number>();
  for (const e of graph.edges) {
    degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
    degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
  }
  const radius = (n: MapNode) => (n.group === "owner" ? 13 : 4 + 2.2 * Math.sqrt(degree.get(n.id) ?? 0));
  const focus = hovered ?? selected;
  const neighbours = new Set<string>(focus ? [focus] : []);
  if (focus) for (const e of graph.edges) {
    if (e.from === focus) neighbours.add(e.to);
    if (e.to === focus) neighbours.add(e.from);
  }
  const dim = (id: string) => focus !== null && !neighbours.has(id);
  const isNew = (id: string) => Date.now() - (firstSeen.current[id] ?? 0) < NEW_FOR_MS;
  const chosen = graph.nodes.find((n) => n.id === selected);
  const owner = graph.nodes.find((n) => n.group === "owner");

  return (
    <div className="kmap">
      <header className="kmap-head">
        <div>
          <h1>{owner?.label}'s knowledge map</h1>
          <p className="muted">
            <span className="kmap-live" aria-hidden="true" /> Live · each item appears when it's kept · scroll to zoom, drag to move · demo, synthetic data
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
        <svg ref={svgRef} className="kmap-graph" viewBox={`${-VIEW.w / 2} ${-VIEW.h / 2} ${VIEW.w} ${VIEW.h}`} role="img"
          aria-label={`Knowledge map with ${graph.counts.concepts} kept items, ${graph.counts.links} links and ${graph.counts.gaps} gaps. Choose items from the list beside it.`}>
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            {graph.edges.map((e) => {
              const a = bodies.current.get(e.from);
              const b = bodies.current.get(e.to);
              if (!a || !b) return null;
              const lit = focus !== null && (e.from === focus || e.to === focus);
              return (
                <g key={`${e.from}-${e.to}-${e.label}`} className={`kmap-edge${lit ? " is-lit" : ""}${focus && !lit ? " is-dim" : ""}`}>
                  <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                  {lit && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}>{e.label}</text>}
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const b = bodies.current.get(n.id);
              if (!b) return null;
              const r = radius(n);
              const gap = gapFor.get(n.id);
              const showLabel = view.k > 0.65 || neighbours.has(n.id) || n.group === "owner";
              return (
                <g key={n.id} data-node={n.id} transform={`translate(${b.x} ${b.y})`}
                  className={`kmap-node is-${n.group}${focus === n.id ? " is-focus" : ""}${dim(n.id) ? " is-dim" : ""}`}>
                  {isNew(n.id) && <circle className="kmap-glow" r={r + 9} />}
                  {gap && <circle className="kmap-gap" r={r + 5} />}
                  <circle className="kmap-dot" r={r} />
                  {showLabel && <text y={r + 13}>{n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}</text>}
                  {gap && showLabel && <text className="kmap-gap-text" y={r + 26}>No {gap} recorded</text>}
                </g>
              );
            })}
          </g>
        </svg>
        <aside className="kmap-side">
          {chosen ? (
            <section>
              <h2>{chosen.label}</h2>
              <p className="muted">{chosen.type}{chosen.verified ? " · confirmed by the owner" : ""}</p>
              {chosen.description && <p>{chosen.description}</p>}
              {gapFor.get(chosen.id) && <p className="error">No {gapFor.get(chosen.id)} recorded</p>}
              <ul className="kmap-links">
                {graph.edges.filter((e) => e.from === chosen.id || e.to === chosen.id).map((e) => {
                  const other = graph.nodes.find((n) => n.id === (e.from === chosen.id ? e.to : e.from));
                  return <li key={`${e.from}-${e.to}-${e.label}`}><em>{e.label}</em> {other?.label}</li>;
                })}
              </ul>
              {!["owner", "institution", "mentioned"].includes(chosen.group) && (
                <p>
                  <button className="secondary" onClick={() => {
                    if (!window.confirm(`Remove "${chosen.label}"? It's removed for good from what your family receives.`)) return;
                    setRemoveError(null);
                    api.removeKept(chosen.id).then(() => setSelected(null)).catch((e) => setRemoveError(String(e.message ?? e)));
                  }}>Remove, it's wrong</button>
                </p>
              )}
              {removeError && <p className="error" role="alert">{removeError}</p>}
              <button className="link" onClick={() => setSelected(null)}>Close</button>
            </section>
          ) : (
            <section>
              <h2>Just kept</h2>
              {graph.recent.length === 0 ? <p className="muted">Nothing yet. Talk to Saathi and tap Keep.</p> : (
                <ol className="kmap-feed" aria-live="polite">
                  {graph.recent.slice(0, 8).map((r) => (
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
              {LEGEND.map((l) => <li key={l.group} className={`is-${l.group}`}><svg viewBox="-10 -10 20 20" aria-hidden="true"><circle className="kmap-dot" r={6} /></svg>{l.label}</li>)}
              <li className="is-gapkey"><svg viewBox="-10 -10 20 20" aria-hidden="true"><circle r={7} /></svg>Something missing</li>
            </ul>
            <p className="muted">Bigger circles have more links. Hover or tap one to light up its links.</p>
            <button className="link" onClick={() => { userMoved.current = false; }}>Fit the whole map</button>
          </section>
        </aside>
      </div>
    </div>
  );
}
