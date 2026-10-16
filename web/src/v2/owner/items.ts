// What Saathi picked up in a conversation, described in words a person recognises.
import type { InventoryItem, Moment, Person, Proposal } from "../../api";

const MOMENT_KIND: Record<Moment["kind"], string> = {
  story: "A story", advice: "Advice", practical: "Something practical", wish: "A wish",
};

/** The model's field names, in words a person recognises ("account_hint" is the account number). */
const FIELD_WORDS: Record<string, string> = {
  account_hint: "account number", account_number: "account number", policy_number: "policy number",
  nominee: "nominee", nominee_or_beneficiary: "nominee", beneficiary: "nominee",
  approximate_value: "value", papers_at: "where the papers are", holder_name: "account holder",
  institution_contact: "contact at the bank or insurer", intended_for: "who it's meant for",
};
const inWords = (field: string) => FIELD_WORDS[field] ?? field.replaceAll("_", " ");
/** Empty, or a placeholder that says nothing ("N/A", "NA", "nil", "-"); the server's grounding.PLACEHOLDERS. */
const blank = (s: string | null | undefined) =>
  !s || /^(n\s*\/?\s*a|nil|none|null|unknown|not known|not applicable|not available|tbd|[-.\s•]*)$/i.test(s.trim());

type Described = { kind: string; title: string; detail: string | null; missing: string | null };

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
  // A card saved before the server emptied placeholders may still say "N/A" or "..": leave those out.
  const hint = /\d/.test(i.account_hint ?? "") ? ` ${i.account_hint}` : "";
  const title = [i.institution, i.product].filter((s) => !blank(s)).join(" · ") + hint;
  return { kind: `${i.category === "property" ? "Property" : "Account or policy"}${update}`, title: title.trim() || "An item",
    detail: detail || null, missing: i.missing_fields.length ? `Still missing: ${[...new Set(i.missing_fields.map(inWords))].join(", ")}` : null };
}

/** A card that looks like something already kept, and could replace it ("Replace it" works for
 *  stories and wishes only; anything else is saved alongside). */
export const canReplace = (p: Proposal) => Boolean(p.looks_like && !p.updates && p.collection === "moments");
