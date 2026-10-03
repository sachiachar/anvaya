import { useEffect, useState } from "react";
import { api, type Case, type Gap, type InventoryItem, type KeptItem, type Moment, type Task, type VaultItem } from "./api";
import Conversation from "./Conversation";
import { Card, ErrorLine, useAction } from "./ui";
import { encryptSecret, keyFingerprint, ownerDataKey, wrapForRecipient } from "./vaultCrypto";

export default function Owner() {
  const [tab, setTab] = useState<"talk" | "plan">("talk");
  return (
    <>
      <AliveAlert />
      <div className="segmented" role="tablist">
        <button role="tab" aria-selected={tab === "talk"} className={tab === "talk" ? "active" : ""} onClick={() => setTab("talk")}>Talk</button>
        <button role="tab" aria-selected={tab === "plan"} className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}>My plan</button>
      </div>
      <p className="map-link">
        <a href="/#knowledge-map" target="_blank" rel="noopener">Open the knowledge map</a>
        <span className="muted"> · in a new window, to watch it fill in as you keep things</span>
      </p>
      {tab === "talk" ? (
        <Conversation />
      ) : (
        <>
          <NextSteps onTalk={() => setTab("talk")} />
          <MissingPieces />
          <WhatYouKept />
          <People />
          <WeeklyReview />
          <div id="legal"><WishesAndLegal /></div>
          <div id="vault"><Vault /></div>
        </>
      )}
    </>
  );
}

/** Shown if someone reports the owner's death: one tap stops the release. */
function AliveAlert() {
  const [open, setOpen] = useState<Case | null>(null);
  const { run, error } = useAction();
  useEffect(() => {
    const check = () =>
      api.releaseStatus().then((r) => setOpen(r.case && r.case.state !== "released" ? r.case : null)).catch(() => {});
    check();
    const t = setInterval(check, 5000);
    return () => clearInterval(t);
  }, []);
  if (!open) return null;
  return (
    <section className="alert" role="alert">
      <strong>Someone has reported your death.</strong>
      <p>If you are well, tap below. Your vault stays locked and the report is cancelled.</p>
      <button onClick={() => run(async () => { await api.ownerAlive(); setOpen(null); })}>I'm OK, cancel this</button>
      <ErrorLine error={error} />
    </section>
  );
}

const KEPT_GROUPS = [
  { collection: "moments", heading: "Stories, advice and wishes" },
  { collection: "inventory", heading: "Accounts and policies" },
  { collection: "people", heading: "People" },
] as const;

/** Everything kept, with Remove for anything recorded wrongly. Removing deletes it, and every
 * earlier version, from the kept knowledge for good: the family never sees it. The conversation
 * it came from stays in the chat. */
function WhatYouKept() {
  const [items, setItems] = useState<KeptItem[] | null>(null);
  const { error, run } = useAction();
  useEffect(() => { run(async () => setItems((await api.kept()).items)); }, []);
  if (error && !items) return <ErrorLine error={error} />;
  if (!items || items.length === 0) return null;
  const remove = (item: KeptItem) => {
    if (!window.confirm(`Remove "${item.title}"? It's removed for good from what your family receives.`)) return;
    run(async () => setItems((await api.removeKept(item.id)).items));
  };
  return (
    <Card title="What you've kept">
      <p className="muted">If something was recorded wrongly, remove it here. To change a detail, just tell Saathi.</p>
      {KEPT_GROUPS.map(({ collection, heading }) => {
        const group = items.filter((i) => i.collection === collection);
        if (group.length === 0) return null;
        return (
          <details key={collection}>
            <summary>{heading} ({group.length})</summary>
            <ul className="list">
              {group.map((i) => (
                <li key={i.id} className="row">
                  <span><strong>{i.title}</strong>{i.description && <span className="muted"> · {i.description}</span>}</span>
                  <button className="secondary" onClick={() => remove(i)}>Remove</button>
                </li>
              ))}
            </ul>
          </details>
        );
      })}
      <ErrorLine error={error} />
    </Card>
  );
}

/** Gaps found by fixed rules (PR-7.10). Saathi may gently ask about the top ones; Skip means
 * "I'd rather not, or I don't know", and Saathi never asks about it again. */
function MissingPieces() {
  const [gaps, setGaps] = useState<Gap[] | null>(null);
  const { error, run } = useAction();
  useEffect(() => { run(async () => setGaps((await api.gaps()).gaps)); }, []);
  if (!gaps || gaps.length === 0) return null;
  return (
    <Card title="Missing pieces" level="act_within_limits">
      <p className="muted">These would help your family most. Saathi may gently ask about the first ones when the moment is right. Skip any you'd rather not answer.</p>
      <ul className="list">
        {gaps.map((g) => (
          <li key={g.key} className="row">
            <span>{g.text}</span>
            <button className="secondary" onClick={() => run(async () => setGaps((await api.skipGap(g.key)).gaps))}>Skip</button>
          </li>
        ))}
      </ul>
      <p className="muted">We count common gaps, never the details, to protect everyone.</p>
      <ErrorLine error={error} />
    </Card>
  );
}

/** Family members who asked for access; only the owner can let them in. */
function People() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.ownerAccessRequests>> | null>(null);
  const { error, run } = useAction();
  const refresh = () => run(async () => setData(await api.ownerAccessRequests()));
  useEffect(() => { refresh(); }, []);
  if (!data || (data.requests.length === 0 && data.contacts.length === 0)) return null;
  return (
    <Card title="People" level="propose">
      {data.requests.map((r) => (
        <div key={r.id} className="proposal">
          <p className="proposal-title">{r.name} ({r.relationship}) is asking for access</p>
          {r.note && <p className="muted">"{r.note}"</p>}
          <div className="row">
            <button onClick={() => run(async () => { await api.decideAccess(r.id, "approve", "owner"); await refresh(); })}>Allow</button>
            <button className="secondary" onClick={() => run(async () => { await api.decideAccess(r.id, "decline", "owner"); await refresh(); })}>Decline</button>
          </div>
        </div>
      ))}
      {data.contacts.length > 0 && (
        <ul className="list">
          {data.contacts.map((c) => <li key={c.contact}>✓ <strong>{c.name}</strong> ({c.relationship}) can see your stories after you're gone</li>)}
        </ul>
      )}
      <ErrorLine error={error} />
    </Card>
  );
}

function WeeklyReview() {
  const [queue, setQueue] = useState<{ moments: Moment[]; inventory: InventoryItem[] }>({ moments: [], inventory: [] });
  const { error, run } = useAction();
  const refresh = () => run(async () => setQueue(await api.review()));
  useEffect(() => { refresh(); }, []);

  const decide = (id: string, c: "moments" | "inventory", d: "approve" | "reject", replace = false) =>
    run(async () => { await api.decide(id, c, d, replace); await refresh(); });

  const empty = queue.moments.length + queue.inventory.length === 0;
  return (
    <Card title="Weekly review" level="propose">
      <p className="muted">Nothing is saved to your archive until you say yes.</p>
      <button className="link" onClick={refresh}>Refresh</button>
      {empty && <p className="muted">Nothing waiting for you.</p>}
      <ul className="list">
        {queue.moments.map((m) => (
          <li key={m.id}>
            <span className="tag">{m.kind}</span> <strong>{m.title}</strong>
            <blockquote>{m.quote}</blockquote>
            {m.looks_like && !m.updates ? (
              <>
                <p className="warn">This looks like an update to "{m.looks_like.title}".</p>
                <div className="row">
                  <button onClick={() => decide(m.id, "moments", "approve", true)}>Replace it</button>
                  <button className="secondary" onClick={() => decide(m.id, "moments", "approve")}>Keep both</button>
                  <button className="secondary" onClick={() => decide(m.id, "moments", "reject")}>Discard</button>
                </div>
              </>
            ) : (
              <div className="row">
                <button onClick={() => decide(m.id, "moments", "approve")}>Keep</button>
                <button className="secondary" onClick={() => decide(m.id, "moments", "reject")}>Discard</button>
              </div>
            )}
          </li>
        ))}
        {queue.inventory.map((i) => (
          <li key={i.id}>
            <span className="tag">{i.category.replace("_", " ")}</span> <strong>{i.institution}</strong> · {i.product} {i.account_hint}
            <div className="row">
              <button onClick={() => decide(i.id, "inventory", "approve")}>Keep</button>
              <button className="secondary" onClick={() => decide(i.id, "inventory", "reject")}>Discard</button>
            </div>
          </li>
        ))}
      </ul>
      <ErrorLine error={error} />
    </Card>
  );
}

const ACTION_BUTTON: Record<Task["action"], string> = {
  talk: "Talk about this",
  family: "Talk about this",
  legal: "Get help with the legal step",
  vault: "Open my vault",
  institution: "",
};

/**
 * Readiness as next steps: each gap stays on the list, with a button that acts on it,
 * until the owner marks it done or not relevant.
 */
function NextSteps({ onTalk }: { onTalk: () => void }) {
  const [score, setScore] = useState<number | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const { busy, error, run } = useAction();

  const check = () => run(async () => { const r = await api.readiness(); setScore(r.score); setTasks(r.tasks); });
  useEffect(() => {
    run(async () => {
      const r = await api.tasks();
      setScore(r.score);
      setTasks(r.tasks);
      if (r.score === null) await check();  // first visit: check straight away
    });
  }, []);

  const close = (t: Task, status: "done" | "dismissed") =>
    run(async () => { await api.updateTask(t.id, status); setTasks((all) => all?.filter((x) => x.id !== t.id) ?? null); });

  const act = (t: Task) => run(async () => {
    if (t.action === "talk" || t.action === "family") {
      await api.companionAsks(t.question_for_owner);
      onTalk();
    } else {
      document.getElementById(t.action === "vault" ? "vault" : "legal")?.scrollIntoView({ behavior: "smooth" });
    }
  });

  return (
    <Card title="Next steps" level="act_within_limits">
      <div className="row">
        {score !== null && <p className="score">{score}<span>/100 ready</span></p>}
        <button className="link" disabled={busy} onClick={check}>{busy ? "Checking…" : "Check again"}</button>
      </div>
      <ErrorLine error={error} />
      {tasks && tasks.length === 0 && <p className="ok">✓ Nothing to do right now.</p>}
      <ul className="list">
        {tasks?.map((t) => (
          <li key={t.id}>
            <span className={`tag sev-${t.severity}`}>{t.severity}</span> <strong>{t.title}</strong>
            <p className="muted">{t.why_it_matters}</p>
            {t.steps.length > 0 && <ol className="steps-list">{t.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
            <div className="row">
              {ACTION_BUTTON[t.action] && <button disabled={busy} onClick={() => act(t)}>{ACTION_BUTTON[t.action]}</button>}
              <button className="secondary" disabled={busy} onClick={() => close(t, "done")}>Done</button>
              <button className="link" disabled={busy} onClick={() => close(t, "dismissed")}>Not relevant</button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function WishesAndLegal() {
  const [wish, setWish] = useState("");
  const [needs, setNeeds] = useState<Awaited<ReturnType<typeof api.legalNeeds>>["needs"] | null>(null);
  const [consent, setConsent] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  return (
    <Card title="Wishes and legal documents" level="propose">
      <p className="muted">Write wishes in your own words. The assistant flags any that need a legal document. It never gives legal advice.</p>
      <div className="row">
        <input value={wish} onChange={(e) => setWish(e.target.value)} placeholder="e.g. My car should go to Arjun" aria-label="New wish" />
        <button disabled={!wish.trim() || busy} onClick={() => run(async () => { await api.addWish(wish.trim()); setWish(""); })}>Add</button>
      </div>
      <button className="secondary" disabled={busy} onClick={() => run(async () => setNeeds((await api.legalNeeds()).needs))}>
        {busy ? "Checking…" : "Which wishes need a legal document?"}
      </button>
      <ErrorLine error={error} />
      {needs && (
        <div className="result">
          <ul className="list">
            {needs.map((n, i) => (
              <li key={i}>
                <span className={`tag sev-${n.urgency}`}>{n.document_needed.replaceAll("_", " ")}</span>
                <p>"{n.wish_excerpt}"</p>
                <p className="muted">{n.reason_plain}</p>
              </li>
            ))}
          </ul>
          <label className="check">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            I agree to share this summary (never my vault) with Demo Legal Partner.
          </label>
          <button disabled={!consent || busy}
            onClick={() => run(async () => setSent((await api.legalHandoff("Demo Legal Partner", consent, needs)).status))}>
            Send to legal partner
          </button>
          {sent && <p className="ok">✓ {sent}</p>}
        </div>
      )}
    </Card>
  );
}

function Vault() {
  const [items, setItems] = useState<VaultItem[]>([]);
  const [ready, setReady] = useState<string | null>(null);
  const [form, setForm] = useState({ category: "password", label: "", secret: "", aiMaySeeLabel: true });
  const { busy, error, run } = useAction();
  const [recipient, setRecipient] = useState<Awaited<ReturnType<typeof api.getRecipient>> | null>(null);
  const [invite, setInvite] = useState<{ name: string; relationship: string } | null>(null);
  const [family, setFamily] = useState<string[]>([]);
  const [choice, setChoice] = useState({ name: "", relationship: "" });
  const [code, setCode] = useState<string | null>(null);
  const refresh = () => run(async () => {
    setItems((await api.vaultItems()).items);
    const r = await api.getRecipient().catch(() => null);
    setRecipient(r);
    setCode(r ? await keyFingerprint(r.public_key_jwk) : null);
    setInvite(await api.getInvite().catch(() => null));
    setFamily((await api.owner()).family ?? []);
  });

  // "Meena (wife)" -> { name: "Meena", relationship: "wife" }
  const parse = (f: string) => {
    const m = f.match(/^(.*?)\s*\((.*)\)$/);
    return m ? { name: m[1], relationship: m[2] } : { name: f, relationship: "" };
  };
  useEffect(() => { refresh(); }, []);

  async function setup() {
    const recipient = await api.getRecipient();
    const wrapped = await wrapForRecipient(await ownerDataKey(), recipient.public_key_jwk);
    const r = await api.setEscrow(wrapped);
    setReady(`Vault key sealed for ${recipient.name}${r.kms ? " and escrowed with Cloud KMS" : " (dev mode: no KMS)"}.`);
    await refresh();
  }

  async function add() {
    const { iv, ciphertext } = await encryptSecret(await ownerDataKey(), { label: form.label, secret: form.secret });
    await api.addVaultItem({ category: form.category, iv, ciphertext, ai_label: form.aiMaySeeLabel ? form.label : null });
    setForm({ ...form, label: "", secret: "" });
    await refresh();
  }

  return (
    <Card title="Secure vault" level="act_within_limits">
      <p className="muted">Passwords, PINs and account details are encrypted on this phone. Neither the assistant nor Anvaya can read them. They go only to your vault recipient, after strict checks.</p>
      {(!invite || !recipient) && (
        <div className="proposal">
          <p className="proposal-title">
            {invite ? `You chose ${invite.name}. Waiting for them to accept in their app.` : "Who should receive your vault?"}
          </p>
          <div className="stack">
            {family.length > 0 && (
              <select value="" onChange={(e) => e.target.value && setChoice(parse(e.target.value))} aria-label="Choose from family">
                <option value="">Choose from your family…</option>
                {family.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            )}
            <div className="row">
              <input value={choice.name} onChange={(e) => setChoice({ ...choice, name: e.target.value })} placeholder="Name" aria-label="Recipient name" />
              <input value={choice.relationship} onChange={(e) => setChoice({ ...choice, relationship: e.target.value })} placeholder="Relationship" aria-label="Relationship" />
            </div>
            <button disabled={busy || !choice.name.trim() || !choice.relationship.trim()}
              onClick={() => run(async () => { await api.inviteRecipient(choice.name.trim(), choice.relationship.trim()); setChoice({ name: "", relationship: "" }); await refresh(); })}>
              {invite ? "Choose someone else" : "Invite"}
            </button>
          </div>
        </div>
      )}
      {recipient?.status === "pending_owner" && (
        <div className="proposal">
          <p className="proposal-title">{recipient.name} ({recipient.relationship}) wants to be your vault recipient</p>
          <p>Only approve if their phone shows the same code: <strong className="code-inline">{code}</strong></p>
          <div className="row">
            <button disabled={busy} onClick={() => run(async () => { await api.decideRecipient("approve"); await refresh(); })}>Approve</button>
            <button className="secondary" disabled={busy} onClick={() => run(async () => { await api.decideRecipient("decline"); await refresh(); })}>Decline</button>
          </div>
        </div>
      )}
      {recipient?.status === "approved" && !recipient.vault_sealed && (
        <p className="warn">{recipient.name} is approved. Seal the vault so only {recipient.name} can open it.</p>
      )}
      {recipient?.vault_sealed && <p className="ok">✓ Sealed for {recipient.name} (key code {code}).</p>}
      {recipient?.status === "approved" && (
        <button className="secondary" disabled={busy} onClick={() => run(setup)}>Seal vault for {recipient.name}</button>
      )}
      {ready && <p className="ok">✓ {ready}</p>}
      <div className="stack">
        <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} aria-label="Category">
          <option value="password">Password</option>
          <option value="pin">PIN</option>
          <option value="account">Account details</option>
          <option value="instructions">Instructions</option>
        </select>
        <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="What is it? e.g. Email login" aria-label="Label" />
        <input value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} placeholder="The secret" type="password" aria-label="Secret" />
        <label className="check">
          <input type="checkbox" checked={form.aiMaySeeLabel} onChange={(e) => setForm({ ...form, aiMaySeeLabel: e.target.checked })} />
          Let the assistant see the label only, so it can spot gaps
        </label>
        <button disabled={!form.label || !form.secret || busy} onClick={() => run(add)}>Encrypt and save</button>
      </div>
      <ErrorLine error={error} />
      <ul className="list">
        {items.map((v) => (
          <li key={v.id}>
            🔒 <strong>{v.ai_label ?? "Hidden label"}</strong> <span className="tag">{v.category}</span>
            <code className="cipher">{v.ciphertext.slice(0, 32)}…</code>
          </li>
        ))}
      </ul>
    </Card>
  );
}
