// A card Saathi suggests keeping. Nothing joins what the family receives until the owner taps Keep
// (AGENTS.md rule 3). A card that looks like something already kept asks: Replace it, or Keep both.
import type { InventoryItem, Moment, Person, Proposal } from "../../api";
import { Check } from "../icons";

export type Decide = (p: Proposal, decision: "approve" | "reject", replace?: boolean) => unknown;

const MOMENT_KIND: Record<Moment["kind"], string> = {
  story: "A story", advice: "Advice", practical: "Something practical", wish: "A wish",
};

/** A waiting card that a plain Keep is right for (not one asking "Replace it or Keep both"). */
export const plainKeep = (p: Proposal) => p.status === "pending" && !(p.looks_like && !p.updates);

/** What the card is, in words a person recognises. */
export function describe(p: Proposal): { kind: string; title: string; detail: string | null; missing: string | null } {
  const update = p.updates ? " · an update" : "";
  if (p.collection === "people") {
    const person = p as Person;
    return { kind: `Family${update}`, title: `${person.name}, ${person.relationship}`, detail: null, missing: null };
  }
  if (p.collection === "moments") {
    const m = p as Moment;
    const meant = m.intended_for?.length ? `For ${m.intended_for.join(", ")}` : null;
    return { kind: `${MOMENT_KIND[m.kind] ?? "A memory"}${update}`, title: m.title,
      detail: [m.summary, meant].filter(Boolean).join(" · ") || null, missing: null };
  }
  const i = p as InventoryItem;
  const detail = [i.nominee_or_beneficiary && `Nominee: ${i.nominee_or_beneficiary}`,
    i.intended_for && `Meant for: ${i.intended_for}`, i.papers_at && `Papers: ${i.papers_at}`].filter(Boolean).join(" · ");
  return { kind: `Account or policy${update}`, title: `${i.institution} · ${i.product} ${i.account_hint}`.trim(),
    detail: detail || null, missing: i.missing_fields.length ? `Still missing: ${i.missing_fields.join(", ")}` : null };
}

export function KeepCard({ p, onDecide, busy }: { p: Proposal; onDecide: Decide; busy?: boolean }) {
  const d = describe(p);
  const kept = p.status === "approved";
  return (
    <article className={`v2-keep${kept ? " is-kept" : ""}`} aria-label={`${d.kind}: ${d.title}`}>
      {kept && <span className="v2-bead" aria-hidden="true" />}
      <p className="v2-eyebrow">{d.kind}</p>
      <h3 className="v2-keep-title">{d.title}</h3>
      {d.detail && <p className="v2-keep-detail">{d.detail}</p>}
      {d.missing && <p className="v2-keep-missing">{d.missing}</p>}
      {kept ? (
        <p className="v2-keep-done"><Check /> Kept for your family</p>
      ) : p.looks_like && !p.updates ? (
        <>
          <p className="v2-keep-missing">This looks like an update to “{p.looks_like.title}”.</p>
          <div className="v2-keep-actions">
            <button className="v2-btn" disabled={busy} onClick={() => onDecide(p, "approve", true)}>Replace it</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => onDecide(p, "approve")}>Keep both</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => onDecide(p, "reject")}>Not now</button>
          </div>
        </>
      ) : (
        <div className="v2-keep-actions">
          <button className="v2-btn" disabled={busy} onClick={() => onDecide(p, "approve")}>Keep</button>
          <button className="v2-btn quiet" disabled={busy} onClick={() => onDecide(p, "reject")}>Not now</button>
        </div>
      )}
    </article>
  );
}
