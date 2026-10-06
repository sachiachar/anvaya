// The family side (v2): one stage at a time. Before anything happens, the chosen person accepts
// on their own phone and keeps a recovery code; when the time comes, they start the handover and
// follow it in plain steps; after release, they open the vault, ask about their parent and get
// the claim letters. The logic follows the classic Family.tsx (copied); all cryptography stays in
// vaultCrypto.ts, and the vault is decrypted on this phone only.
import { useEffect, useState } from "react";
import { api, type Case } from "../../api";
import { useAction } from "../../ui";
import {
  createRecipientKeys, hasRecipientKeys, myRecipientFingerprint, newRecoveryCode, normalizeRecoveryCode, restoreRecipientKey,
} from "../../vaultCrypto";
import { friendlyError } from "../copy";
import FilePick from "../FilePick";
import { Check } from "../icons";
import After from "./After";
import Tracker from "./Tracker";

type Recipient = Awaited<ReturnType<typeof api.getRecipient>>;

export default function Journey({ me }: { me: string }) {
  const [owner, setOwner] = useState("");
  const [status, setStatus] = useState<Case | null>(null);
  const [statusKnown, setStatusKnown] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [keyHere, setKeyHere] = useState(hasRecipientKeys()); // this phone holds the recipient's key

  useEffect(() => {
    api.owner().then((o) => setOwner(String(o.name ?? "").split(" ")[0])).catch(() => {});
    const refresh = () => api.releaseStatus()
      .then((r) => { setStatus(r.case); setStatusKnown(true); setStatusError(null); setKeyHere(hasRecipientKeys()); })
      .catch((e) => setStatusError(friendlyError(e)));
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);

  const parent = owner || "your family member";
  const open = status && status.state !== "cancelled" ? status : null;
  return (
    <div className="v2-stack v2-tab">
      <header className="v2-tab-head">
        <h1>{open?.state === "released" ? `${parent}'s vault is yours now` : `For you, ${me}`}</h1>
        <p>{open?.state === "released"
          ? `Everything ${parent} kept for the family, opened on this phone only.`
          : open ? "Here is where things stand. You don't need to do anything you're not ready for."
          : `What ${parent} chose to hand on to you, kept safe until the time comes.`}</p>
      </header>
      {statusError && !statusKnown && <p className="v2-error" role="alert">{statusError}</p>}
      {open?.state === "released" ? (
        <>
          {/* On a new phone the key has to be restored (with the recovery code) before the vault opens. */}
          {!keyHere && <Recipient me={me} parent={parent} />}
          {/* Keyed on the key: if it leaves this phone, the opened vault closes too. */}
          <After key={String(keyHere)} parent={parent} />
        </>
      ) : (
        <>
          <Recipient me={me} parent={parent} />
          {statusKnown && (open ? <Tracker status={open} parent={parent} /> : <WhenTheTimeComes parent={parent} />)}
          <RequestAccess parent={parent} />
        </>
      )}
    </div>
  );
}

/** Accepting the vault, keeping the recovery code, matching the code, or restoring on a new phone. */
function Recipient({ me, parent }: { me: string; parent: string }) {
  const [onDevice, setOnDevice] = useState(hasRecipientKeys());
  const [recipient, setRecipient] = useState<Recipient | null>(null);
  const [registered, setRegistered] = useState<boolean | null>(null);
  const [invite, setInvite] = useState<{ name: string; relationship: string } | null>(null);
  const [myCode, setMyCode] = useState<string | null>(null);
  const [newCode, setNewCode] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [code, setCode] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const { busy, error, run } = useAction();

  const refresh = async () => {
    try {
      const r = await api.getRecipient().catch((e) => { if (/no vault recipient/.test(String(e?.message))) return null; throw e; });
      setRecipient(r);
      setRegistered(Boolean(r));
      setInvite(await api.getInvite().catch((e) => { if (/hasn't chosen a vault recipient/.test(String(e?.message))) return null; throw e; }));
      setMyCode(await myRecipientFingerprint());
      setOnDevice(hasRecipientKeys());
      setLoadError(null);
    } catch (e) {
      setLoadError(friendlyError(e));
    }
  };
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 5000);
    return () => clearInterval(t);
  }, []);

  /** Accept: a key pair made on this phone, its private half backed up locked with a recovery code. */
  const accept = () => run(async () => {
    const recovery = newRecoveryCode();
    const { publicJwk, backup } = await createRecipientKeys(recovery);
    await api.setRecipient(invite?.name ?? "", invite?.relationship ?? "", publicJwk, backup);
    setNewCode(recovery);
    setSaved(false);
    await refresh();
  });
  const restore = () => run(async () => {
    const { key_backup } = await api.getRecipient();
    await restoreRecipientKey(key_backup, code);
    setCode("");
    await refresh();
  });

  const forMe = Boolean(invite?.name.trim().toLowerCase().startsWith(me.toLowerCase()));
  let body: React.ReactNode = null;
  if (newCode && !saved) {
    body = (
      <div className="v2-stack">
        <p><strong>Keep your recovery code safe.</strong> You'll need it to set up a new or replacement phone. Nobody at Anvaya can see it or reset it.</p>
        <p className="v2-code">{newCode}</p>
        <label className="v2-check">
          <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
          I've written it down, or saved it in my password manager
        </label>
      </div>
    );
  } else if (registered === false) {
    body = !invite ? (
      <p className="v2-muted">{parent} hasn't chosen who receives the vault yet. That's done in their own app.</p>
    ) : !forMe ? (
      <p className="v2-muted">{parent} has chosen {invite.name} ({invite.relationship}) to receive the vault.</p>
    ) : (
      <div className="v2-stack">
        <p><strong>{parent} has chosen you to receive the vault one day.</strong> Passwords, PINs and account details locked there reach only you, after careful checks.</p>
        <p className="v2-muted v2-small">Accepting makes a key on this phone and a recovery code for you to keep. The key is backed up locked, so any of your phones can use it.</p>
        <button className="v2-btn" disabled={busy} onClick={accept}>Accept as {invite.name}</button>
      </div>
    );
  } else if (registered && onDevice) {
    body = recipient?.status === "approved" ? (
      <p className="v2-sealed-line"><Check /> You're set up. {parent} approved this phone (key code {myCode}).</p>
    ) : (
      <div className="v2-stack">
        <p>Waiting for {parent} to approve. Their phone should show this same code:</p>
        <p className="v2-code">{myCode}</p>
      </div>
    );
  } else if (registered && !onDevice) {
    body = (
      <div className="v2-stack">
        <p><strong>Set up this phone.</strong> Enter your recovery code to bring your key to this phone.</p>
        <div className="v2-row">
          <input id="v2-recovery" className="v2-input v2-grow v2-mono" value={code} onChange={(e) => setCode(e.target.value)}
            placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" aria-label="Recovery code" autoCapitalize="characters" autoComplete="off" spellCheck={false} />
          <button className="v2-btn" disabled={busy || normalizeRecoveryCode(code).length < 29} onClick={restore}>Restore</button>
        </div>
        <details className="v2-more">
          <summary>Lost the recovery code too?</summary>
          <p className="v2-muted v2-small">While {parent} is here, you can make a new key; their app will ask them to seal the vault again for it. When the time comes this is no longer possible, so keep the code safe.</p>
          <button className="v2-btn quiet" disabled={busy} onClick={accept}>Make a new key</button>
        </details>
      </div>
    );
  }

  return (
    <section className="v2-panel" aria-labelledby="recipient-title">
      <p className="v2-eyebrow">The vault</p>
      <h2 id="recipient-title">Receiving {parent}'s vault</h2>
      {registered === null && !loadError && <p className="v2-muted">Loading…</p>}
      {loadError && registered === null && (
        <div className="v2-stack">
          <p className="v2-error" role="alert">{loadError}</p>
          <button className="v2-btn quiet" onClick={() => void refresh()}>Try again</button>
        </div>
      )}
      {body}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

/** Before anything happens: what the handover involves, then the first step. */
function WhenTheTimeComes({ parent }: { parent: string }) {
  const { busy, error, run } = useAction();
  return (
    <section className="v2-panel" aria-labelledby="time-title">
      <p className="v2-eyebrow">When the time comes</p>
      <h2 id="time-title">If {parent} is no longer here</h2>
      <ol className="v2-explain">
        <li>You upload the official certificate of passing.</li>
        <li>Careful checks look for mistakes or forgery, then two different people on our team confirm it: one confirms the loss, the other your legal authority.</li>
        <li>A waiting period follows. {parent}'s phone is told first, and one tap from them cancels everything.</li>
        <li>Then the vault opens on your phone, and only yours.</li>
      </ol>
      <FilePick label={busy ? "Checking…" : "Start the handover: upload the certificate"} accept="image/*,application/pdf" disabled={busy}
        onFile={(f) => run(async () => { await api.reportDeath(f); })} />
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

/** For family members who were never listed. While the owner is here, only the owner decides. */
function RequestAccess({ parent }: { parent: string }) {
  const [form, setForm] = useState({ name: "", relationship: "", contact: "", note: "" });
  const [reply, setReply] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  return (
    <details className="v2-more v2-panel">
      <summary>Not listed? Ask to be added</summary>
      {reply ? <p className="v2-ok">{reply}</p> : (
        <div className="v2-stack">
          <p className="v2-muted v2-small">While {parent} is here, {parent} decides. Later, our team checks proof of your relationship. The vault always needs separate legal proof.</p>
          <input id="v2-ra-name" className="v2-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Your name" aria-label="Your name" />
          <input id="v2-ra-rel" className="v2-input" value={form.relationship} onChange={(e) => setForm({ ...form, relationship: e.target.value })} placeholder="Relationship, e.g. son" aria-label="Relationship" />
          <input id="v2-ra-contact" className="v2-input" value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} placeholder="Phone or email" aria-label="Phone or email" />
          <input id="v2-ra-note" className="v2-input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="A short note (optional)" aria-label="Note" />
          <button className="v2-btn" disabled={busy || !form.name.trim() || !form.relationship.trim() || !form.contact.trim()}
            onClick={() => run(async () => setReply((await api.requestAccess(form)).message))}>Send request</button>
        </div>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </details>
  );
}
