// The end of a conversation (v2): what Saathi heard, in a few sentences, and everything it picked
// up, all marked to save. The owner removes only what they don't want, then saves the rest with
// one tap, or discards the whole conversation. Saving here is the owner's own decision (AGENTS.md
// rule 3); leaving without choosing saves nothing, and the items wait for next time.
import { useState } from "react";
import type { Proposal } from "../../api";
import { Check, Close } from "../icons";
import { canReplace, describe } from "./items";

export type ReviewChoice = { p: Proposal; save: boolean; replace: boolean };

export default function Review({ items, summary, summaryState, meta, note = null, busy, error, onSave, onLater, onDiscard }: {
  items: Proposal[]; summary: string | null; summaryState: "loading" | "ready" | "failed"; meta: string; note?: string | null;
  busy: boolean; error: string | null;
  onSave: (choices: ReviewChoice[]) => void; onLater: () => void; onDiscard: (() => void) | null;
}) {
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  // A card that looks like something kept is saved as new unless the owner chooses to update the
  // earlier one: nothing kept is replaced without the owner's own choice.
  const [update, setUpdate] = useState<ReadonlySet<string>>(new Set());
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const toggle = (set: ReadonlySet<string>, id: string) => {
    const next = new Set(set);
    if (!next.delete(id)) next.add(id);
    return next;
  };
  const saving = items.filter((p) => !removed.has(p.id)).length;
  const choices = (): ReviewChoice[] =>
    items.map((p) => ({ p, save: !removed.has(p.id), replace: canReplace(p) && update.has(p.id) }));

  return (
    <section className="v2-review" aria-labelledby="review-title">
      <div className="v2-review-head">
        <p className="v2-eyebrow">{meta}</p>
        <h2 id="review-title">{onDiscard ? "Here's what you told Saathi" : "Waiting from earlier"}</h2>
        {summaryState === "loading" && <p className="v2-summary is-loading">Saathi is summing up the conversation…</p>}
        {summaryState === "ready" && summary && <p className="v2-summary">{summary}</p>}
        {note && <p className="v2-notice-card">{note}</p>}
      </div>

      {items.length > 0 ? (
        <>
          <p className="v2-review-lede">
            {items.length === 1 ? "Saathi picked up one thing for your family." : `Saathi picked up ${items.length} things for your family.`}{" "}
            Remove anything you don't want, and save the rest.
          </p>
          <ul className="v2-review-list">
            {items.map((p) => {
              const d = describe(p);
              const off = removed.has(p.id);
              return (
                <li key={p.id} className={`v2-review-item${off ? " is-removed" : ""}`}>
                  <div className="v2-review-text">
                    <p className="v2-eyebrow">{off ? "Won't be saved" : d.kind}</p>
                    <p className="v2-review-title">{d.title}</p>
                    {!off && d.detail && <p className="v2-review-detail">{d.detail}</p>}
                    {!off && d.missing && <p className="v2-review-missing">{d.missing}</p>}
                    {!off && canReplace(p) && p.looks_like && (
                      <fieldset className="v2-choice">
                        <legend className="v2-visually-hidden">How to save “{d.title}”</legend>
                        <span className="v2-choice-note">This looks like “{p.looks_like.title}”, which you kept before.</span>
                        <label>
                          <input type="radio" name={`r-${p.id}`} checked={!update.has(p.id)}
                            onChange={() => setUpdate((s) => { const n = new Set(s); n.delete(p.id); return n; })} />
                          Save as new
                        </label>
                        <label>
                          <input type="radio" name={`r-${p.id}`} checked={update.has(p.id)}
                            onChange={() => setUpdate((s) => new Set(s).add(p.id))} />
                          Update the earlier one
                        </label>
                      </fieldset>
                    )}
                  </div>
                  <button className={`v2-remove${off ? " is-undo" : ""}`} disabled={busy}
                    onClick={() => setRemoved((s) => toggle(s, p.id))}
                    aria-label={off ? `Undo: save “${d.title}”` : `Don't save “${d.title}”`}>
                    {off ? "Undo" : <Close />}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p className="v2-review-lede">Nothing in this conversation needs saving.</p>
      )}

      {error && <p className="v2-error" role="alert">{error}</p>}

      {confirmDiscard && onDiscard ? (
        <div className="v2-confirm" role="alert">
          <p><strong>Delete this conversation?</strong> What you said, and everything above, is deleted. Saathi won't carry on from it. Things you kept before stay.</p>
          <div className="v2-row">
            <button className="v2-btn danger" disabled={busy} onClick={onDiscard}>Delete the conversation</button>
            <button className="v2-btn quiet" disabled={busy} onClick={() => setConfirmDiscard(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="v2-review-actions">
          <button className="v2-btn wide" disabled={busy} onClick={() => onSave(choices())}>
            <Check />
            {items.length === 0 ? "Done" : saving === 0 ? "Save nothing" : saving === 1 ? "Save 1 thing" : `Save ${saving} things`}
          </button>
          <div className="v2-row v2-review-minor">
            {items.length > 0 && <button className="v2-link" disabled={busy} onClick={onLater}>Decide later</button>}
            {onDiscard && <button className="v2-link danger" disabled={busy} onClick={() => setConfirmDiscard(true)}>Discard this conversation</button>}
          </div>
        </div>
      )}
    </section>
  );
}
