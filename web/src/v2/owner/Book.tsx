// Kept (v2): everything the owner has kept, as a book their family will one day receive. Chapters
// instead of a score: gaps are "pages still to write", each with a way to write it.
import { useContext, useEffect, useState } from "react";
import { api, type Gap, type KeptItem, type Task } from "../../api";
import { useAction } from "../../ui";
import { friendlyError } from "../copy";
import { ShowHow } from "../demo";
import { Chevron, Close } from "../icons";

/** When a page was kept, in the reader's own time zone: "10 Oct 2026, 12:14". */
const keptOn = (iso: string) => new Date(iso).toLocaleString(undefined, {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

type Chapter = { key: string; title: string; empty: string; match: (i: KeptItem) => boolean };

const CHAPTERS: Chapter[] = [
  { key: "stories", title: "Stories and advice", empty: "Stories and advice you keep from your conversations appear here.",
    match: (i) => i.collection === "moments" && i.type !== "Wish" },
  { key: "money", title: "Money matters", empty: "Accounts, policies and property you keep appear here.",
    match: (i) => i.collection === "inventory" },
  { key: "wishes", title: "Wishes", empty: "Write a wish below, or tell Saathi.", match: (i) => i.collection === "moments" && i.type === "Wish" },
  { key: "people", title: "People", empty: "The family members you mention appear here.", match: (i) => i.collection === "people" },
];

const LEGAL_PARTNER = "Demo Legal Partner";

export default function Book({ onTalkAbout, onReview }: { onTalkAbout: (question: string) => void; onReview: () => void }) {
  const showHow = useContext(ShowHow);
  const [items, setItems] = useState<KeptItem[] | null>(null);
  const [owner, setOwner] = useState<{ name: string; family: string[] } | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [score, setScore] = useState<number | null>(null);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [waiting, setWaiting] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<KeptItem | null>(null);
  const [missingFailed, setMissingFailed] = useState(false); // next steps or gaps couldn't be loaded
  // Separate busy flags: opening the book, changing a page, and Saathi looking for gaps.
  const load = useAction();
  const { busy, error, run } = useAction();
  const look = useAction();

  useEffect(() => {
    load.run(async () => setItems((await api.kept()).items));
    api.owner().then((o) => setOwner({ name: String(o.name ?? ""), family: o.family ?? [] })).catch(() => {});
    api.tasks().then((r) => { setScore(r.score); setTasks(r.tasks); }).catch(() => { setTasks([]); setMissingFailed(true); });
    api.gaps().then((r) => setGaps(r.gaps)).catch(() => setMissingFailed(true));
    api.review().then((r) => setWaiting(r.moments.length + r.inventory.length)).catch(() => {});
  }, []);

  /** Next steps are worked out by Saathi on request only (it's a model call), never on opening. */
  const findMissing = () => look.run(async () => { const r = await api.readiness(); setScore(r.score); setTasks(r.tasks); });
  const closeTask = (t: Task, status: "done" | "dismissed") =>
    run(async () => { await api.updateTask(t.id, status); setTasks((all) => all?.filter((x) => x.id !== t.id) ?? null); });
  const skipGap = (g: Gap) => run(async () => setGaps((await api.skipGap(g.key)).gaps));
  const remove = (item: KeptItem) => run(async () => { setItems((await api.removeKept(item.id)).items); setConfirm(null); });

  const kept = items ?? [];
  const toWrite = (tasks?.length ?? 0) + gaps.length;
  const share = kept.length + toWrite ? Math.round((kept.length / (kept.length + toWrite)) * 100) : 0;
  const forWhom = (owner?.family ?? []).map((f) => f.replace(/\s*\(.*\)$/, "")).filter(Boolean);
  const firstName = owner?.name.split(" ")[0] ?? "";

  if (items === null && load.error) {
    return (
      <div className="v2-book">
        <section className="v2-panel" role="alert">
          <h2>Your book couldn't be opened just now</h2>
          <p className="v2-muted">Nothing you kept has changed. {friendlyError(new Error(load.error))}</p>
          <button className="v2-btn quiet" disabled={load.busy} onClick={() => load.run(async () => setItems((await api.kept()).items))}>Try again</button>
        </section>
      </div>
    );
  }

  return (
    <div className="v2-book">
      <section className="v2-cover" aria-labelledby="book-title">
        {forWhom.length > 0 && <p className="v2-cover-for">For {listOf(forWhom)}</p>}
        <h1 id="book-title">The book of<br />{owner?.name || "your family"}</h1>
        <p className="v2-cover-count">
          {items === null ? "Opening your book…" : kept.length === 1 ? "1 page kept" : `${kept.length} pages kept`}
          {toWrite > 0 && ` · ${toWrite === 1 ? "1 page" : `${toWrite} pages`} still to write`}
        </p>
        <div className="v2-cover-bar" aria-hidden="true"><i style={{ width: `${share}%` }} /></div>
        {showHow && score !== null && <p className="v2-cover-how">Readiness score: {score}/100 (shown only in “Show how Saathi works”)</p>}
      </section>

      {waiting > 0 && (
        <button className="v2-waiting" onClick={onReview}>
          <b>{waiting === 1 ? "1 thing is" : `${waiting} things are`} waiting for your Keep</b>
          <span>Review them in Talk</span>
        </button>
      )}

      <section className="v2-chapters" aria-label="Chapters">
        {CHAPTERS.map((c) => {
          const pages = kept.filter(c.match);
          const isOpen = open === c.key;
          return (
            <div key={c.key} className={`v2-chapter${isOpen ? " is-open" : ""}`}>
              <button className="v2-chapter-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : c.key)}>
                <span className="v2-chapter-title">{c.title}</span>
                <span className="v2-chapter-count">{pages.length === 1 ? "1 page" : `${pages.length} pages`}</span>
                <span className="v2-chapter-chev" aria-hidden="true"><Chevron /></span>
              </button>
              {isOpen && (
                <div className="v2-chapter-body">
                  {pages.length === 0 && <p className="v2-muted v2-small">{c.empty}</p>}
                  <ul className="v2-pages">
                    {pages.map((i) => (
                      <li key={i.id}>
                        <div>
                          <p className="v2-page-title">{i.title}</p>
                          {i.description && <p className="v2-page-desc">{i.description}</p>}
                          {i.kept_at && <p className="v2-page-kept">Kept on {keptOn(i.kept_at)}</p>}
                        </div>
                        <button className="v2-remove" disabled={busy} onClick={() => setConfirm(i)} aria-label={`Remove “${i.title}”`}><Close /></button>
                      </li>
                    ))}
                  </ul>
                  {c.key === "wishes" && <WishTools onAdded={() => run(async () => setItems((await api.kept()).items))} />}
                </div>
              )}
            </div>
          );
        })}
      </section>

      {confirm && (
        <div className="v2-confirm" role="alert">
          <p><strong>Remove “{confirm.title}”?</strong> It's removed for good from what your family receives, with every earlier version. What you said about it stays in your conversation.</p>
          <div className="v2-row">
            <button className="v2-btn danger" disabled={busy} onClick={() => remove(confirm)}>Remove it</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => setConfirm(null)}>Keep it</button>
          </div>
        </div>
      )}

      <section className="v2-towrite" aria-labelledby="towrite-title">
        <h2 id="towrite-title">Pages still to write</h2>
        <p className="v2-muted">These would help your family most. Talk about any of them, or skip the ones you'd rather not.</p>
        {tasks && tasks.length === 0 && gaps.length === 0 && (
          <p className="v2-muted v2-small">{missingFailed ? "What's still missing couldn't be loaded just now." : score === null ? "Saathi hasn't looked for gaps yet." : "Nothing missing right now."}</p>
        )}
        <ul className="v2-steps">
          {gaps.map((g) => (
            <li key={`gap-${g.key}`}>
              <p className="v2-step-title">{g.text}</p>
              <div className="v2-row">
                <button className="v2-btn" disabled={busy} onClick={() => onTalkAbout(g.text)}>Talk about this</button>
                <button className="v2-link" disabled={busy} onClick={() => skipGap(g)}>Skip</button>
              </div>
            </li>
          ))}
          {tasks?.map((t) => (
            <li key={t.id} className={`sev-${t.severity}`}>
              <p className="v2-step-title">{t.title}</p>
              <p className="v2-step-why">{t.why_it_matters}</p>
              {t.steps.length > 0 && <ol className="v2-step-list">{t.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
              <div className="v2-row">
                {(t.action === "talk" || t.action === "family") && t.question_for_owner && (
                  <button className="v2-btn" disabled={busy} onClick={() => onTalkAbout(t.question_for_owner)}>Talk about this</button>
                )}
                <button className="v2-btn quiet" disabled={busy} onClick={() => closeTask(t, "done")}>Done</button>
                <button className="v2-link" disabled={busy} onClick={() => closeTask(t, "dismissed")}>Not relevant</button>
              </div>
            </li>
          ))}
        </ul>
        <button className="v2-link" disabled={look.busy} onClick={findMissing}>
          {look.busy ? "Saathi is looking…" : score === null ? "Ask Saathi what's still missing" : "Look again for what's missing"}
        </button>
      </section>

      {[load.error, error, look.error].filter(Boolean).map((e) => (
        <p key={e} className="v2-error" role="alert">{friendlyError(new Error(e ?? ""))}</p>
      ))}
      {firstName && <p className="v2-muted v2-small v2-book-foot">Only what you keep is in your book, {firstName}. Saathi never adds a page without your Keep.</p>}
    </div>
  );
}

/** Write a wish in your own words; Saathi points out any that need a legal document (it never
 *  gives legal advice), and only with consent sends a summary, never the vault, to a partner. */
function WishTools({ onAdded }: { onAdded: () => void }) {
  const [wish, setWish] = useState("");
  const [needs, setNeeds] = useState<Awaited<ReturnType<typeof api.legalNeeds>>["needs"] | null>(null);
  const [consent, setConsent] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  return (
    <div className="v2-wishtools">
      <div className="v2-field">
        <label htmlFor="v2-wish">Write a wish</label>
        <div className="v2-row">
          <input id="v2-wish" className="v2-input v2-grow" value={wish} onChange={(e) => setWish(e.target.value)}
            placeholder="e.g. My car should go to Arjun" />
          <button className="v2-btn" disabled={!wish.trim() || busy}
            onClick={() => run(async () => { await api.addWish(wish.trim()); setWish(""); onAdded(); })}>Add</button>
        </div>
        <p className="v2-muted v2-small">A wish you write here is kept straight away, in your own words.</p>
      </div>
      <button className="v2-btn quiet" disabled={busy} onClick={() => run(async () => setNeeds((await api.legalNeeds()).needs))}>
        {busy && !needs ? "Checking…" : "Which wishes need a legal document?"}
      </button>
      {needs && (
        <div className="v2-stack">
          {needs.length === 0 && <p className="v2-muted v2-small">None of your wishes need a legal document right now.</p>}
          <ul className="v2-pages">
            {needs.map((n) => (
              <li key={`${n.wish_excerpt}-${n.document_needed}`}>
                <div>
                  <p className="v2-eyebrow">{n.document_needed.replaceAll("_", " ")}</p>
                  <p className="v2-page-title">“{n.wish_excerpt}”</p>
                  <p className="v2-page-desc">{n.reason_plain}</p>
                </div>
              </li>
            ))}
          </ul>
          <p className="v2-muted v2-small">Saathi never gives legal advice. A licensed partner can help, and only if you agree.</p>
          {needs.length > 0 && (sent ? <p className="v2-ok">{sent}</p> : (
            <>
              <label className="v2-check">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                I agree to share this summary, never my vault, with {LEGAL_PARTNER}.
              </label>
              <button className="v2-btn" disabled={!consent || busy}
                onClick={() => run(async () => setSent((await api.legalHandoff(LEGAL_PARTNER, consent, needs)).status))}>
                Send to {LEGAL_PARTNER}
              </button>
            </>
          ))}
        </div>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </div>
  );
}

function listOf(names: string[]): string {
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
