// "Ask for more" (PR-10.6), shared by both designs: what each measure's request asks for, and how a
// request reads back to the person. daily_allowance is in thousands of tokens.
import type { MoreKind, MoreRequest } from "./api";

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
