// After release (v2): the vault, opened on this phone only; Ask about Papa, answered only from what
// was kept, with where each answer came from; and the claims checklist with draft letters.
import { useState } from "react";
import { api } from "../../api";
import { useAction } from "../../ui";
import { decryptSecret, unwrapAsRecipient } from "../../vaultCrypto";
import { friendlyError } from "../copy";
import { Lock, Send } from "../icons";

export default function After({ parent }: { parent: string }) {
  return (
    <>
      <OpenVault parent={parent} />
      <Ask parent={parent} />
      <Claims parent={parent} />
    </>
  );
}

function OpenVault({ parent }: { parent: string }) {
  const [items, setItems] = useState<{ category: string; label: string; secret: string }[] | null>(null);
  const [shown, setShown] = useState<ReadonlySet<number>>(new Set());
  const { busy, error, run } = useAction();
  /** Unwrapped and decrypted here, with this phone's private key; the server never sees inside. */
  const unlock = () => run(async () => {
    const bundle = await api.releaseBundle();
    const key = await unwrapAsRecipient(bundle.recipient_wrapped_key);
    setItems(await Promise.all(bundle.items.map(async (v) => ({
      category: v.category, ...(await decryptSecret<{ label: string; secret: string }>(key, v.iv, v.ciphertext)),
    }))));
  });
  return (
    <section className="v2-vault-status is-sealed v2-vault-open" aria-labelledby="open-title">
      <Lock />
      <div>
        <p id="open-title" className="v2-vault-line">{parent}'s vault</p>
        {!items ? (
          <>
            <p>It opens on this phone only, with your key. Nobody else can read it.</p>
            <button className="v2-btn gold" disabled={busy} onClick={unlock}>{busy ? "Opening…" : "Open the vault"}</button>
          </>
        ) : items.length === 0 ? (
          <p>The vault is empty.</p>
        ) : (
          <ul className="v2-secrets is-open">
            {items.map((v, i) => (
              // The vault's own order; two secrets can share a label.
              <li key={`${i}-${v.label}`}>
                <span className="v2-secret-name">{v.label}</span>
                {shown.has(i) ? <code className="v2-secret-value">{v.secret}</code> : (
                  <button className="v2-link" onClick={() => setShown((s) => new Set(s).add(i))}>Show</button>
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
      </div>
    </section>
  );
}

function Ask({ parent }: { parent: string }) {
  const [q, setQ] = useState("");
  const [session, setSession] = useState<string | undefined>();
  const [thread, setThread] = useState<{ q: string; a: string; sources: { id: string; title: string }[] }[]>([]);
  const { busy, error, run } = useAction();
  const suggestions = parent === "your family member"
    ? ["What insurance policies were there?", "Where are the important papers?", "Was there any crypto?"]
    : ["What insurance did Papa have?", "पापा ने पैसे बचाने के बारे में क्या कहा था?", "Did Papa have any crypto?"];
  /** One question at a time, so each answer carries on the same conversation (session). */
  const send = (question: string) => busy ? undefined : run(async () => {
    const r = await api.ask(question, session);
    setSession(r.session_id);
    setThread((t) => [...t, { q: question, a: r.answer, sources: r.sources }]);
    setQ("");
  });
  return (
    <section className="v2-panel" aria-labelledby="ask-title">
      <p className="v2-eyebrow">Ask Saathi</p>
      <h2 id="ask-title">Ask about {parent}</h2>
      <p className="v2-muted v2-small">Answers come only from what {parent} kept, with where each one came from. If it was never said, Saathi says so.</p>
      <div className="v2-chips">
        {suggestions.map((s) => <button key={s} className="v2-chip" disabled={busy} onClick={() => send(s)}>{s}</button>)}
      </div>
      {thread.length > 0 && (
        <ol className="v2-thread v2-ask-thread" aria-live="polite">
          {thread.map((t, i) => (
            <li key={i} className="v2-turn">
              <div className="v2-turn owner"><p>{t.q}</p></div>
              <div className="v2-turn assistant"><p>{t.a}</p></div>
              {t.sources.length > 0 && <p className="v2-picked">From what {parent} kept · {t.sources.map((s) => s.title).join(" · ")}</p>}
            </li>
          ))}
        </ol>
      )}
      <div className="v2-row">
        <input id="v2-ask" className="v2-input v2-grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Ask about ${parent}`}
          aria-label="Question" onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) send(q.trim()); }} />
        <button className="v2-round" disabled={!q.trim() || busy} onClick={() => send(q.trim())} aria-label="Ask"><Send /></button>
      </div>
      {busy && <p className="v2-muted v2-small">Saathi is looking through what {parent} kept…</p>}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

function Claims({ parent }: { parent: string }) {
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof api.settlement>> | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const { busy, error, run } = useAction();
  const copy = (i: number, text: string) => {
    navigator.clipboard?.writeText(text).then(() => setCopied(i)).catch(() => setCopied(null));
  };
  return (
    <section className="v2-panel" aria-labelledby="claims-title">
      <p className="v2-eyebrow">Next steps</p>
      <h2 id="claims-title">Claims and letters</h2>
      <p className="v2-muted v2-small">A checklist and draft letters for each bank and insurer {parent} kept. You check each one and send it yourself.</p>
      {!plan && <button className="v2-btn" disabled={busy} onClick={() => run(async () => setPlan(await api.settlement()))}>{busy ? "Preparing…" : "Prepare the checklist and letters"}</button>}
      {plan && (
        <div className="v2-stack">
          <ul className="v2-steps">
            {plan.checklist.map((c) => (
              <li key={`${c.institution}-${c.action}`} className={`sev-${c.priority}`}>
                <p className="v2-step-title">{c.institution}</p>
                <p className="v2-step-why">{c.action}</p>
                {c.documents_needed.length > 0 && <p className="v2-muted v2-small">You'll need: {c.documents_needed.join(", ")}</p>}
              </li>
            ))}
          </ul>
          {plan.letters.map((l, i) => (
            <details key={`${l.institution}-${l.subject}`} className="v2-more v2-panel">
              <summary>Draft letter to {l.institution}</summary>
              <p><strong>{l.subject}</strong></p>
              <pre className="v2-letter">{l.body}</pre>
              <button className="v2-btn quiet" onClick={() => copy(i, `${l.subject}\n\n${l.body}`)}>{copied === i ? "Copied" : "Copy the letter"}</button>
            </details>
          ))}
          {plan.unclaimed_asset_searches.length > 0 && (
            <p className="v2-muted v2-small"><strong>Also search:</strong> {plan.unclaimed_asset_searches.join(" · ")}</p>
          )}
        </div>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}
