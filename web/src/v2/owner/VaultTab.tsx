// Vault (v2): passwords, PINs and account details, locked on this phone before they leave it. The
// server only ever holds the locked form; the screen shows what each secret is, never its locked
// text. Encryption follows the classic Owner.tsx and lives in vaultCrypto.ts.
import { useEffect, useState } from "react";
import { api, type VaultItem } from "../../api";
import { useAction } from "../../ui";
import { encryptSecret, ownerDataKey } from "../../vaultCrypto";
import { friendlyError } from "../copy";
import { Lock, Shield } from "../icons";

const CATEGORIES = [
  { id: "password", label: "Password" },
  { id: "pin", label: "PIN" },
  { id: "account", label: "Account details" },
  { id: "instructions", label: "Instructions" },
] as const;
const CATEGORY_LABEL: Record<string, string> = Object.fromEntries(CATEGORIES.map((c) => [c.id, c.label]));

export default function VaultTab({ onFamily }: { onFamily: () => void }) {
  const [items, setItems] = useState<VaultItem[] | null>(null);
  const [recipient, setRecipient] = useState<Awaited<ReturnType<typeof api.getRecipient>> | null>(null);
  const [form, setForm] = useState({ category: "password", label: "", secret: "", saathiMaySeeLabel: true });
  const [reveal, setReveal] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  const [recipientKnown, setRecipientKnown] = useState(false);
  const { busy, error, run } = useAction();

  const [checkError, setCheckError] = useState<string | null>(null);
  // Separately, and honestly: if either can't be checked, say so rather than "not sealed" or "nothing locked".
  const refresh = () => run(async () => {
    setCheckError(null);
    const [list, who] = await Promise.allSettled([
      api.vaultItems(),
      api.getRecipient().catch((e) => { if (/no vault recipient/.test(String(e?.message))) return null; throw e; }),
    ]);
    if (list.status === "fulfilled") setItems(list.value.items);
    if (who.status === "fulfilled") { setRecipient(who.value); setRecipientKnown(true); }
    const failed = [list, who].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    if (failed) setCheckError(friendlyError(failed.reason));
  });
  useEffect(() => { refresh(); }, []);

  /** Locked on this phone first; only the locked form is sent. */
  const add = () => run(async () => {
    const { iv, ciphertext } = await encryptSecret(await ownerDataKey(), { label: form.label, secret: form.secret });
    await api.addVaultItem({ category: form.category, iv, ciphertext, ai_label: form.saathiMaySeeLabel ? form.label : null });
    setAdded(form.label);
    setForm({ ...form, label: "", secret: "" });
    setReveal(false);
    await refresh();
  });

  const sealed = Boolean(recipient?.vault_sealed);
  return (
    <div className="v2-stack v2-tab">
      <header className="v2-tab-head">
        <h1>Your vault</h1>
        <p>Locked on this phone. Nobody else can read it: not Anvaya, not your bank, not Saathi.</p>
      </header>

      {checkError && (
        <div className="v2-stack">
          <p className="v2-error" role="alert">{checkError} Your vault couldn't be checked just now.</p>
          <button className="v2-btn quiet" disabled={busy} onClick={refresh}>Try again</button>
        </div>
      )}
      {recipientKnown && (
      <section className={`v2-vault-status${sealed ? " is-sealed" : ""}`} aria-live="polite">
        {sealed ? <Lock /> : <Shield />}
        <div>
          {sealed && recipient ? (
            <>
              <p className="v2-vault-line">Sealed for {recipient.name}</p>
              <p>Only {recipient.name}'s phone can open it, after our team's checks and a waiting period. You hear first, and one tap cancels.</p>
            </>
          ) : (
            <>
              <p className="v2-vault-line">Not sealed for anyone yet</p>
              <p>Choose who receives it, so that one day it reaches them and nobody else.</p>
              <button className="v2-btn gold" onClick={onFamily}>Choose who receives it</button>
            </>
          )}
        </div>
      </section>
      )}

      {items && (
      <section className="v2-panel" aria-labelledby="vault-items">
        <h2 id="vault-items">{items?.length ? `${items.length === 1 ? "1 secret" : `${items.length} secrets`} locked` : "Nothing locked yet"}</h2>
        {items?.length ? (
          <ul className="v2-secrets">
            {items.map((v) => (
              <li key={v.id}>
                <Lock />
                <span className="v2-secret-name">{v.ai_label ?? "A secret with a private label"}</span>
                <span className="v2-secret-kind">{CATEGORY_LABEL[v.category] ?? v.category}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {items?.some((v) => !v.ai_label) && (
          <p className="v2-muted v2-small">A private label is locked along with its secret, so it shows here only as “a secret with a private label”. {recipient?.name ?? "Your recipient"} sees it when the vault is opened.</p>
        )}
      </section>
      )}

      <section className="v2-panel" aria-labelledby="vault-add">
        <h2 id="vault-add">Lock a new secret</h2>
        <fieldset className="v2-chips">
          <legend className="v2-visually-hidden">What kind of secret</legend>
          {CATEGORIES.map((c) => (
            <button key={c.id} className="v2-chip" aria-pressed={form.category === c.id}
              onClick={() => setForm({ ...form, category: c.id })}>{c.label}</button>
          ))}
        </fieldset>
        <div className="v2-field">
          <label htmlFor="v2-secret-label">What is it?</label>
          <input id="v2-secret-label" className="v2-input" value={form.label} placeholder="e.g. Email login"
            onChange={(e) => setForm({ ...form, label: e.target.value })} />
        </div>
        <div className="v2-field">
          <label htmlFor="v2-secret">The secret</label>
          <div className="v2-row">
            <input id="v2-secret" className="v2-input v2-grow" value={form.secret} type={reveal ? "text" : "password"}
              autoComplete="off" spellCheck={false} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
            <button className="v2-btn quiet" onClick={() => setReveal(!reveal)} aria-pressed={reveal}>{reveal ? "Hide" : "Show"}</button>
          </div>
        </div>
        <label className="v2-check">
          <input type="checkbox" checked={form.saathiMaySeeLabel} onChange={(e) => setForm({ ...form, saathiMaySeeLabel: e.target.checked })} />
          Saathi may see what it is (“{form.label || "Email login"}”), never the secret, so it can spot gaps.
        </label>
        <button className="v2-btn" disabled={!form.label.trim() || !form.secret || busy} onClick={add}>
          <Lock />Lock it on this phone
        </button>
        {added && <p className="v2-ok" role="status">“{added}” is locked in your vault.</p>}
        {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
      </section>
    </div>
  );
}
