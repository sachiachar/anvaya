import { useEffect, useState } from "react";
import { api, type Case } from "./api";
import { Card, ErrorLine, FilePick, useAction } from "./ui";
import {
  createRecipientKeys,
  decryptSecret,
  forgetRecipientKeyOnThisDevice,
  hasRecipientKeys,
  myRecipientFingerprint,
  newRecoveryCode,
  normalizeRecoveryCode,
  restoreRecipientKey,
  unwrapAsRecipient,
} from "./vaultCrypto";

export default function Family() {
  const [status, setStatus] = useState<Case | null>(null);
  const refresh = () => api.releaseStatus().then((r) => setStatus(r.case)).catch(() => {});
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);
  const released = status?.state === "released";

  return (
    <>
      <RequestAccess />
      <AcceptRole />
      <Release status={status} onChange={refresh} />
      {released && (
        <>
          <OpenVault />
          <AskAssistant />
          <SettleEstate />
        </>
      )}
    </>
  );
}

/** For family members Vikram never listed, e.g. his son Arjun. */
function RequestAccess() {
  const [form, setForm] = useState({ name: "Arjun Rao", relationship: "son", contact: "", note: "" });
  const [reply, setReply] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  return (
    <Card title="Not listed? Request access" level="propose">
      {reply ? (
        <p className="ok">{reply}</p>
      ) : (
        <>
          <p className="muted">While Vikram is alive, he decides. After a death, our team checks proof of your relationship. The vault always needs separate legal proof.</p>
          <div className="stack">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Your name" aria-label="Your name" />
            <input value={form.relationship} onChange={(e) => setForm({ ...form, relationship: e.target.value })} placeholder="Relationship, e.g. son" aria-label="Relationship" />
            <input value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} placeholder="Phone or email" aria-label="Phone or email" />
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="A short note (optional)" aria-label="Note" />
            <button disabled={busy || !form.name || !form.relationship || !form.contact}
              onClick={() => run(async () => setReply((await api.requestAccess(form)).message))}>Send request</button>
          </div>
        </>
      )}
      <ErrorLine error={error} />
    </Card>
  );
}

/**
 * Priya's key is not tied to one phone: the private key is backed up, encrypted
 * with a recovery code only she has, and any phone can restore it.
 */
function AcceptRole() {
  const [onDevice, setOnDevice] = useState(hasRecipientKeys());
  const [registered, setRegistered] = useState<boolean | null>(null);
  const [newCode, setNewCode] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [myCode, setMyCode] = useState<string | null>(null);
  const [invite, setInvite] = useState<{ name: string; relationship: string } | null>(null);
  const { busy, error, run } = useAction();

  const refresh = () => {
    api.getRecipient().then((r) => { setRegistered(true); setStatus(r.status); }).catch(() => setRegistered(false));
    api.getInvite().then(setInvite).catch(() => setInvite(null));
    myRecipientFingerprint().then(setMyCode);
  };
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  async function createKey() {
    const recovery = newRecoveryCode();
    const { publicJwk, backup } = await createRecipientKeys(recovery);
    await api.setRecipient(invite?.name ?? "", invite?.relationship ?? "", publicJwk, backup);
    setNewCode(recovery);
    setSaved(false);
    setOnDevice(true);
    setRegistered(true);
    refresh();
  }

  async function restore() {
    const { key_backup } = await api.getRecipient();
    await restoreRecipientKey(key_backup, code);
    setCode("");
    setOnDevice(true);
  }

  return (
    <Card title="Vault recipient" level="act_within_limits">
      {newCode && !saved ? (
        <div className="recovery">
          <p><strong>Save your recovery code.</strong> You need it to set up a new or replacement phone. Anvaya cannot see it or reset it.</p>
          <p className="code">{newCode}</p>
          <label className="check">
            <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
            I have written it down or saved it in my password manager
          </label>
        </div>
      ) : registered === false ? (
        invite && !invite.name.trim().toLowerCase().startsWith("priya") ? (
          <p className="muted">Vikram has chosen {invite.name} ({invite.relationship}), not you, to receive his vault. To try this part, switch to Vikram and choose Priya.</p>
        ) : invite ? (
          <>
            <p><strong>Vikram has chosen you, {invite.name} ({invite.relationship}), to receive his vault.</strong></p>
            <p className="muted">Accepting creates your key and a recovery code. The key is backed up in encrypted form, so any of your phones can use it.</p>
            <button disabled={busy} onClick={() => run(createKey)}>Accept as {invite.name}</button>
          </>
        ) : (
          <p className="muted">Vikram hasn't chosen who receives his vault yet. He does that in his app.</p>
        )
      ) : onDevice ? (
        <>
          <p className="ok">✓ This phone has the vault recipient's key{invite ? ` (${invite.name})` : ""}.</p>
          {status === "pending_owner" && (
            <p className="warn">Waiting for Vikram to approve. He should see this code: <strong className="code-inline">{myCode}</strong></p>
          )}
          {status === "approved" && <p className="ok">✓ Approved by Vikram (key code {myCode}).</p>}
          <button className="link" onClick={() => { forgetRecipientKeyOnThisDevice(); setOnDevice(false); }}>
            Simulate a lost or new phone
          </button>
        </>
      ) : registered ? (
        <>
          <p><strong>Set up this phone.</strong> Enter Priya's recovery code to download and unlock her key.</p>
          <div className="row">
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
              aria-label="Recovery code" autoCapitalize="characters" autoComplete="off" spellCheck={false} />
            <button disabled={busy || normalizeRecoveryCode(code).length < 29} onClick={() => run(restore)}>Restore</button>
          </div>
          <details>
            <summary>Lost the recovery code too?</summary>
            <p className="muted">While Vikram is alive, create a new key. His app will ask him to re-seal the vault for it. After a death this is no longer possible, so keep the code safe.</p>
            <button className="secondary" disabled={busy} onClick={() => run(createKey)}>Create a new key</button>
          </details>
        </>
      ) : null}
      <ErrorLine error={error} />
    </Card>
  );
}

function Release({ status, onChange }: { status: Case | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <Card title="Start the handover" level="assist">
      {!status || status.state === "cancelled" ? (
        <>
          <p className="muted">Upload the death certificate. The AI pre-checks it; a person on our team confirms.</p>
          <FilePick label={busy ? "Checking…" : "Upload death certificate"} accept="image/*,application/pdf" disabled={busy}
            onFile={(f) => run(async () => { await api.reportDeath(f); onChange(); })} />
        </>
      ) : (
        <ol className="steps">
          <li className="done">Death reported</li>
          <li className={status.state !== "reported" ? "done" : ""}>Death confirmed by our team</li>
          <li className={status.authority_check ? "done" : ""}>
            Proof of legal authority
            {status.state === "death_confirmed" && !status.authority_check && (
              <FilePick label={busy ? "Checking…" : "Upload proof"} accept="image/*,application/pdf" disabled={busy}
                onFile={(f) => run(async () => { await api.submitAuthority(f); onChange(); })} />
            )}
          </li>
          <li className={["waiting", "released"].includes(status.state) ? "done" : ""}>
            Legal authority confirmed · waiting period
            {status.state === "waiting" && status.wait_until && (
              <span className="muted"> ({Math.max(0, Math.ceil(status.wait_until - now))}s left; Vikram's devices are alerted)</span>
            )}
          </li>
          <li className={status.state === "released" ? "done" : ""}>Vault released to Priya</li>
        </ol>
      )}
      <ErrorLine error={error} />
    </Card>
  );
}

function OpenVault() {
  const [items, setItems] = useState<{ category: string; label: string; secret: string }[] | null>(null);
  const { busy, error, run } = useAction();
  return (
    <Card title="Open the vault" level="act_within_limits">
      <p className="muted">Decrypted on this device with Priya's private key. The server never sees the contents.</p>
      <button disabled={busy} onClick={() => run(async () => {
        const bundle = await api.releaseBundle();
        const key = await unwrapAsRecipient(bundle.recipient_wrapped_key);
        setItems(await Promise.all(bundle.items.map(async (v) => ({
          category: v.category,
          ...(await decryptSecret<{ label: string; secret: string }>(key, v.iv, v.ciphertext)),
        }))));
      })}>Unlock</button>
      <ErrorLine error={error} />
      {items && (
        <ul className="list">
          {items.length === 0 && <li className="muted">The vault is empty.</li>}
          {items.map((v, i) => <li key={i}><span className="tag">{v.category}</span> <strong>{v.label}</strong>: <code>{v.secret}</code></li>)}
        </ul>
      )}
    </Card>
  );
}

function AskAssistant() {
  const [q, setQ] = useState("");
  const [session, setSession] = useState<string | undefined>();
  const [thread, setThread] = useState<{ q: string; a: string; sources: { id: string; title: string }[] }[]>([]);
  const { busy, error, run } = useAction();
  const suggestions = ["What insurance did Papa have?", "पापा ने पैसे बचाने के बारे में क्या कहा था?", "Did Papa have any crypto?"];

  const send = (question: string) => run(async () => {
    const r = await api.ask(question, session);
    setSession(r.session_id);
    setThread((t) => [...t, { q: question, a: r.answer, sources: r.sources }]);
    setQ("");
  });

  return (
    <Card title="Ask about Papa" level="act_within_limits">
      <p className="muted">Answers come only from Vikram's own words and records, with sources.</p>
      <div className="chips">
        {suggestions.map((s) => <button key={s} className="chip" disabled={busy} onClick={() => send(s)}>{s}</button>)}
      </div>
      <ul className="thread">
        {thread.map((t, i) => (
          <li key={i}>
            <p className="q">{t.q}</p>
            <p className="a">{t.a}</p>
            {t.sources.length > 0 && <p className="muted">From what Vikram kept: {t.sources.map((s) => s.title).join(" · ")}</p>}
          </li>
        ))}
      </ul>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question" aria-label="Question"
          onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) send(q.trim()); }} />
        <button disabled={!q.trim() || busy} onClick={() => send(q.trim())}>{busy ? "…" : "Ask"}</button>
      </div>
      <ErrorLine error={error} />
    </Card>
  );
}

function SettleEstate() {
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof api.settlement>> | null>(null);
  const { busy, error, run } = useAction();
  return (
    <Card title="Settle the estate" level="propose">
      <p className="muted">A checklist and draft letters built from Vikram's financial inventory. Priya reviews and sends them.</p>
      <button disabled={busy} onClick={() => run(async () => setPlan(await api.settlement()))}>
        {busy ? "Preparing…" : "Prepare checklist and letters"}
      </button>
      <ErrorLine error={error} />
      {plan && (
        <div className="result">
          <ul className="list">
            {plan.checklist.map((c, i) => (
              <li key={i}>
                <span className={`tag sev-${c.priority}`}>{c.priority}</span> <strong>{c.institution}</strong>: {c.action}
                <p className="muted">Needs: {c.documents_needed.join(", ")}</p>
              </li>
            ))}
          </ul>
          {plan.letters.map((l, i) => (
            <details key={i}>
              <summary>Draft letter: {l.institution}</summary>
              <p><strong>{l.subject}</strong></p>
              <pre className="letter">{l.body}</pre>
            </details>
          ))}
          <p><strong>Also search:</strong> {plan.unclaimed_asset_searches.join(" · ")}</p>
        </div>
      )}
    </Card>
  );
}
