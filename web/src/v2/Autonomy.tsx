// How much the helper behind a card, step or check may do on its own (PR-14.6), from its policy on
// the server (architecture §4): each card, task and check arrives with its label. Shown only with
// "Show how Saathi works" on: customers meet Saathi, not the machinery.
import { useContext, useState } from "react";
import type { Autonomy } from "../api";
import { ShowHow } from "./demo";

const LEVEL: Record<Autonomy, { tag: string; means: string }> = {
  assist: { tag: "ASSIST", means: "Prepares the facts. A person decides." },
  propose: { tag: "PROPOSE", means: "Suggests. You decide." },
  act_within_limits: { tag: "ACT", means: "Acts on its own, within set limits." },
};

/** The label, which shows what it means when tapped (a phone has no hover). */
export default function AutonomyTag({ level }: { level?: Autonomy | null }) {
  const showHow = useContext(ShowHow);
  const [open, setOpen] = useState(false);
  const l = level ? LEVEL[level] : null;
  if (!showHow || !l) return null;
  return (
    <span className="v2-autonomy-wrap">
      <button type="button" className="v2-autonomy" aria-expanded={open} onClick={() => setOpen(!open)}
        aria-label={`${l.tag}: what this means`}>{l.tag}</button>
      {open && <span className="v2-autonomy-means">{l.means}</span>}
    </span>
  );
}
