import { useCallback, useEffect, useRef, useState } from "react";

/** How often the Agents page asks again: the loop map and your demo's recent steps. */
export const POLL_MS = 15000;

/** Load now, then every `ms` while the tab is visible, and again when it becomes visible. Only the
 *  newest request's answer is shown, and none after the page closes. `refresh` loads at once. */
export function usePoll<T>(load: () => Promise<T>, ms = POLL_MS) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useRef<() => void>(() => {});
  useEffect(() => {
    let stopped = false;
    let latest = 0;
    run.current = () => {
      if (document.hidden) return; // no polling in a background tab
      const mine = ++latest;
      load().then((d) => { if (!stopped && mine === latest) { setData(d); setError(null); } })
        .catch((e) => { if (!stopped && mine === latest) setError(String(e.message ?? e)); });
    };
    const tick = () => run.current();
    tick();
    const timer = window.setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [load, ms]);
  const refresh = useCallback(() => run.current(), []);
  return { data, error, refresh };
}
