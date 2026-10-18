// After release (v2): the vault, opened on this phone only; Ask about Papa, answered only from what
// was kept, with where each answer came from; and the claims checklist with draft letters.
import { useState } from "react";
import { api, type KeptDocument } from "../../api";
import { useAction } from "../../ui";
import { decryptSecret, unwrapAsRecipient } from "../../vaultCrypto";
import { friendlyError } from "../copy";
import { Book as BookIcon, Lock, Send } from "../icons";
import OpenDocument from "../OpenDocument";

export default function After({ parent }: { parent: string }) {
  return (
    <>
      <OpenVault parent={parent} />
      <Ask parent={parent} />
      <Documents parent={parent} />
      <Claims parent={parent} />
    </>
  );
}

/** The photos and documents {parent} kept, with what they said about each: only after the handover. */
function Documents({ parent }: { parent: string }) {
  const [docs, setDocs] = useState<KeptDocument[] | null>(null);
  const { busy, error, run } = useAction();
  return (
    <section className="v2-panel" aria-labelledby="docs-title">
      <h2 id="docs-title">Documents {parent} kept</h2>
      {docs === null ? (
        <button className="v2-btn" disabled={busy} onClick={() => run(async () => setDocs((await api.documents()).documents))}>
          {busy ? "Opening…" : "Show the documents"}
        </button>
      ) : docs.length === 0 ? (
        <p className="v2-muted">{parent} didn't keep any photos or documents.</p>
      ) : (
        <ul className="v2-pages">
          {docs.map((d) => (
            <li key={d.id}>
              <div>
                <p className="v2-page-title">{d.title}</p>
                {d.comment && <p className="v2-page-desc">In {parent}'s words: “{d.comment}”</p>}
                <OpenDocument id={d.id} title={d.title} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
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
  const [thread, setThread] = useState<{ q: string; a: string; sources: { id: string; title: string }[]; notes: string[] }[]>([]);
  const { busy, error, run } = useAction();
  const suggestions = parent === "your family member"
    ? ["What insurance policies were there?", "Where are the important papers?", "Was there any crypto?"]
    : ["What insurance did Papa have?", "पापा ने पैसे बचाने के बारे में क्या कहा था?", "Did Papa have any crypto?"];
  /** One question at a time, so each answer carries on the same conversation (session). */
  const send = (question: string) => busy ? undefined : run(async () => {
    const r = await api.ask(question, session);
    setSession(r.session_id);
    setThread((t) => [...t, { q: question, a: r.answer, sources: r.sources, notes: r.notes ?? [] }]);
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
              <div className="v2-turn assistant"><Answer text={t.a} /></div>
              {/* Where the answer came from, as chips (PR-14.3): only kept items the server checked. */}
              {t.sources.length > 0 && (
                <div className="v2-sources">
                  <p className="v2-sources-label">From what {parent} kept</p>
                  <ul className="v2-chips" aria-label={`Sources from what ${parent} kept`}>
                    {t.sources.map((s) => {
                      const kind = KIND[s.id.split("_")[0]];
                      const title = s.title.trim() || kind || "Something kept";
                      return (
                        <li key={s.id} className="v2-source">
                          <BookIcon />{title}{kind && title !== kind && <span> · {kind}</span>}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              {t.notes.map((n) => <p key={n} className="v2-note">{n}</p>)}
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

/** What kind of kept item a source is, from its id: a story and the policy it's about can share a
 *  title, and this tells them apart. The id can't tell a story from a wish, so those share one name;
 *  the others are as on the review cards (items.ts). */
const KIND: Record<string, string> = { m: "Story or wish", inv: "Account or policy", p: "Family" };

/** The answer as plain paragraphs. The model quotes the owner as Markdown ("> …", or nested ">> …"),
 *  on its own or straight after a line of text; those lines show as a quote, without the ">"
 *  (PR-14.3: no raw codes or symbols in the answer). A ">" with no space after it (">50%") is text. */
const QUOTE = /^\s*>+(?:\s+>+)*(?:\s+|$)/;
function Answer({ text }: { text: string }) {
  const parts: { quote: boolean; text: string }[] = [];
  for (const block of text.split(/\n\s*\n/)) {
    for (const line of block.split("\n")) {
      const quote = QUOTE.test(line);
      const body = quote ? line.replace(QUOTE, "") : line;
      const last = parts[parts.length - 1];
      if (last && last.quote === quote && !last.text.endsWith("\n\n")) last.text += `\n${body}`;
      else parts.push({ quote, text: body });
    }
    if (parts.length) parts[parts.length - 1].text += "\n\n"; // a blank line always starts a new paragraph
  }
  return (
    <>
      {parts.map((p) => ({ ...p, text: p.text.trim() })).filter((p) => p.text).map((p, i) =>
        // The parts never change order or content while shown, so the index is a stable key.
        p.quote ? <blockquote key={i} className="v2-answer-quote">{p.text}</blockquote> : <p key={i}>{p.text}</p>)}
    </>
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
