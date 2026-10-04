// What Saathi picked up in a conversation, described in words a person recognises.
import type { InventoryItem, Moment, Person, Proposal } from "../../api";

const MOMENT_KIND: Record<Moment["kind"], string> = {
  story: "A story", advice: "Advice", practical: "Something practical", wish: "A wish",
};

export type Described = { kind: string; title: string; detail: string | null; missing: string | null };

export function describe(p: Proposal): Described {
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

/** A card that looks like something already kept, and could replace it ("Replace it" works for
 *  stories and wishes only; anything else is saved alongside). */
export const canReplace = (p: Proposal) => Boolean(p.looks_like && !p.updates && p.collection === "moments");
