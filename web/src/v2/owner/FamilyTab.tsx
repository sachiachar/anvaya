// Family (v2): who receives the vault, and who may see the stories. Choosing the recipient is a
// three-step handshake: invite, they accept on their own phone, the owner approves only if both
// phones show the same code, then seals the vault for them. All cryptography stays in vaultCrypto.ts.
import { useContext, useEffect, useState } from "react";
import { type AccessRequest, api } from "../../api";
import { useAction } from "../../ui";
import { keyFingerprint, wrapForRecipient } from "../../vaultCrypto";
import { keyForLocking } from "./ownerKey";
import { friendlyError } from "../copy";
import { Check, Lock } from "../icons";
import { Nav } from "../nav";
import SwitchTo from "../SwitchTo";

type Recipient = Awaited<ReturnType<typeof api.getRecipient>>;

/** "Meena (wife)" -> { name: "Meena", relationship: "wife" } */
const parse = (f: string) => {
  const m = f.match(/^(.*?)\s*\((.*)\)$/);
  return m ? { name: m[1], relationship: m[2] } : { name: f, relationship: "" };
};

export default function FamilyTab({ onVault }: { onVault: () => void }) {
  return (
    <div className="v2-stack v2-tab">
      <header className="v2-tab-head">
        <h1>Your family</h1>
        <p>Who receives your vault when the time comes, and who may see your stories.</p>
      </header>
      <VaultRecipient onVault={onVault} />
      <People />
    </div>
  );
}

function VaultRecipient({ onVault }: { onVault: () => void }) {
  const [recipient, setRecipient] = useState<Recipient | null>(null);
  const [invite, setInvite] = useState<{ name: string; relationship: string } | null>(null);
  const [family, setFamily] = useState<string[]>([]);
  const [choice, setChoice] = useState({ name: "", relationship: "" });
  const [code, setCode] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const { busy, error, run } = useAction();
  const nav = useContext(Nav);
  // The demo's family member who can accept, when the invite is for them (PR-14.2).
  const invitee = nav.people.find((p) => p.role === "family" && invite?.name.trim().toLowerCase().startsWith(p.name.toLowerCase()));

  const [loadError, setLoadError] = useState<string | null>(null);
  const [heldNote, setHeldNote] = useState<string | null>(null);  // a change the guardian holds for our team
  // Each part on its own: a slow or failed family list never blocks approving or sealing.
  const refresh = () => run(async () => {
    setLoadError(null);
    try {
      const r = await api.getRecipient().catch((e) => { if (/no vault recipient/.test(String(e?.message))) return null; throw e; });
      setRecipient(r);
      setCode(r ? await keyFingerprint(r.public_key_jwk) : null);
      setInvite(await api.getInvite().catch((e) => { if (/hasn't chosen a vault recipient/.test(String(e?.message))) return null; throw e; }));
      setLoaded(true);
    } catch (e) {
      setLoadError(friendlyError(e));
    }
    api.owner().then((o) => setFamily(o.family ?? [])).catch(() => { /* typing a name still works */ });
  });
  useEffect(() => { refresh(); }, []);

  const sendInvite = () => run(async () => {
    const r = await api.inviteRecipient(choice.name.trim(), choice.relationship.trim());
    setHeldNote(r.held ? r.message ?? null : null);
    setChoice({ name: "", relationship: "" });
    await refresh();
  });
  const decide = (d: "approve" | "decline") => run(async () => { await api.decideRecipient(d); await refresh(); });
  /** Seal: the vault's key is wrapped, on this phone, for the recipient's public key only. */
  const seal = () => run(async () => {
    const r = await api.getRecipient();
    await api.setEscrow(await wrapForRecipient(await keyForLocking(), r.public_key_jwk));
    await refresh();
  });

  const sealed = Boolean(recipient?.vault_sealed);
  const step = !invite ? 0 : !recipient ? 1 : recipient.status === "pending_owner" ? 2 : !sealed ? 3 : 4;
  const STEPS = ["Choose", "They accept", "Match the code", "Seal"];

  return (
    <section className="v2-panel" aria-labelledby="recipient-title">
      <p className="v2-eyebrow">Your vault</p>
      <h2 id="recipient-title">Who receives your vault</h2>
      {!loaded && !loadError && <p className="v2-muted">Loading…</p>}
      {loadError && (
        <div className="v2-stack">
          <p className="v2-error" role="alert">{loadError}</p>
          <button className="v2-btn quiet" disabled={busy} onClick={refresh}>Try again</button>
        </div>
      )}
      {loaded && (
        <>
          <ol className="v2-handshake" aria-label="Steps">
            {STEPS.map((s, i) => (
              <li key={s} className={i < step ? "is-done" : i === step ? "is-now" : ""}>
                <span aria-hidden="true">{i < step ? <Check /> : i + 1}</span>{s}
              </li>
            ))}
          </ol>

          {sealed && recipient ? (
            <div className="v2-sealed">
              <Lock />
              <div>
                <p><strong>Sealed for {recipient.name}</strong> ({recipient.relationship})</p>
                <p className="v2-muted v2-small">Only {recipient.name}'s phone can open it, after our checks and a waiting period. Key code {code}.</p>
              </div>
            </div>
          ) : recipient?.status === "approved" ? (
            <div className="v2-stack">
              <p>{recipient.name} is approved. Seal your vault so that only {recipient.name} can open it.</p>
              <button className="v2-btn" disabled={busy} onClick={seal}><Lock />Seal my vault for {recipient.name}</button>
            </div>
          ) : recipient?.status === "pending_owner" ? (
            <div className="v2-stack">
              <p>{recipient.name} ({recipient.relationship}) has accepted on their phone. Approve only if their phone shows this same code:</p>
              <p className="v2-code"><span className="v2-visually-hidden">Key code </span>{code}</p>
              <div className="v2-row">
                <button className="v2-btn" disabled={busy} onClick={() => decide("approve")}>The codes match</button>
                <button className="v2-btn quiet" disabled={busy} onClick={() => decide("decline")}>They don't match</button>
              </div>
            </div>
          ) : invite ? (
            <div className="v2-stack">
              <p>You chose <strong>{invite.name}</strong> ({invite.relationship}). Waiting for them to accept in their own app.</p>
              {invitee && <SwitchTo to={invitee.id}>Switch to {invitee.name} to accept</SwitchTo>}
            </div>
          ) : (
            <p className="v2-muted">Usually a son, daughter or spouse. They accept on their own phone, and you confirm a matching code.</p>
          )}

          {(!invite || !recipient) && (
            <div className="v2-stack">
              {family.length > 0 && (
                <fieldset className="v2-chips">
                  <legend className="v2-visually-hidden">Choose from your family</legend>
                  {family.map((f) => {
                    const p = parse(f);
                    const on = choice.name === p.name && choice.relationship === p.relationship;
                    return (
                      <button key={f} className="v2-chip" aria-pressed={on} onClick={() => setChoice(p)}>
                        {p.name}{p.relationship && <span> · {p.relationship}</span>}
                      </button>
                    );
                  })}
                </fieldset>
              )}
              <div className="v2-row">
                <input id="v2-recipient-name" className="v2-input v2-grow" value={choice.name} placeholder="Name"
                  aria-label="Name" onChange={(e) => setChoice({ ...choice, name: e.target.value })} />
                <input id="v2-recipient-rel" className="v2-input v2-grow" value={choice.relationship} placeholder="Relationship"
                  aria-label="Relationship" onChange={(e) => setChoice({ ...choice, relationship: e.target.value })} />
              </div>
              <button className="v2-btn quiet" disabled={busy || !choice.name.trim() || !choice.relationship.trim()} onClick={sendInvite}>
                {invite ? "Choose someone else" : `Invite ${choice.name.trim() || "them"}`}
              </button>
            </div>
          )}
          {sealed && <button className="v2-link" onClick={onVault}>Open your vault</button>}
        </>
      )}
      {heldNote && <p className="v2-muted" role="status">{heldNote}</p>}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

/** Family members who asked for access; while the owner is here, only the owner can let them in. */
function People() {
  const [data, setData] = useState<{ requests: AccessRequest[]; contacts: { name: string; relationship: string; contact: string }[] } | null>(null);
  const { busy, error, run } = useAction();
  const refresh = () => run(async () => setData(await api.ownerAccessRequests()));
  useEffect(() => { refresh(); }, []);
  const decide = (id: string, d: "approve" | "decline") => run(async () => { await api.decideAccess(id, d, "owner"); await refresh(); });

  return (
    <section className="v2-panel" aria-labelledby="people-title">
      <p className="v2-eyebrow">Your stories</p>
      <h2 id="people-title">Who may see your stories</h2>
      {data && data.requests.length === 0 && data.contacts.length === 0 && (
        <p className="v2-muted">Nobody has asked yet. Family members can ask to be added; while you're here, only you can let them in.</p>
      )}
      {data?.requests.map((r) => (
        <div key={r.id} className="v2-request">
          <p><strong>{r.name}</strong> ({r.relationship}) is asking to be added.</p>
          {r.note && <p className="v2-quote">“{r.note}”</p>}
          <div className="v2-row">
            <button className="v2-btn" disabled={busy} onClick={() => decide(r.id, "approve")}>Let them in</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => decide(r.id, "decline")}>Decline</button>
          </div>
        </div>
      ))}
      {data && data.contacts.length > 0 && (
        <ul className="v2-pages">
          {data.contacts.map((c) => (
            <li key={c.contact}><div><p className="v2-page-title">{c.name}</p><p className="v2-page-desc">{c.relationship} · can see your stories when the time comes</p></div></li>
          ))}
        </ul>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}
