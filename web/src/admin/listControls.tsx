import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fmt, type Paged } from "./api";

// Paged lists for the admin portal: a filter bar, a pager, and the page and filters kept in the
// address (#/guardian?ev.q=voice&ev.page=2), so a filtered list can be shared or reloaded.
// The server pages, filters and counts (governance/paging.py); the browser never holds a whole list.

export const PAGE_SIZES = [20, 50, 100] as const;
const SEARCH_DELAY_MS = 300;

type Params = Record<string, string>;

/** The list's own values from the address: keys start with "prefix." */
function readHash(prefix: string): Params {
  const query = location.hash.split("?")[1] ?? "";
  const out: Params = {};
  for (const [k, v] of new URLSearchParams(query)) {
    if (k.startsWith(`${prefix}.`) && v) out[k.slice(prefix.length + 1)] = v;
  }
  return out;
}

/** Puts the list's values back in the address without a new history step or a reload. */
function writeHash(prefix: string, params: Params) {
  const [path, query = ""] = location.hash.split("?");
  const all = new URLSearchParams(query);
  for (const k of [...all.keys()]) if (k.startsWith(`${prefix}.`)) all.delete(k);
  for (const [k, v] of Object.entries(params)) if (v) all.set(`${prefix}.${k}`, v);
  const qs = all.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}${path || "#/"}${qs ? `?${qs}` : ""}`);
}

export type List<T, X = object> = {
  data: Paged<T, X> | null;
  error: string | null;
  params: Params;
  /** Sets one value; any change but the page goes back to page 1. An empty value clears it. */
  set: (key: string, value: string) => void;
  clear: () => void;
  reload: () => void;
  /** True when a search, a filter or a date is set. */
  filtered: boolean;
};

/**
 * One paged list. `prefix` keeps two lists on a page apart in the address. `fixed` values are
 * always sent and never shown (for example status=pending). Change `version` to load again, for
 * example after a decision or while a fraud run is live.
 */
export function useList<T, X = object>(prefix: string, load: (q: URLSearchParams) => Promise<Paged<T, X>>,
  fixed: Params = {}, version = 0): List<T, X> {
  const [params, setParams] = useState<Params>(() => readHash(prefix));
  const [data, setData] = useState<Paged<T, X> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const fixedKey = JSON.stringify(fixed);

  useEffect(() => {
    let current = true;
    const q = new URLSearchParams({ ...params, ...JSON.parse(fixedKey) });
    load(q).then((d) => {
      if (!current) return;
      setData(d);
      setError(null);
      // A page past the end comes back as the last page: show that number in the address too.
      if (params.page && Number(params.page) !== d.page.page) setParams((p) => ({ ...p, page: String(d.page.page) }));
    }).catch((e) => { if (current) setError(String(e.message ?? e)); });
    return () => { current = false; };
  }, [load, params, fixedKey, tick, version]);

  useEffect(() => writeHash(prefix, params), [prefix, params]);

  const set = useCallback((key: string, value: string) => {
    setParams((p) => {
      if ((p[key] ?? "") === value) return p;
      const next = { ...p, [key]: value };
      if (key !== "page") delete next.page;
      if (!value) delete next[key];
      return next;
    });
  }, []);
  const clear = useCallback(() => setParams((p): Params => (p.size ? { size: p.size } : {})), []);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  const filtered = Object.keys(params).some((k) => k !== "page" && k !== "size");
  return { data, error, params, set, clear, reload, filtered };
}

/** A filter in the bar: fixed options, or the server's choices for `choices` (with counts). */
export type FilterDef = {
  key: string;
  label: string;
  /** Which entry of the response's choices to offer; defaults to `key`. */
  choices?: string;
  /** Fixed options as [value, label]; used instead of the server's choices. */
  options?: [string, string][];
  /** How to show a value from the server's choices; defaults to fmt.label. */
  show?: (value: string) => string;
};

/** Search, filters and dates for one list. The search waits for a pause in typing. */
export function FilterBar<T, X>({ list, filters = [], dates = true, search = "Search" }: {
  list: List<T, X>; filters?: FilterDef[]; dates?: boolean; search?: string;
}) {
  const { params, set } = list;
  const [text, setText] = useState(params.q ?? "");
  const typed = useRef(false);
  useEffect(() => {
    if (!typed.current) return;
    const t = window.setTimeout(() => set("q", text.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [text, set]);
  // "Clear" empties the box too.
  useEffect(() => { if (!params.q) setText(""); }, [params.q]);

  return (
    <search className="pa-filters">
      <label className="pa-filter-q">
        <span className="pa-muted">{search}</span>
        <input type="search" value={text} maxLength={100}
          onChange={(e) => { typed.current = true; setText(e.target.value); }} />
      </label>
      {filters.map((f) => {
        const counts = list.data?.choices?.[f.choices ?? f.key] ?? {};
        const options: [string, string][] = f.options
          ?? Object.entries(counts).map(([v, n]) => [v, `${(f.show ?? fmt.label)(v)} (${n})`]);
        const value = params[f.key] ?? "";
        // A value from the address stays selectable even if no row has it now.
        if (value && !options.some(([v]) => v === value)) options.push([value, `${(f.show ?? fmt.label)(value)} (0)`]);
        return (
          <label key={f.key}>
            <span className="pa-muted">{f.label}</span>
            <select value={value} onChange={(e) => set(f.key, e.target.value)}>
              <option value="">All</option>
              {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        );
      })}
      {dates && (
        <span className="pa-dates">
          <label>
            <span className="pa-muted">From</span>
            <input type="date" value={params.from ?? ""} max={params.to} onChange={(e) => set("from", e.target.value)} />
          </label>
          <label>
            <span className="pa-muted">To</span>
            <input type="date" value={params.to ?? ""} min={params.from} onChange={(e) => set("to", e.target.value)} />
          </label>
        </span>
      )}
      {list.filtered && (
        <button type="button" className="pa-small" onClick={list.clear}>Clear</button>
      )}
    </search>
  );
}

/** Page numbers to show: the first, the last, and two either side of the current one. */
function pageNumbers(page: number, pages: number): (number | "gap")[] {
  const out: (number | "gap")[] = [];
  for (let n = 1; n <= pages; n++) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 2) out.push(n);
    else if (out[out.length - 1] !== "gap") out.push("gap");
  }
  return out;
}

/** "Showing 21–40 of 312", the page buttons, and how many rows a page shows. */
export function Pager<T, X>({ list }: { list: List<T, X> }) {
  const info = list.data?.page;
  if (!info || info.total === 0) return null;
  const first = (info.page - 1) * info.size + 1;
  const last = Math.min(info.total, info.page * info.size);
  const go = (n: number) => list.set("page", n === 1 ? "" : String(n));
  return (
    <nav className="pa-pager" aria-label="Pages">
      <span className="pa-muted">Showing {fmt.num(first)}–{fmt.num(last)} of {fmt.num(info.total)}</span>
      {info.pages > 1 && (
        <span className="pa-pages">
          <button type="button" className="pa-small" disabled={info.page === 1} onClick={() => go(info.page - 1)}>Previous</button>
          {pageNumbers(info.page, info.pages).map((n, i) => n === "gap"
            ? <span key={`gap${i}`} className="pa-muted" aria-hidden="true">…</span>
            : <button key={n} type="button" className={`pa-small${n === info.page ? " is-current" : ""}`}
                aria-current={n === info.page ? "page" : undefined} onClick={() => go(n)}>{n}</button>)}
          <button type="button" className="pa-small" disabled={info.page === info.pages} onClick={() => go(info.page + 1)}>Next</button>
        </span>
      )}
      <label className="pa-size">
        <span className="pa-muted">Per page</span>
        <select value={String(info.size)} onChange={(e) => list.set("size", e.target.value === "20" ? "" : e.target.value)}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
    </nav>
  );
}

/** A whole paged list: the filter bar, the rows (or why there are none), and the pager. */
export function ListView<T, X>({ list, filters, dates, search, empty, children }: {
  list: List<T, X>; filters?: FilterDef[]; dates?: boolean; search?: string;
  /** What will fill the list, shown when it's empty with no filters set. */
  empty: ReactNode;
  children: (items: T[], data: Paged<T, X>) => ReactNode;
}) {
  const { data, error } = list;
  return (
    <>
      {/* An empty list with nothing set and no choices (such as faded scenarios) has nothing to filter yet. */}
      {(list.filtered || (data?.page.total ?? 0) > 0
        || Object.values(data?.choices ?? {}).some((c) => Object.keys(c).length > 0)) && <FilterBar list={list} filters={filters} dates={dates} search={search} />}
      {error && <p className="pa-error" role="alert">{error}</p>}
      {!data ? (!error && <p className="pa-muted">Loading…</p>) : data.page.total === 0 ? (
        list.filtered
          ? <p className="pa-muted">Nothing matches. <button type="button" className="pa-link" onClick={list.clear}>Clear the filters</button></p>
          : <p className="pa-muted">{empty}</p>
      ) : children(data.items, data)}
      <Pager list={list} />
    </>
  );
}
