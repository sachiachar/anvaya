import { GraphChart } from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";
import { platform, type FraudGraph, type GraphEvent, type GraphGroup, type GraphNode } from "./api";

// The fraud knowledge as a graph, drawn like Obsidian's graph view: a force layout on a dark canvas,
// nodes sized by how connected they are, scroll to zoom, drag to pan or move a node, hover to light up
// its links, click to open the node with its connections and history. Loaded only when the Fraud
// page opens (pages.tsx imports it lazily).
echarts.use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer]);

const GROUPS: { group: GraphGroup; label: string; color: string; shown: boolean }[] = [
  { group: "guide", label: "Guide", color: "#d4af37", shown: true },
  { group: "sign", label: "Warning sign", color: "#5aa0c8", shown: true },
  { group: "tactic", label: "Tactic", color: "#c2577f", shown: true },
  { group: "scenario", label: "Scenario in the guide", color: "#7b68c9", shown: true },
  { group: "safeguard", label: "Safeguard", color: "#3f9e8a", shown: true },
  { group: "source", label: "Source", color: "#8a96ad", shown: true },
  { group: "candidate", label: "Scenario being checked", color: "#a99be0", shown: true },
  { group: "gap", label: "Gap", color: "#e0703f", shown: true },
  { group: "draft", label: "Waiting for review", color: "#e8c766", shown: true },
  { group: "ruled_out", label: "Ruled out", color: "#59606e", shown: false },
];
const INDEX = new Map(GROUPS.map((g, i) => [g.group, i]));
const LABEL = new Map(GROUPS.map((g) => [g.group, g.label]));
const CANVAS = "#0b132b";

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export default function FraudGraphView() {
  const [guide, setGuide] = useState("");
  const [graph, setGraph] = useState<FraudGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [events, setEvents] = useState<GraphEvent[] | null>(null);
  const [query, setQuery] = useState("");
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let live = true;
    platform.fraudGraph(guide || undefined).then((g) => { if (live) { setGraph(g); setError(null); } })
      .catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
  }, [guide]);

  const nodes = useMemo(() => new Map(graph?.nodes.map((n) => [n.id, n]) ?? []), [graph]);
  const degree = useMemo(() => {
    const d = new Map<string, number>();
    for (const l of graph?.links ?? []) for (const id of [l.source, l.target]) d.set(id, (d.get(id) ?? 0) + 1);
    return d;
  }, [graph]);
  const neighbours = useMemo(() => {
    if (!selected || !graph) return [];
    return graph.links.flatMap((l) => l.source === selected.id ? [{ kind: l.kind, node: nodes.get(l.target) }]
      : l.target === selected.id ? [{ kind: `${l.kind} (from)`, node: nodes.get(l.source) }] : [])
      .filter((x): x is { kind: string; node: GraphNode } => !!x.node);
  }, [selected, graph, nodes]);

  // The chart is made once; its data follows the graph.
  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    c.on("click", (e) => {
      if (e.dataType === "node") setSelected((e.data as { node: GraphNode }).node);
    });
    const resize = new ResizeObserver(() => c.resize());
    resize.observe(el.current);
    return () => { resize.disconnect(); c.dispose(); chart.current = null; };
  }, []);

  useEffect(() => {
    if (!chart.current || !graph) return;
    chart.current.setOption({
      backgroundColor: CANVAS,
      textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
      tooltip: {
        confine: true,
        formatter: (p: { dataType: string; data: { node?: GraphNode; kind?: string } }) =>
          p.dataType === "node" && p.data.node ? `${LABEL.get(p.data.node.group)}<br/>${escapeHtml(p.data.node.title)}` : escapeHtml(p.data.kind ?? ""),
      },
      legend: {
        type: "scroll", bottom: 8, left: 8, right: 8, textStyle: { color: "#c9d3e6" }, icon: "circle", inactiveColor: "#3a4258",
        data: GROUPS.map((g) => g.label),
        selected: Object.fromEntries(GROUPS.map((g) => [g.label, g.shown])),
      },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true, animationDuration: 600,
        categories: GROUPS.map((g) => ({ name: g.label, itemStyle: { color: g.color } })),
        force: { repulsion: 240, edgeLength: [50, 140], gravity: 0.08, friction: 0.15 },
        label: { show: true, position: "right", color: "#e6ebf5", fontSize: 11, formatter: "{b}" },
        labelLayout: { hideOverlap: true },
        lineStyle: { color: "#4b5878", width: 1, curveness: 0.08, opacity: 0.7 },
        emphasis: { focus: "adjacency", lineStyle: { width: 2, color: "#d4af37", opacity: 1 }, label: { show: true } },
        blur: { itemStyle: { opacity: 0.15 }, lineStyle: { opacity: 0.05 }, label: { show: false } },
        data: graph.nodes.map((n) => {
          const d = degree.get(n.id) ?? 0;
          const retired = n.status === "retired";
          return {
            id: n.id, name: n.label, category: INDEX.get(n.group) ?? 0, node: n,
            symbolSize: n.group === "guide" ? 30 : Math.min(6 + Math.sqrt(d) * 4, 24),
            label: { show: n.group === "guide" || d >= 4 },
            itemStyle: retired ? { opacity: 0.35 } : n.auto ? { borderColor: "#f7f5ef", borderWidth: 1.5 } : undefined,
          };
        }),
        links: graph.links.map((l) => ({ source: l.source, target: l.target, kind: l.kind,
          lineStyle: l.kind === "imagined from" ? { type: "dashed" } : undefined })),
      }],
    }, { notMerge: true });
  }, [graph, degree]);

  // The selected node is lit up in the chart, and its history is loaded.
  useEffect(() => {
    const c = chart.current;
    setEvents(null);
    if (!c || !graph) return;
    c.dispatchAction({ type: "downplay", seriesIndex: 0 });
    if (!selected) return;
    const index = graph.nodes.findIndex((n) => n.id === selected.id);
    if (index >= 0) c.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex: index });
    if (!selected.history) return;
    let live = true;
    platform.fraudHistory(selected.history).then((r) => live && setEvents(r.events)).catch(() => live && setEvents([]));
    return () => { live = false; };
  }, [selected, graph]);

  const find = (text: string) => {
    const q = text.trim().toLowerCase();
    if (!q || !graph) return;
    const hit = graph.nodes.find((n) => n.title.toLowerCase().includes(q));
    if (hit) setSelected(hit);
  };

  return (
    <div className="fg">
      <div className="fg-tools">
        <label>
          <span className="pa-muted">Guide</span>
          <select value={guide} onChange={(e) => { setGuide(e.target.value); setSelected(null); }}>
            <option value="">All guides</option>
            {graph?.guides.map((g) => <option key={g.id} value={g.id}>{g.title}</option>)}
          </select>
        </label>
        <form className="fg-find" onSubmit={(e) => { e.preventDefault(); find(query); }}>
          <label>
            <span className="pa-muted">Find a node</span>
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. seal" />
          </label>
          <button type="submit" className="pa-small">Find</button>
        </form>
        {graph && <span className="pa-muted">{graph.nodes.length} nodes · {graph.links.length} links</span>}
      </div>
      {error && <p className="pa-note" role="alert">{error}</p>}
      <div className="fg-body">
        <div ref={el} className="fg-canvas" role="img"
          aria-label="Graph of the fraud knowledge. Use Find a node to open a node and its connections." />
        <aside className="fg-panel" aria-live="polite">
          {!selected ? (
            <p className="pa-muted">Click a node, or find one, to see what it is, what it connects to and its history. Scroll to zoom; drag to move. Click a colour in the legend to show or hide that kind.</p>
          ) : (
            <>
              <p className="fg-kind"><span className="fg-dot" style={{ background: GROUPS[INDEX.get(selected.group) ?? 0].color }} />
                {LABEL.get(selected.group)}{selected.auto ? " · added on its own" : ""}{selected.status ? ` · ${selected.status}` : ""}</p>
              <h3>{selected.title}</h3>
              {selected.text && <p className="fg-text">{selected.text}</p>}
              {selected.coverage && <p className="pa-muted">Coverage: {selected.coverage}</p>}
              {selected.cue && <p className="pa-muted">Starts from a common gap that {selected.cue.owners} owners share (counted, never read).</p>}
              {selected.url && /^https?:\/\//i.test(selected.url) && <p><a href={selected.url} target="_blank" rel="noopener noreferrer">Open the source</a></p>}
              <h4>Connections ({neighbours.length})</h4>
              <ul className="fg-links">
                {neighbours.map(({ kind, node }) => (
                  <li key={`${kind}-${node.id}`}>
                    <span className="pa-muted">{kind}</span>
                    <button type="button" className="fg-link" onClick={() => setSelected(node)}>{node.label}</button>
                  </li>
                ))}
              </ul>
              {selected.history && (
                <>
                  <h4>History</h4>
                  {events === null ? <p className="pa-muted">Loading…</p> : events.length === 0 ? <p className="pa-muted">No changes recorded.</p> : (
                    <ol className="fg-history">
                      {events.map((e) => (
                        <li key={`${e.at}-${e.action}`}>{e.action}{e.version ? `, guide version ${e.version}` : ""} · {when(e.at)}</li>
                      ))}
                    </ol>
                  )}
                </>
              )}
              <button type="button" className="pa-small" onClick={() => setSelected(null)}>Close</button>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function escapeHtml(text: string) {
  return text.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch] ?? ch);
}
