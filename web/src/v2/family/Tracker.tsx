// The handover, step by step, in plain words: what has happened, what happens next, and how long
// the waiting period has left. The family uploads proof of legal authority at its step.
import { useEffect, useState } from "react";
import { api, type Case } from "../../api";
import { useAction } from "../../ui";
import { friendlyError } from "../copy";
import FilePick from "../FilePick";
import { Check } from "../icons";

export default function Tracker({ status, parent }: { status: Case; parent: string }) {
  const { busy, error, run } = useAction();
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const s = status.state;
  const left = status.wait_until ? Math.max(0, Math.ceil(status.wait_until - now)) : null;
  const steps: { title: string; detail: string; done: boolean; now: boolean; action?: React.ReactNode }[] = [
    { title: "Certificate received", detail: "Careful checks looked for mistakes or forgery.", done: true, now: false },
    { title: "Confirmed by our team", detail: "A person on our team confirms the loss.", done: s !== "reported", now: s === "reported" },
    {
      title: "Proof of your legal authority",
      detail: status.authority_check ? "Received. A second person on our team confirms it." : "For example a will, succession certificate or legal heir certificate.",
      done: Boolean(status.authority_check) && s !== "death_confirmed",
      now: s === "death_confirmed",
      action: s === "death_confirmed" && !status.authority_check ? (
        <FilePick label={busy ? "Checking…" : "Upload proof"} accept="image/*,application/pdf" disabled={busy}
          onFile={(f) => run(async () => { await api.submitAuthority(f); })} />
      ) : undefined,
    },
    {
      title: s === "waiting" && left !== null ? `Waiting period: ${formatLeft(left)} left` : "Waiting period",
      detail: `${parent}'s phone is told first. One tap from them would cancel everything.`,
      done: s === "released", now: s === "waiting",
    },
    { title: `${parent}'s vault opens on your phone`, detail: "Only on your phone, with your key.", done: s === "released", now: false },
  ];

  return (
    <section className="v2-panel" aria-labelledby="tracker-title">
      <p className="v2-eyebrow">The handover</p>
      <h2 id="tracker-title">Where things stand</h2>
      <ol className="v2-tracker">
        {steps.map((st) => (
          <li key={st.title} className={st.done ? "is-done" : st.now ? "is-now" : ""}>
            <span className="v2-tracker-dot" aria-hidden="true">{st.done && <Check />}</span>
            <div>
              <p className="v2-tracker-title">{st.title}{st.done ? <span className="v2-visually-hidden"> (done)</span> : st.now ? <span className="v2-visually-hidden"> (now)</span> : null}</p>
              <p className="v2-tracker-detail">{st.detail}</p>
              {st.action}
            </div>
          </li>
        ))}
      </ol>
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

function formatLeft(seconds: number): string {
  if (seconds < 120) return `${seconds} seconds`;
  const m = Math.ceil(seconds / 60);
  if (m < 120) return `${m} minutes`;
  const h = Math.ceil(m / 60);
  return h < 48 ? `${h} hours` : `${Math.ceil(h / 24)} days`;
}
