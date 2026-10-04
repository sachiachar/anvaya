// "Ask for more" (PR-10.6), shared by both designs: what each measure's request asks for, and how a
// request reads back to the person. daily_allowance is in thousands of tokens.
import { useState } from "react";
import { type Allowance, api, type MoreKind, type MoreRequest } from "./api";

export const MORE: Record<string, { kind: MoreKind; unit: string; step: number }> = {
  tokens: { kind: "daily_allowance", unit: "thousand tokens", step: 100 },
  voice_minutes: { kind: "voice_minutes", unit: "voice minutes", step: 30 },
  spoken_replies: { kind: "spoken_replies", unit: "spoken replies", step: 50 },
};

const KIND_TEXT: Record<MoreKind, string> = {
  daily_allowance: "thousand tokens", voice_minutes: "voice minutes", spoken_replies: "spoken replies",
};

export function requestText(r: MoreRequest): string {
  return r.status === "pending"
    ? `You asked for ${r.amount} more ${KIND_TEXT[r.kind]}: waiting for approval.`
    : `Approved: ${r.amount} more ${KIND_TEXT[r.kind]} until ${new Date((r.expires_at ?? 0) * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}.`;
}

/** The notice's state: which measures are near or at the limit, the requests, and the form. Both designs'
 * notices use it, so the cap and the one-waiting-request check can't differ between them. */
export function useAskForMore(allowance: Allowance, onAsked: () => void) {
  const owner = allowance.pools.owner ?? {};
  const reachedKeys = Object.entries(owner).filter(([, m]) => m.state === "reached").map(([k]) => k);
  const nearKeys = Object.entries(owner).filter(([, m]) => m.state === "warning").map(([k]) => k);
  const requests = allowance.requests ?? [];
  const first = MORE[reachedKeys[0] ?? nearKeys[0] ?? "tokens"] ?? MORE.tokens;
  const [asking, setAsking] = useState(false);
  const [kind, setKind] = useState<MoreKind>(first.kind);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const amount = Object.values(MORE).find((m) => m.kind === kind)?.step ?? 100;
  const waiting = requests.some((r) => r.kind === kind && r.status === "pending");
  return {
    owner, reachedKeys, nearKeys, requests, asking, kind, setKind, reason, setReason, busy, error, waiting,
    canAsk: !busy && !waiting && reason.trim().length >= 3,
    open: () => { setKind(first.kind); setAsking(true); },
    close: () => setAsking(false),
    ask: () => {
      setBusy(true);
      setError(null);
      api.askForMore(kind, Math.min(amount, allowance.limits?.[kind] ?? amount), reason.trim())
        .then(() => { setAsking(false); setReason(""); onAsked(); })
        .catch((e) => setError(String(e.message ?? e)))
        .finally(() => setBusy(false));
    },
  };
}
