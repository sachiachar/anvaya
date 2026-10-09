import { BarChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt, type ServiceCeiling, type Use } from "./api";

// Only what these two charts use, so the portal loads as little as it can. This file is loaded
// only when the dashboard opens (pages.tsx imports it lazily).
echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer]);

type Metric = "tokens" | "minutes" | "calls";
type Row = { agents: Record<string, Use> };
export type UsageSeries = {
  hourly: ({ hour: string } & Row)[];
  daily_by_agent: ({ day: string } & Row)[];
};

const METRICS: Record<Metric, { label: string; unit: string }> = {
  tokens: { label: "Tokens", unit: "tokens" },
  minutes: { label: "Voice minutes", unit: "minutes" },
  calls: { label: "Calls", unit: "calls" },
};
const HOURS_SHOWN = 48;
const HOURS_KEPT = 14 * 24;
const DAYS_SHOWN = 30;
const CEILING_SHOWN_FROM = 0.25; // the ceiling line shows once a day reaches this share of it
const TOP_AGENTS = 7; // the rest are summed as "other agents", so the legend stays readable
// One colour per agent, the same in both charts; readable on the light and the dark background.
const PALETTE = ["#4f7ccf", "#d4a03a", "#3f9e8a", "#c2577f", "#7b68c9", "#d9774a", "#5aa0c8", "#8a9a5b"];

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const value = (u: Use | undefined, m: Metric) => (u ? u[m] : 0);
const pad = (n: number) => String(n).padStart(2, "0");
const utcHour = (d: Date) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}`;
const utcDay = (d: Date) => utcHour(d).slice(0, 10);

/** An ECharts chart that follows the page's light or dark theme and its width. */
function EChart({ option, height, label }: { option: EChartsCoreOption; height: number; label: string }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const [dark, setDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const change = () => setDark(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, dark ? "dark" : undefined, { renderer: "canvas" });
    chart.current = c;
    const resize = new ResizeObserver(() => c.resize());
    resize.observe(el.current);
    return () => { resize.disconnect(); c.dispose(); chart.current = null; };
  }, [dark]);

  useEffect(() => { chart.current?.setOption(option, { notMerge: true }); }, [option, dark]);

  return <div ref={el} className="uc-chart" style={{ height }} role="img" aria-label={label} />;
}

/** The agents with the most use first; beyond TOP_AGENTS they become one "other agents" series. */
function seriesFor(rows: Row[], m: Metric) {
  const totals = new Map<string, number>();
  for (const r of rows) for (const [a, u] of Object.entries(r.agents)) totals.set(a, (totals.get(a) ?? 0) + value(u, m));
  const ranked = [...totals.entries()].filter(([, t]) => t > 0).sort((x, y) => y[1] - x[1]).map(([a]) => a);
  const top = ranked.slice(0, TOP_AGENTS);
  const rest = ranked.slice(TOP_AGENTS);
  const names = rest.length ? [...top, "other agents"] : top;
  return names.map((name, i) => ({
    name, type: "bar", stack: "use", barMaxWidth: 28, emphasis: { focus: "series" },
    itemStyle: { color: PALETTE[i % PALETTE.length], borderRadius: 0 },
    data: rows.map((r) => (name === "other agents" ? rest.reduce((s, a) => s + value(r.agents[a], m), 0) : value(r.agents[name], m))),
  }));
}

function baseOption(m: Metric) {
  const text = css("--pa-muted") || "#4a5568";
  const line = css("--pa-line") || "#d9dee8";
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: text },
    grid: { left: 8, right: 16, top: 44, bottom: 64, containLabel: true },
    legend: { type: "scroll", top: 0, textStyle: { color: text }, icon: "roundRect" },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" }, confine: true, order: "valueDesc",
      valueFormatter: (v: number) => (m === "minutes" ? `${(Math.round(v * 10) / 10).toString()} min` : fmt.num(v)),
    },
    yAxis: { type: "value", axisLabel: { color: text, formatter: (v: number) => compact(v) },
      splitLine: { lineStyle: { color: line } } },
  };
}

const compact = (v: number) => (v >= 1e6 ? `${Math.round(v / 1e5) / 10}M` : v >= 1e3 ? `${Math.round(v / 100) / 10}k` : String(Math.round(v * 10) / 10));

export default function UsageCharts({ usage, ceiling }: { usage: UsageSeries; ceiling: ServiceCeiling }) {
  const [metric, setMetric] = useState<Metric>("tokens");

  // Every hour of the last 14 days, empty hours included, in the reader's own time zone.
  const hours = useMemo(() => {
    const byHour = new Map(usage.hourly.map((h) => [h.hour, h]));
    const now = new Date();
    now.setUTCMinutes(0, 0, 0);
    return Array.from({ length: HOURS_KEPT }, (_, i) => {
      const d = new Date(now.getTime() - (HOURS_KEPT - 1 - i) * 3600_000);
      return { at: d, agents: byHour.get(utcHour(d))?.agents ?? {} };
    });
  }, [usage.hourly]);

  // Every day of the last 30 days, empty days included (days are in UTC, like the daily ceiling).
  const days = useMemo(() => {
    const byDay = new Map(usage.daily_by_agent.map((d) => [d.day, d]));
    const today = new Date();
    return Array.from({ length: DAYS_SHOWN }, (_, i) => {
      const d = new Date(today.getTime() - (DAYS_SHOWN - 1 - i) * 86400_000);
      return { day: utcDay(d), agents: byDay.get(utcDay(d))?.agents ?? {} };
    });
  }, [usage.daily_by_agent]);

  const hourlyOption = useMemo(() => {
    const base = baseOption(metric);
    const series = seriesFor(hours, metric);
    return {
      ...base,
      xAxis: {
        type: "category", data: hours.map((h) => h.at.toISOString()),
        axisLabel: { color: base.textStyle.color, hideOverlap: true, formatter: (iso: string) => {
          const d = new Date(iso);
          return d.getHours() === 0 ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : `${pad(d.getHours())}:00`;
        } },
        axisPointer: { label: { formatter: ({ value: iso }: { value: string }) =>
          new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) } },
      },
      // The last 48 hours first; drag the slider back up to 14 days.
      dataZoom: [{ type: "inside", startValue: HOURS_KEPT - HOURS_SHOWN, endValue: HOURS_KEPT - 1 },
        { type: "slider", startValue: HOURS_KEPT - HOURS_SHOWN, endValue: HOURS_KEPT - 1, height: 22, bottom: 8 }],
      series,
    };
  }, [hours, metric]);

  const ceilingLimit = metric === "tokens" ? ceiling.tokens?.limit : metric === "minutes" ? ceiling.voice_minutes?.limit : undefined;
  const peakDay = Math.max(0, ...days.map((d) => Object.values(d.agents).reduce((t, u) => t + value(u, metric), 0)));

  const dailyOption = useMemo(() => {
    const base = baseOption(metric);
    const series = seriesFor(days, metric);
    // The service's daily ceiling as a line, for the measure it limits.
    // Drawn only when a day comes near it: far above every bar, it would flatten them all.
    const near = ceilingLimit && peakDay >= CEILING_SHOWN_FROM * ceilingLimit;
    const limit = near ? ceilingLimit : undefined;
    if (limit && series.length) {
      Object.assign(series[series.length - 1], { markLine: {
        symbol: "none", silent: true, data: [{ yAxis: limit }],
        lineStyle: { color: css("--pa-danger") || "#9a3b1c", type: "dashed" },
        label: { formatter: `Daily ceiling ${compact(limit)}`, position: "insideEndTop", color: css("--pa-danger") || "#9a3b1c" },
      } });
    }
    return {
      ...base,
      xAxis: { type: "category", data: days.map((d) => d.day),
        axisLabel: { color: base.textStyle.color, hideOverlap: true, formatter: (day: string) =>
          new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" }) } },
      yAxis: { ...base.yAxis, max: limit ? (v: { max: number }) => Math.max(v.max, limit * 1.05) : undefined },
      grid: { ...base.grid, bottom: 24 },
      series,
    };
  }, [days, metric, ceilingLimit, peakDay]);

  const sum = (rows: Row[]) => rows.reduce((s, r) => s + Object.values(r.agents).reduce((t, u) => t + value(u, metric), 0), 0);
  const last48 = sum(hours.slice(-HOURS_SHOWN));
  const last30 = sum(days);
  const unit = METRICS[metric].unit;
  const show = (v: number) => (metric === "minutes" ? `${Math.round(v * 10) / 10}` : fmt.num(v));

  return (
    <div className="uc">
      <div className="uc-head">
        <fieldset className="uc-switch">
          <legend className="uc-hidden">What the charts show</legend>
          {(Object.keys(METRICS) as Metric[]).map((m) => (
            <label key={m} className={metric === m ? "on" : ""}>
              <input type="radio" name="uc-metric" value={m} checked={metric === m} onChange={() => setMetric(m)} />
              {METRICS[m].label}
            </label>
          ))}
        </fieldset>
        <p className="pa-muted">Hover for each agent's share. Select an agent in the legend to hide or show it.</p>
      </div>
      <div className="uc-pair">
        <figure>
          <figcaption><strong>Per hour</strong> <span className="pa-muted">{show(last48)} {unit} in the last 48 hours · drag the slider back up to 14 days · your time zone</span></figcaption>
          <EChart option={hourlyOption} height={320}
            label={`${METRICS[metric].label} per hour by agent: ${show(last48)} ${unit} in the last 48 hours.`} />
        </figure>
        <figure>
          <figcaption><strong>Per day</strong> <span className="pa-muted">{show(last30)} {unit} in the last 30 days{ceilingLimit
            ? ` · busiest day ${fmt.pct(peakDay / ceilingLimit)} of the daily ceiling${peakDay >= CEILING_SHOWN_FROM * ceilingLimit ? " (dashed line)" : ""}` : ""} · days in UTC, like the ceiling</span></figcaption>
          <EChart option={dailyOption} height={280}
            label={`${METRICS[metric].label} per day by agent: ${show(last30)} ${unit} in the last 30 days.`} />
        </figure>
      </div>
      {usage.hourly.length === 0 && <p className="pa-muted">Hourly counts start with this release, so the hourly chart fills up from now on.</p>}
    </div>
  );
}
