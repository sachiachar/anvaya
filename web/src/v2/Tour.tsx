// "Start here" (PR-14.1): a short tour for someone who opens the demo cold, without the demo script.
// Five steps, each with buttons that go to the right person and screen. It folds to a thin bar so
// the screen stays usable, closes for good, and comes back from the Demo menu or after a reset.
// What it remembers (closed, step, folded) is a convenience in this browser only.
import { useContext, useState } from "react";
import { currentOwnerId, type PersonId } from "../api";
import { Chevron, Close } from "./icons";
import { ADMIN_URL, Nav, type OwnerTab } from "./nav";
import { SAMPLES, type SampleKind } from "./Samples";

type Go = { to: PersonId; tab?: OwnerTab; label: string };
type Step = { title: string; points: string[]; go?: Go[]; samples?: SampleKind[]; admin?: boolean; settings?: boolean };

const STEPS: Step[] = [
  {
    title: "Vikram talks to Saathi",
    points: [
      "Vikram is the owner. Tap Talk to Saathi and speak, or type below it.",
      "Tell Saathi about a policy, an account or a story. For example: “I have an LIC policy ending 4417, and Priya is the nominee.”",
      "At the end, review what Saathi picked up and tap Keep.",
      "Optional: turn on the voice check in Settings (the gear at the top) by reading three sentences. If a call doesn't sound like Vikram, what Saathi picked up waits until he says it was him.",
    ],
    go: [{ to: "vikram", tab: "talk", label: "Go to Vikram's Talk" }],
    settings: true,
  },
  {
    title: "Vikram keeps what matters and invites Priya",
    points: [
      "Kept is Vikram's book: everything he kept, and the pages still to write.",
      "On Family, Vikram invites his daughter Priya. She accepts on her phone, the codes match, and he seals his vault for her.",
    ],
    go: [{ to: "vikram", tab: "kept", label: "Open Kept" }, { to: "vikram", tab: "family", label: "Open Family" }],
  },
  {
    title: "Priya starts the handover, and two reviewers decide",
    points: [
      "Priya uploads a certificate of passing. Careful checks look for mistakes or forgery.",
      "The samples below say they are specimens, so the checks flag them. That shows the checks at work. Our team can still confirm.",
      "Anjali, on our team, confirms the loss. Priya then sends proof of her legal authority, and Rahul confirms it: two different people must.",
      "A waiting period of about a minute follows. Vikram's phone is told first, and one tap from him cancels everything.",
    ],
    go: [{ to: "priya", label: "Switch to Priya" }, { to: "anjali", label: "Switch to Anjali" }, { to: "rahul", label: "Switch to Rahul" }],
    samples: ["certificate", "authority"],
  },
  {
    title: "Priya opens the vault and asks about Vikram",
    points: [
      "When the waiting period ends, Vikram's vault opens on Priya's phone only.",
      "She asks Saathi about him, for example “What insurance did Papa have?”, and sees which kept items each answer came from.",
      "She prepares a checklist and draft letters for each bank and insurer.",
    ],
    go: [{ to: "priya", label: "Switch to Priya" }],
  },
  {
    title: "See the agents at work",
    points: [
      "The admin portal shows each step Saathi's agents took in your demo: which agent, its model, tokens, and how much it may do on its own.",
      "It opens in a new tab. Sign in there with the same Google account.",
    ],
    admin: true,
  },
];

const key = () => `anvaya.v2.tour.${currentOwnerId() || "demo"}`;
type Saved = { closed?: boolean; step?: number; folded?: boolean };

function read(): Saved {
  try { return JSON.parse(localStorage.getItem(key()) ?? "{}") as Saved; } catch { return {}; }
}
function write(s: Saved) {
  try { localStorage.setItem(key(), JSON.stringify(s)); } catch { /* not remembered; the tour still works */ }
}

/** Whether to show the tour now: on a new demo copy, until it's closed. */
export const tourWanted = () => !read().closed;
/** Show it again, from the first step (the Demo menu). */
export const reopenTour = () => write({ step: 0 });
/** Forget it, so a reset demo starts with the tour (Reset my demo). */
export function forgetTour() {
  try { localStorage.removeItem(key()); } catch { /* nothing to forget */ }
}

export default function Tour({ onClose }: { onClose: () => void }) {
  const nav = useContext(Nav);
  // Read once, when the tour opens; after that the state here is the truth, and is written back.
  const [step, setStep] = useState(() => Math.min(Math.max(read().step ?? 0, 0), STEPS.length - 1));
  const [folded, setFolded] = useState(() => Boolean(read().folded));
  const save = (s: Saved) => write({ step, folded, ...s });
  const goTo = (n: number) => { setStep(n); setFolded(false); save({ step: n, folded: false }); };
  const fold = (f: boolean) => { setFolded(f); save({ folded: f }); };
  const close = () => { write({ closed: true, step }); onClose(); };
  // Going to a screen folds the card, so the screen it opens is in view.
  const go = (g: Go) => { nav.switchTo(g.to, g.tab); fold(true); };

  const s = STEPS[step];
  const last = step === STEPS.length - 1;
  return (
    <section className={`v2-tour${folded ? " is-folded" : ""}`} aria-label="Start here: a tour of the demo">
      <div className="v2-tour-head">
        <button className="v2-tour-fold" aria-expanded={!folded} onClick={() => fold(!folded)}>
          <span className="v2-eyebrow">Start here · step {step + 1} of {STEPS.length}</span>
          <span className="v2-tour-title">{s.title}</span>
          <span className="v2-tour-chev" aria-hidden="true"><Chevron /></span>
        </button>
        <button className="v2-close" onClick={close} aria-label="Close the tour"><Close /></button>
      </div>
      {!folded && (
        <div className="v2-tour-body">
          <ul className="v2-tour-points">{s.points.map((p) => <li key={p}>{p}</li>)}</ul>
          {s.samples && (
            <ul className="v2-tour-files">
              {s.samples.map((k) => <li key={k}><a href={SAMPLES[k].href} download>Download the {SAMPLES[k].name} (PDF)</a></li>)}
            </ul>
          )}
          <div className="v2-row">
            {s.go?.map((g) => (
              <button key={`${g.to}-${g.tab ?? ""}`} className="v2-switch" onClick={() => go(g)}>
                <span>{g.label}</span><Chevron />
              </button>
            ))}
            {s.settings && (
              <button className="v2-switch" onClick={() => { nav.openSettings(); fold(true); }}>
                <span>Turn on the voice check</span><Chevron />
              </button>
            )}
            {s.admin && (
              <a className="v2-switch" href={`${ADMIN_URL}#/agents`} target="_blank" rel="noreferrer">
                <span>Open the admin portal</span><Chevron />
              </a>
            )}
          </div>
          <div className="v2-tour-nav">
            <button className="v2-link" disabled={step === 0} onClick={() => goTo(step - 1)}>Back</button>
            <ol className="v2-tour-dots" aria-label="Steps">
              {STEPS.map((t, i) => (
                <li key={t.title}>
                  <button aria-current={i === step ? "step" : undefined} aria-label={`Step ${i + 1}: ${t.title}`} onClick={() => goTo(i)} />
                </li>
              ))}
            </ol>
            {last
              ? <button className="v2-btn" onClick={close}>Done</button>
              : <button className="v2-btn" onClick={() => goTo(step + 1)}>Next</button>}
          </div>
        </div>
      )}
    </section>
  );
}
