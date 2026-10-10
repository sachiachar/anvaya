import { idToken } from "./auth";
import type { KeyBackup } from "./vaultCrypto";

// The owner in the signed-in person's own demo copy (PR-1). The server decides it, at sign-in.
let ownerId = "";
export const currentOwnerId = () => ownerId;

// Who is acting within the copy (PR-1.3): sent with every request, and checked on the server.
export type PersonId = "vikram" | "priya" | "anjali" | "rahul" | "admin";
export type DemoPerson = { id: PersonId; name: string; role: "owner" | "family" | "reviewer" | "platform_admin" };
let actingAs: PersonId = "vikram";
export const currentPerson = () => actingAs;
export const setActingAs = (person: PersonId) => {
  actingAs = person;
};

export type Autonomy = "assist" | "propose" | "act_within_limits";

export type KeptItem = { id: string; collection: "moments" | "inventory" | "people"; type: string; title: string;
  description: string;
  /** When the owner kept it: UTC, ISO 8601 (PR-2.13.1). */
  kept_at?: string | null };

// The same kinds as gaps.KINDS on the server.
export type Gap = { key: string; kind: "nominee" | "conflict" | "heir" | "relationship" | "papers" | "will" | "loans"
  | "first_call" | "digital" | "confirm" | "recheck"; harm: number;
  about: string; text: string };

export type MapNode = { id: string; label: string; type: string;
  group: "owner" | "person" | "mentioned" | "story" | "wish" | "account" | "institution";
  description?: string; kept_at?: string | null; verified?: boolean };
/** derived: implied by the cards' fields; typed links (PR-7.9) have derived false. */
export type MapEdge = { from: string; to: string; label: string; derived: boolean };
export type KnowledgeMap = { nodes: MapNode[]; edges: MapEdge[]; gaps: { node: string; missing: string }[];
  recent: { at: string; action: "Creation" | "Update" | "Deprecation"; id: string; title: string }[];
  counts: { concepts: number; links: number; gaps: number } };

export type Moment = {
  id: string;
  /** On a card waiting for Keep: the kept item it updates, or one it looks like. */
  updates?: string;
  looks_like?: { id: string; title: string };
  kind: "story" | "advice" | "practical" | "wish";
  title: string;
  summary: string;
  quote: string;
  people: string[];
  places: string[];
  year: string | null;
  language?: string;
  intended_for?: string[];
};

export type InventoryItem = {
  id: string;
  category: string;
  institution: string;
  product: string;
  account_hint: string;
  nominee_or_beneficiary: string | null;
  approximate_value: string | null;
  missing_fields: string[];
  papers_at?: string | null;
  intended_for?: string | null;
};

export type DocumentCheck = {
  document_type: string;
  is_expected_type: boolean;
  person_named: string | null;
  matches_expected_person: boolean;
  key_date: string | null;
  concerns: string[];
  /** Likelihood the document is genuine, of the right type, for the right person. */
  confidence: number;
  verdict: "likely_valid" | "needs_attention" | "not_valid";
  /** One result per warning sign in the fraud reference guide (PR-2.3.5). Older cases have none. */
  signs?: SignFinding[];
  guide?: { key: string; title: string; version: number } | null;
};

export type SignFinding = {
  id: string;
  look_for: string;
  result: "found" | "ruled_out" | "cant_tell";
  note: string;
  trust: "human-reviewed" | "machine-confirmed" | "unverified";
  projection: boolean;
};

export type GuardianHeld = {
  id: string;
  at: number;
  action: "recipient_change" | "release";
  status: "held" | "cancelled";
  message: string;
};

export type Case = {
  id: string;
  owner_id: string;
  state: "reported" | "death_confirmed" | "waiting" | "released" | "cancelled";
  certificate_check: DocumentCheck;
  authority_check: DocumentCheck | null;
  wait_until: number | null;
  history: { at: number; event: string }[];
};

export type Person = { id: string; name: string; relationship: string };

export type Proposal = (Moment | InventoryItem | Person) & {
  collection: "moments" | "inventory" | "people";
  status: "pending" | "approved";
  updates?: string;
  /** A kept story or wish this card may be a reworded update of: the card asks Replace or Keep both. */
  looks_like?: { id: string; title: string };
  /** Another voice was heard when this was said (PR-2.13.2): how many voices, never whose. */
  another_voice?: boolean;
};

export type Turn = {
  id: string;
  role: "owner" | "assistant";
  text: string;
  heard?: string;
  attachment?: { kind: "voice" | "photo" | "document"; name: string | null } | null;
  language?: string;
  proposals?: Proposal[];
  /** A live-call exchange whose cards Saathi is still working out (the end-of-call review waits). */
  saving?: boolean;
};

/** A step of the conversation plan (PR-2.12.2), and who starts it: Saathi, or the owner when ready. */
export type PlanStep = { id: string; source: "initial" | "gap" | "suggestion" | "safe_route"; topic: string; ask: string;
  opens: "saathi" | "owner";
  /** How Saathi speaks on it, and those words (PR-2.12.3). */
  tone?: "warm" | "gentle" | "light" | "clear" | "reassuring" | "patient"; tone_words?: string };

export type Task = {
  id: string;
  title: string;
  why_it_matters: string;
  action: "talk" | "legal" | "vault" | "institution" | "family";
  question_for_owner: string;
  steps: string[];
  severity: "high" | "medium" | "low";
};

export type AllowanceMeasure = { used: number; limit: number; share: number; state: "ok" | "warning" | "reached" };
export type MoreKind = "daily_allowance" | "voice_minutes" | "spoken_replies";
export type MoreRequest = { id: string; at: number; kind: MoreKind; amount: number; hours: number;
  status: "pending" | "approved"; expires_at?: number | null };
export type Allowance = { pools: Record<string, Record<string, AllowanceMeasure>>; warn_at: number;
  /** Your requests for more that are waiting, or approved and still running (PR-10.6). */
  requests?: MoreRequest[]; limits?: Record<MoreKind, number> };

export type AccessRequest = { id: string; name: string; relationship: string; contact: string; note: string; at: number };

export type VaultItem = { id: string; category: string; iv: string; ciphertext: string; ai_label: string | null };

/** Adds the signed-in user's token (the API refuses anyone not on the demo list) and who is acting. */
async function withAuth(init?: RequestInit): Promise<RequestInit> {
  const token = await idToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-Acting-As", actingAs);
  return { ...init, headers };
}

/** A person's own model key (PR-10.10). The key itself never comes back from the server. */
export type KeyKind = "studio" | "vertex_express";
export type KeyResult = "works" | "not_available" | "out_of_quota" | "refused" | "not_allowed" | "error";
export type KeyFeature = { id: string; label: string; result: KeyResult; ms: number };
export type ModelAccess = { kind: KeyKind; last4: string; status: "working" | "failing"; features: KeyFeature[];
  tested_at: number; saved_at: number; failed_at: number | null; failure: number | string | null };
export type ModelAccessState = { policy: { enabled: boolean; kinds: KeyKind[] }; access: ModelAccess | null;
  ready: boolean; kinds: KeyKind[] };

export class AuthError extends Error {}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, await withAuth(init));
  if (res.status === 401 || res.status === 403) {
    let detail = "Please sign in.";
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new AuthError(detail);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const upload = (field: string, file: Blob, name = "upload"): RequestInit => {
  const form = new FormData();
  form.append(field, file, name);
  return { method: "POST", body: form };
};

const o = (path: string) => `/owners/${ownerId}${path}`;

export const api = {
  me: () =>
    request<{ email: string | null; name: string | null; auth: boolean; owner_id: string; people: DemoPerson[];
      nickname: string | null; ask_nickname: boolean }>("/me").then((m) => {
      ownerId = m.owner_id;
      return m;
    }),
  /** The optional name for your demo copy; only the Anvaya team sees it. null skips. */
  setNickname: (nickname: string | null) => request<{ nickname: string | null }>("/me/nickname", json({ nickname })),
  /** Today's use against the signed-in person's daily allowance (PR-10.4). */
  allowance: () => request<Allowance>("/allowance"),
  askForMore: (kind: MoreKind, amount: number, reason: string, hours = 24) =>
    request<{ request: MoreRequest }>("/allowance/request", json({ kind, amount, reason, hours })),
  seed: () => request<{ owner: Record<string, unknown> }>("/demo/seed", { method: "POST" }),
  /** Your own model key (PR-10.10): its status only, never the key. */
  modelAccess: () => request<ModelAccessState>("/model-access"),
  /** Tests a key on every model Anvaya uses. Nothing is stored. */
  testModelKey: (kind: KeyKind, key: string) =>
    request<{ features: KeyFeature[]; usable: boolean }>("/model-access/test", json({ kind, key })),
  /** Tests the key again, then stores it encrypted. */
  saveModelKey: (kind: KeyKind, key: string) =>
    request<{ access: ModelAccess; features: KeyFeature[] }>("/model-access", { ...json({ kind, key }), method: "PUT" }),
  removeModelKey: () => request<{ access: null }>("/model-access", { method: "DELETE" }),
  owner: () => request<{ name: string; family: string[] } & Record<string, unknown>>(o("")),
  archive: () => request<{ moments: Moment[]; inventory: InventoryItem[] }>(o("/archive")),
  /** The owner's kept knowledge as a graph, for the demo's live knowledge map. */
  knowledgeMap: () => request<KnowledgeMap>(o("/knowledge-map")),
  /** Everything kept, current versions only, so the owner can remove a mistake. */
  kept: () => request<{ items: KeptItem[] }>(o("/kept")),
  /** Deletes the item and every earlier version from the kept knowledge, for good. */
  removeKept: (id: string) => request<{ removed: number; items: KeptItem[] }>(o(`/kept/${encodeURIComponent(id)}`), { method: "DELETE" }),
  /** What's missing, found by fixed rules (PR-7.10), most harmful first. */
  gaps: () => request<{ gaps: Gap[] }>(o("/gaps")),
  skipGap: (key: string) => request<{ gaps: Gap[] }>(o(`/gaps/${encodeURIComponent(key)}/skip`), { method: "POST" }),

  captureAudio: (audio: Blob, name: string) =>
    request<{ autonomy: Autonomy; language: string; transcript: string; proposals: Moment[] }>(
      o("/capture/audio"), upload("audio", audio, name)),
  addDocument: (image: File) =>
    request<{ autonomy: Autonomy; proposal: InventoryItem }>(o("/documents"), upload("image", image, image.name)),
  addWish: (text: string) => {
    const form = new FormData();
    form.append("text", text);
    return request<Moment>(o("/wishes"), { method: "POST", body: form });
  },

  conversation: () => request<{ turns: Turn[] }>(o("/conversation")),
  say: (text: string | null, file?: Blob, fileName?: string) => {
    const form = new FormData();
    if (text) form.append("text", text);
    if (file) form.append("file", file, fileName ?? "upload");
    return request<{ owner_turn: Turn; assistant_turn: Turn }>(o("/conversation"), { method: "POST", body: form });
  },

  /** Spoken version of one of Saathi's replies, by its ID (the server speaks only Saathi's own replies). */
  speech: async (turnId: string): Promise<Blob> => {
    const res = await fetch("/api/speech", await withAuth(json({ turn_id: turnId })));
    if (!res.ok) throw new Error(`speech failed: ${res.status}`);
    return res.blob();
  },

  requestAccess: (req: { name: string; relationship: string; contact: string; note: string }) =>
    request<{ message: string }>(o("/access-requests"), json(req)),
  ownerAccessRequests: () =>
    request<{ requests: AccessRequest[]; contacts: { name: string; relationship: string; contact: string }[] }>(o("/access-requests")),
  teamAccessRequests: () => request<{ requests: AccessRequest[] }>("/admin/access-requests"),
  decideAccess: (id: string, decision: "approve" | "decline", decided_by: "owner" | "team") =>
    request(`/access-requests/${id}/decision`, json({ decision, decided_by })),

  review: () => request<{ moments: Moment[]; inventory: InventoryItem[] }>(o("/review")),
  /** replace: keep the card as a new version of the kept item it looks like ("Replace it"). */
  decide: (id: string, collection: "moments" | "inventory" | "people", decision: "approve" | "reject", replace = false) =>
    request(`/proposals/${id}/decision`, json({ collection, decision, replace })),

  readiness: () => request<{ score: number; tasks: Task[] }>(o("/readiness"), { method: "POST" }),
  tasks: () => request<{ score: number | null; tasks: Task[] }>(o("/tasks")),
  /** The conversation plan: the only source of what Saathi talks about (PR-2.12.2). */
  plan: () => request<{ version: number; current: PlanStep | null; next: PlanStep[] }>(o("/plan")),
  updateTask: (id: string, status: "done" | "dismissed") => request<Task>(`/tasks/${id}`, json({ status })),
  /** Saathi opens a topic; with the plan step it opens, the next answer is judged against that step (PR-2.12.3). */
  companionAsks: (text: string, stepId?: string | null) => request<Turn>(o("/conversation/prompt"), json({ text, step_id: stepId ?? null })),
  /** A few sentences that sum up these turns of the conversation (shown, never stored). */
  summarizeConversation: (turn_ids: string[]) =>
    request<{ summary: string }>(o("/conversation/summary"), json({ turn_ids })),
  /** Discard: deletes these turns and every card from them still waiting for Keep. Kept items stay. */
  forgetConversation: (turn_ids: string[]) =>
    request<{ deleted_turns: number; deleted_items: number }>(o("/conversation/forget"), json({ turn_ids })),
  legalNeeds: () =>
    request<{ autonomy: Autonomy; needs: { wish_excerpt: string; document_needed: string; reason_plain: string; urgency: string }[] }>(
      o("/legal-needs"), { method: "POST" }),
  legalHandoff: (partner: string, owner_consent: boolean, needs: unknown[]) =>
    request<{ status: string }>(o("/legal-handoff"), json({ partner, owner_consent, needs })),

  setRecipient: (name: string, relationship: string, public_key_jwk: JsonWebKey, key_backup: KeyBackup) =>
    request(o("/vault/recipient"), json({ name, relationship, public_key_jwk, key_backup })),
  getRecipient: () =>
    request<{ name: string; relationship: string; public_key_jwk: JsonWebKey; key_backup: KeyBackup; vault_sealed: boolean;
      status: "pending_owner" | "approved" }>(o("/vault/recipient")),
  decideRecipient: (decision: "approve" | "decline") => request(o("/vault/recipient/decision"), json({ decision })),
  // held: the guardian holds the change for our team to check (PR-2.7), with a message to show.
  inviteRecipient: (name: string, relationship: string) =>
    request<{ invited?: string; held?: boolean; message?: string }>(o("/vault/invite"), json({ name, relationship })),
  getInvite: () => request<{ name: string; relationship: string }>(o("/vault/invite")),
  setEscrow: (recipient_wrapped_key: string) =>
    request<{ stored: boolean; kms: boolean }>(o("/vault/escrow"), json({ recipient_wrapped_key })),
  addVaultItem: (item: { category: string; iv: string; ciphertext: string; ai_label: string | null }) =>
    request<VaultItem>(o("/vault/items"), json(item)),
  vaultItems: () => request<{ items: VaultItem[] }>(o("/vault/items")),

  reportDeath: (certificate: File) =>
    request<{ case: Case }>(o("/death-report"), upload("certificate", certificate, certificate.name)),
  submitAuthority: (proof: File) =>
    request<{ case: Case }>(o("/release/authority"), upload("proof", proof, proof.name)),
  releaseStatus: () => request<{ case: Case | null }>(o("/release")),
  // What the guardian holds for a risk reviewer, or stopped this week (PR-2.7): a fixed message each.
  guardianHeld: () => request<{ held: GuardianHeld[] }>(o("/guardian/held")),
  ownerAlive: () => request<{ case?: Case }>(o("/alive"), { method: "POST" }),
  releaseBundle: () => request<{ recipient_wrapped_key: string; items: VaultItem[] }>(o("/vault/release-bundle")),

  cases: () => request<{ cases: Case[] }>("/admin/cases"),
  confirmDeath: (id: string, reviewer: string, override_reason?: string) =>
    request<{ case: Case }>(`/admin/cases/${id}/confirm-death`, json({ reviewer, override_reason })),
  confirmAuthority: (id: string, reviewer: string, override_reason?: string) =>
    request<{ case: Case }>(`/admin/cases/${id}/confirm-authority`, json({ reviewer, override_reason })),

  ask: (question: string, session_id?: string) =>
    // notes: a helpful line the guardian adds for a risky pattern, such as a fee someone asked for (PR-2.12.4)
    request<{ answer: string; sources: { id: string; title: string }[]; tools_used: string[]; session_id: string;
      notes?: string[] }>(
      o("/ask"), json({ question, session_id })),
  settlement: () =>
    request<{
      checklist: { institution: string; action: string; documents_needed: string[]; priority: string; source_id: string }[];
      letters: { institution: string; subject: string; body: string }[];
      unclaimed_asset_searches: string[];
    }>(o("/settlement"), { method: "POST" }),
};
