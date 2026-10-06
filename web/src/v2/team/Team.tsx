// Our team (v2): reviewing a reported passing. The certificate check prepares the facts; a person on
// our team decides, and two different people must confirm the loss and the legal authority (the
// server enforces it). Going against a "not valid" check needs a written reason, kept in the audit
// log. The logic follows the classic Team.tsx (copied).
import { useEffect, useState } from "react";
import { type AccessRequest, api, type Case, type DocumentCheck } from "../../api";
import { useAction } from "../../ui";
import { friendlyError } from "../copy";

const STATE_TEXT: Record<Case["state"], { title: string; tone: "act" | "wait" | "done" }> = {
  reported: { title: "Passing reported: needs a first review", tone: "act" },
  death_confirmed: { title: "Loss confirmed: waiting for proof of legal authority", tone: "wait" },
  waiting: { title: "Waiting period: the owner's devices are alerted", tone: "wait" },
  released: { title: "Vault released to the recipient", tone: "done" },
  cancelled: { title: "Cancelled: the owner responded", tone: "done" },
};

const VERDICT = {
  likely_valid: { text: "Looks valid", tone: "good" },
  needs_attention: { text: "Needs a closer look", tone: "attention" },
  not_valid: { text: "Not a valid document", tone: "bad" },
} as const;

const RESULT = { found: "Found", ruled_out: "Ruled out", cant_tell: "Can't tell" } as const;

export default function Team({ reviewer }: { reviewer: string }) {
  return (
    <div className="v2-stack v2-tab">
      <header className="v2-tab-head">
        <h1>Review cases</h1>
        <p>Every document is checked first; only a person on our team confirms. Two different people must confirm the loss and the legal authority.</p>
      </header>
      <p className="v2-notice-card">You're reviewing as <strong>{reviewer}</strong>. To confirm the second step, switch to the other reviewer in the Demo menu.</p>
      <Cases reviewer={reviewer} />
      <AccessRequests />
    </div>
  );
}

function Cases({ reviewer }: { reviewer: string }) {
  const [cases, setCases] = useState<Case[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const refresh = () => api.cases().then((r) => { setCases(r.cases); setLoadError(null); }).catch((e) => setLoadError(friendlyError(e)));
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 5000);
    return () => clearInterval(t);
  }, []);

  if (cases === null) {
    return loadError ? <p className="v2-error" role="alert">{loadError}</p> : <p className="v2-muted">Loading cases…</p>;
  }
  return (
    <section className="v2-stack" aria-label="Cases">
      {cases.length === 0 && <p className="v2-muted">No open cases.</p>}
      {cases.map((c) => (
        <article key={c.id} className={`v2-panel v2-case is-${STATE_TEXT[c.state].tone}`}>
          <p className="v2-eyebrow">Case</p>
          <h2>{STATE_TEXT[c.state].title}</h2>
          <CheckView title="Certificate of passing: the check" check={c.certificate_check} />
          {c.state === "reported" && (
            <Confirm label="Confirm the loss" check={c.certificate_check} busy={busy}
              onConfirm={(reason) => run(async () => { await api.confirmDeath(c.id, reviewer.trim(), reason); await refresh(); })} />
          )}
          {c.authority_check && <CheckView title="Proof of legal authority: the check" check={c.authority_check} />}
          {c.state === "death_confirmed" && c.authority_check && (
            <Confirm label="Confirm the legal authority" check={c.authority_check} busy={busy}
              onConfirm={(reason) => run(async () => { await api.confirmAuthority(c.id, reviewer.trim(), reason); await refresh(); })} />
          )}
          <details className="v2-more">
            <summary>Audit log</summary>
            <ol className="v2-log">
              {c.history.map((h) => <li key={`${h.at}-${h.event}`}><time>{new Date(h.at * 1000).toLocaleTimeString()}</time> {h.event}</li>)}
            </ol>
          </details>
        </article>
      ))}
      {(error || loadError) && <p className="v2-error" role="alert">{error ? friendlyError(new Error(error)) : loadError}</p>}
    </section>
  );
}

function CheckView({ title, check }: { title: string; check: DocumentCheck }) {
  const v = VERDICT[check.verdict] ?? VERDICT.needs_attention;
  const signs = check.signs ?? [];
  const found = signs.filter((s) => s.result === "found").length;
  return (
    <div className={`v2-check-box is-${v.tone}`}>
      <p className="v2-check-title">{title}</p>
      <p className="v2-check-verdict">{v.text} · likely genuine {Math.round(check.confidence * 100)}%</p>
      <p className="v2-small">Looks like: {check.document_type}{check.person_named ? ` · names ${check.person_named}` : ""}{check.key_date ? ` · ${check.key_date}` : ""}</p>
      {check.concerns.length > 0 && <ul className="v2-concerns">{check.concerns.map((x) => <li key={x}>{x}</li>)}</ul>}
      {check.guide && signs.length > 0 && (
        <details className="v2-more" open={found > 0}>
          <summary>Warning signs: {found} found of {signs.length} · {check.guide.title}, version {check.guide.version}</summary>
          <ul className="v2-signs">
            {signs.map((s) => (
              <li key={s.id} className={`is-${s.result}`}>
                <span className="v2-sign-result">{RESULT[s.result]}</span>
                <span>{s.look_for}{s.note && <> · <em>{s.note}</em></>}</span>
                <small>{s.projection ? "Projection, " : ""}{s.trust === "human-reviewed" ? "reviewed by our team" : "from cited sources, not yet reviewed"}</small>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** The reviewer decides. Going against a "not valid" check needs a reason of at least 10 characters,
 *  written here in the page and kept in the audit log. */
function Confirm({ label, check, busy, onConfirm }: {
  label: string; check: DocumentCheck; busy: boolean; onConfirm: (reason?: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  if (check.verdict !== "not_valid") return <button className="v2-btn" disabled={busy} onClick={() => onConfirm()}>{label}</button>;
  if (!asking) return <button className="v2-btn quiet" disabled={busy} onClick={() => setAsking(true)}>{label} anyway…</button>;
  return (
    <div className="v2-confirm">
      <label htmlFor="v2-override"><strong>The check says this is not a valid document.</strong> Why are you confirming anyway? It's kept in the audit log.</label>
      <textarea id="v2-override" className="v2-input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="v2-row">
        <button className="v2-btn danger" disabled={busy || reason.trim().length < 10} onClick={() => onConfirm(reason.trim())}>{label}</button>
        <button className="v2-btn quiet" onClick={() => { setAsking(false); setReason(""); }}>Cancel</button>
      </div>
      {reason.trim().length > 0 && reason.trim().length < 10 && <p className="v2-muted v2-small">At least 10 characters.</p>}
    </div>
  );
}

/** After a passing: family members who were never listed. A person checks the relationship. */
function AccessRequests() {
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const refresh = () => api.teamAccessRequests()
    .then((r) => { setRequests(r.requests); setLoadError(null); })
    .catch((e) => setLoadError(friendlyError(e)));
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 5000);
    return () => clearInterval(t);
  }, []);
  if (requests.length === 0 && !loadError) return null;
  return (
    <section className="v2-panel" aria-labelledby="team-requests">
      <p className="v2-eyebrow">Access requests</p>
      <h2 id="team-requests">Family asking to be added</h2>
      <p className="v2-muted v2-small">Check proof of the relationship before allowing. This gives access to the stories and to Saathi, never the vault.</p>
      {requests.map((r) => (
        <div key={r.id} className="v2-request">
          <p><strong>{r.name}</strong> ({r.relationship}) · {r.contact}</p>
          {r.note && <p className="v2-quote">“{r.note}”</p>}
          <div className="v2-row">
            <button className="v2-btn" disabled={busy} onClick={() => run(async () => { await api.decideAccess(r.id, "approve", "team"); await refresh(); })}>Allow</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => run(async () => { await api.decideAccess(r.id, "decline", "team"); await refresh(); })}>Decline</button>
          </div>
        </div>
      ))}
      {loadError && <p className="v2-error" role="alert">Access requests couldn't be checked: {loadError}</p>}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}
