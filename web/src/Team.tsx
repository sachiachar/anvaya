import { useEffect, useState } from "react";
import { api, type AccessRequest, type Case, type DocumentCheck } from "./api";
import { Card, ErrorLine, useAction } from "./ui";

const STATE_TEXT: Record<Case["state"], string> = {
  reported: "Death reported – needs review",
  death_confirmed: "Death confirmed – waiting for proof of legal authority",
  waiting: "Waiting period – owner's devices alerted",
  released: "Vault released to recipient",
  cancelled: "Cancelled – the owner responded",
};

/** A reviewer on our team: Anjali or Rahul, as chosen in the switcher. The server records
 * whoever is acting, so the same person can't confirm both steps. */
export default function Team({ reviewer }: { reviewer: string }) {
  return (
    <>
      <Cases reviewer={reviewer} />
      <AccessRequests />
    </>
  );
}

/** After a death: family members who were never listed. A person checks the relationship. */
function AccessRequests() {
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const { error, run } = useAction();
  const refresh = () => run(async () => setRequests((await api.teamAccessRequests()).requests));
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);
  if (requests.length === 0) return null;
  return (
    <Card title="Access requests after a death" level="assist">
      <p className="muted">Check proof of relationship before allowing. This gives access to stories and the assistant, never the vault.</p>
      <ul className="list">
        {requests.map((r) => (
          <li key={r.id}>
            <p><strong>{r.name}</strong> ({r.relationship}) · {r.contact}</p>
            {r.note && <p className="muted">"{r.note}"</p>}
            <div className="row">
              <button onClick={() => run(async () => { await api.decideAccess(r.id, "approve", "team"); await refresh(); })}>Allow</button>
              <button className="secondary" onClick={() => run(async () => { await api.decideAccess(r.id, "decline", "team"); await refresh(); })}>Decline</button>
            </div>
          </li>
        ))}
      </ul>
      <ErrorLine error={error} />
    </Card>
  );
}

function Cases({ reviewer }: { reviewer: string }) {
  const [cases, setCases] = useState<Case[]>([]);
  const { error, run } = useAction();
  const refresh = () => run(async () => setCases((await api.cases()).cases));
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <Card title="Review cases" level="assist">
      <p className="muted">The AI pre-checks every document. Only a person on our team can confirm, and two different people must confirm the death and the legal authority.</p>
      <p className="muted">You are reviewing as <strong>{reviewer}</strong>. To confirm the second step, switch to the other reviewer.</p>
      <ErrorLine error={error} />
      {cases.length === 0 && <p className="muted">No open cases.</p>}
      <ul className="list">
        {cases.map((c) => (
          <li key={c.id}>
            <p><strong>{STATE_TEXT[c.state]}</strong></p>
            <Check title="Death certificate: AI pre-check" check={c.certificate_check} />
            {c.state === "reported" && (
              <ConfirmButton label="Confirm death" check={c.certificate_check} reviewer={reviewer}
                onConfirm={(reason) => run(async () => { await api.confirmDeath(c.id, reviewer.trim(), reason); await refresh(); })} />
            )}
            {c.authority_check && <Check title="Legal authority: AI pre-check" check={c.authority_check} />}
            {c.state === "death_confirmed" && c.authority_check && (
              <ConfirmButton label="Confirm legal authority" check={c.authority_check} reviewer={reviewer}
                onConfirm={(reason) => run(async () => { await api.confirmAuthority(c.id, reviewer.trim(), reason); await refresh(); })} />
            )}
            <details>
              <summary>Audit log</summary>
              <ol className="log">
                {c.history.map((h, i) => <li key={i}>{new Date(h.at * 1000).toLocaleTimeString()} – {h.event}</li>)}
              </ol>
            </details>
          </li>
        ))}
      </ul>
    </Card>
  );
}

const VERDICT = {
  likely_valid: { text: "Looks valid", tone: "good" },
  needs_attention: { text: "Needs a closer look", tone: "attention" },
  not_valid: { text: "Not a valid document", tone: "bad" },
} as const;

function Check({ title, check }: { title: string; check: DocumentCheck }) {
  const v = VERDICT[check.verdict] ?? VERDICT.needs_attention;
  return (
    <div className={`check-box ${v.tone}`}>
      <p><strong>{title}</strong></p>
      <p className="verdict">{v.text} · likelihood genuine {Math.round(check.confidence * 100)}%</p>
      <p>Looks like: {check.document_type}{check.person_named ? ` · names ${check.person_named}` : ""}{check.key_date ? ` · ${check.key_date}` : ""}</p>
      {check.concerns.length > 0 && <ul>{check.concerns.map((x) => <li key={x}>{x}</li>)}</ul>}
    </div>
  );
}

/** The reviewer decides, but going against the AI's "not valid" needs a written reason (kept in the audit log). */
function ConfirmButton({ label, check, reviewer, onConfirm }: {
  label: string; check: DocumentCheck; reviewer: string; onConfirm: (reason?: string) => void;
}) {
  const named = reviewer.trim().length > 1;
  if (check.verdict !== "not_valid") return <button disabled={!named} onClick={() => onConfirm()}>{label}</button>;
  return (
    <button className="secondary" disabled={!named} onClick={() => {
      const reason = window.prompt("The AI says this is not a valid document. Why are you confirming anyway? (at least 10 characters, saved in the audit log)");
      if (reason && reason.trim().length >= 10) onConfirm(reason.trim());
    }}>{label} anyway…</button>
  );
}
