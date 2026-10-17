// Vault (v2): passwords, PINs and account details, locked on this phone before they leave it. The
// server only ever holds the locked form; the screen shows what each secret is, never its locked
// text. The owner can show one of his own secrets on this phone, opened with his key here. His key
// is also kept in the cloud, locked with his recovery code, so a new phone can open the vault
// (PR-14.9). All encryption lives in vaultCrypto.ts.
import { useEffect, useState } from "react";
import { api, type VaultItem } from "../../api";
import { useAction } from "../../ui";
import {
  backupOwnerKey, decryptSecret, encryptSecret, forgetOwnerKeyOnThisDevice, type KeyBackup, newRecoveryCode,
  normalizeRecoveryCode, ownerDataKey, ownerKeyId, restoreOwnerKey,
} from "../../vaultCrypto";
import { friendlyError } from "../copy";
import { Lock, Shield } from "../icons";
import { KEY_NOT_HERE, type KeyPlace, keyForLocking, keyPlace } from "./ownerKey";

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
  // undefined: not checked yet.
  const [backup, setBackup] = useState<KeyBackup | null | undefined>(undefined);
  const [localKeyId, setLocalKeyId] = useState<string | null | undefined>(undefined);
  const { busy, error, run } = useAction();

  const [checkError, setCheckError] = useState<string | null>(null);
  // Separately, and honestly: if either can't be checked, say so rather than "not sealed" or "nothing locked".
  const refresh = () => run(async () => {
    setCheckError(null);
    setLocalKeyId(await ownerKeyId());
    const [list, who, kept] = await Promise.allSettled([
      api.vaultItems(),
      api.getRecipient().catch((e) => { if (/no vault recipient/.test(String(e?.message))) return null; throw e; }),
      api.ownerKeyBackup(),
    ]);
    if (list.status === "fulfilled") setItems(list.value.items);
    if (who.status === "fulfilled") { setRecipient(who.value); setRecipientKnown(true); }
    if (kept.status === "fulfilled") setBackup(kept.value);
    const failed = [list, who, kept].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    if (failed) setCheckError(friendlyError(failed.reason));
  });
  useEffect(() => {
    refresh();
    // Another tab may bring or forget the key.
    const onStorage = (e: StorageEvent) => { if (e.key?.endsWith(".owner.dataKey")) void refresh(); };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  /** Locked on this phone first; only the locked form is sent. */
  const add = () => run(async () => {
    const { iv, ciphertext } = await encryptSecret(await keyForLocking(), { label: form.label, secret: form.secret });
    await api.addVaultItem({ category: form.category, iv, ciphertext, ai_label: form.saathiMaySeeLabel ? form.label : null });
    setAdded(form.label);
    setForm({ ...form, label: "", secret: "" });
    setReveal(false);
    await refresh();
  });

  const sealed = Boolean(recipient?.vault_sealed);
  // Known once the list, the seal and the backup are checked; until then nothing offers to lock.
  const place: KeyPlace | null = items && recipientKnown && backup !== undefined && localKeyId !== undefined
    ? keyPlace(localKeyId, backup, items.length, sealed) : null;
  const lockBlocked = place === "inCloud" || place === "elsewhere";
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
            {items.map((v) => <SecretRow key={v.id} item={v} canShow={place === "here"} />)}
          </ul>
        ) : null}
        {items?.some((v) => !v.ai_label) && (
          <p className="v2-muted v2-small">A private label is locked along with its secret, so it shows here only as “a secret with a private label”, until you show it. {recipient?.name ?? "Your recipient"} sees it when the vault is opened.</p>
        )}
        {place && (
          <OwnerKeyPanel place={place} backup={backup ?? null} sealedFor={sealed ? recipient?.name ?? null : null} onChange={refresh} />
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
        <button className="v2-btn" disabled={!form.label.trim() || !form.secret || busy || !place || lockBlocked} onClick={add}>
          <Lock />Lock it on this phone
        </button>
        {lockBlocked && (
          <p className="v2-muted v2-small">{place === "inCloud" ? "Enter your recovery code above first, to bring your key to this phone." : KEY_NOT_HERE.elsewhere}</p>
        )}
        {added && <p className="v2-ok" role="status">“{added}” is locked in your vault.</p>}
        {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
      </section>
    </div>
  );
}

const SHOWN_FOR_MS = 30_000; // a shown secret hides itself, so it isn't left open on the screen

/** One locked secret. "Show" opens it on this phone with the owner's own key (PR-14.9): the plain
 *  text stays in this row's memory only, is never sent, logged or stored, and hides itself after 30
 *  seconds, on Hide, or when the tab closes. Show is offered only with his key on this phone. */
function SecretRow({ item, canShow }: { item: VaultItem; canShow: boolean }) {
  const [shown, setShown] = useState<{ label: string; secret: string } | null>(null);
  const [cantShow, setCantShow] = useState(false);
  const [opening, setOpening] = useState(false);
  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(null), SHOWN_FOR_MS);
    return () => clearTimeout(t);
  }, [shown]);

  const show = async () => {
    setCantShow(false);
    setOpening(true);
    try {
      const key = await ownerDataKey();
      if (!key) throw new Error("no key on this phone");
      setShown(await decryptSecret<{ label: string; secret: string }>(key, item.iv, item.ciphertext));
    } catch {
      setCantShow(true); // locked with another key, or the key was forgotten meanwhile
    } finally {
      setOpening(false);
    }
  };

  const name = shown?.label ?? item.ai_label ?? "A secret with a private label";
  return (
    <li className={shown ? "is-shown" : undefined}>
      <Lock />
      <span className="v2-secret-text">
        <span className="v2-secret-name">{name}</span>
        <span className="v2-secret-kind">{CATEGORY_LABEL[item.category] ?? item.category}</span>
        {shown && <code className="v2-secret-value" aria-live="polite">{shown.secret}</code>}
        {cantShow && <span className="v2-secret-cant" role="status">This secret was locked with another key, so it can't be shown here.</span>}
      </span>
      {(canShow || shown) && <button className="v2-link" disabled={opening} onClick={() => (shown ? setShown(null) : void show())}
        aria-label={shown ? `Hide “${name}”` : `Show “${name}”`}>
        {shown ? "Hide" : opening ? "Opening…" : "Show"}
      </button>}
    </li>
  );
}

/** Where the key that opens these secrets is, and what to do about it (PR-14.9): keep it in the
 *  cloud with a recovery code, bring it to this phone with the code, or forget it on a shared one. */
function OwnerKeyPanel({ place, backup, sealedFor, onChange }: {
  place: KeyPlace; backup: KeyBackup | null; sealedFor: string | null; onChange: () => Promise<unknown>;
}) {
  const [newCode, setNewCode] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [code, setCode] = useState("");
  const { busy, error, run } = useAction();

  const makeCode = () => { setNewCode(newRecoveryCode()); setSaved(false); };
  const keep = () => run(async () => {
    if (!newCode) return;
    await api.setOwnerKeyBackup(await backupOwnerKey(newCode));
    setNewCode(null);
    await onChange();
  });
  const restore = () => run(async () => {
    if (!backup) return;
    await restoreOwnerKey(backup, code);
    setCode("");
    await onChange();
  });
  const forget = () => run(async () => { forgetOwnerKeyOnThisDevice(); await onChange(); });

  const codeStep = newCode && (
    <div className="v2-stack">
      <p><strong>Keep your recovery code safe.</strong> You need it to open your vault on a new phone. Nobody at Anvaya can see it or reset it.</p>
      <p className="v2-code">{newCode}</p>
      <label className="v2-check">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I've written it down, or saved it in my password manager
      </label>
      <div className="v2-row">
        <button className="v2-btn" disabled={!saved || busy} onClick={keep}>Keep my key in the cloud</button>
        <button className="v2-btn quiet" disabled={busy} onClick={() => setNewCode(null)}>Not now</button>
      </div>
    </div>
  );
  const lostFor = sealedFor ? `for you. ${sealedFor} can still open them when the time comes.` : "for good.";

  let body: React.ReactNode = null;
  if (place === "here" && !backup) {
    body = codeStep || (
      <>
        <p><b>Your key is only in this browser.</b> Keep it in the cloud, locked with a recovery code, so a new phone can open your vault. Until then, clearing this browser loses these secrets {lostFor}</p>
        <button className="v2-btn" disabled={busy} onClick={makeCode}>Make my recovery code</button>
      </>
    );
  } else if (place === "here") {
    body = codeStep || (
      <>
        <p><b>Your key is kept in the cloud, locked with your recovery code.</b> On a new phone, enter the code here to open your vault. Anvaya can't open it.</p>
        <details className="v2-more">
          <summary>Lost the code, or using a shared phone?</summary>
          <p className="v2-muted v2-small">A new code replaces the old one. Forgetting the key here keeps it in the cloud, and the code brings it back.</p>
          <div className="v2-row">
            <button className="v2-btn quiet" disabled={busy} onClick={makeCode}>Make a new code</button>
            <button className="v2-btn quiet" disabled={busy} onClick={forget}>Forget the key on this phone</button>
          </div>
        </details>
      </>
    );
  } else if (place === "inCloud") {
    body = (
      <>
        <p><b>Open your vault on this phone.</b> Enter your recovery code to bring your key here.</p>
        <div className="v2-row">
          <input className="v2-input v2-grow v2-mono" value={code} onChange={(e) => setCode(e.target.value)}
            placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" aria-label="Recovery code" autoCapitalize="characters" autoComplete="off" spellCheck={false} />
          <button className="v2-btn" disabled={busy || normalizeRecoveryCode(code).length < 29} onClick={restore}>Open</button>
        </div>
      </>
    );
  } else if (place === "elsewhere") {
    body = <p>{KEY_NOT_HERE.elsewhere} If that browser's data was cleared, these secrets are lost {lostFor}</p>;
  }
  if (!body) return null;
  return (
    <div className="v2-key-note v2-stack">
      {body}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </div>
  );
}
