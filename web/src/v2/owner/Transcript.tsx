// The conversation as it happens, under the microphone: Saathi's words in the serif, the owner's in
// plain type, along the gold thread. Things Saathi picked up show as small notes; the owner decides
// on them all at once at the end, never one by one mid-conversation.
import { useEffect, useRef } from "react";
import type { Turn } from "../../api";
import { describe } from "./items";
import type { Caption } from "./Talk";

const ATTACHMENT = { photo: "Photo", document: "Document" } as const;

/** follow: keep the newest line in view. During a call the transcript scrolls inside its own box,
 *  so the microphone and its controls never move; it follows only while the owner is at the bottom,
 *  never pulling them away from something they scrolled up to read. */
export default function Transcript({ turns, caption, live, thinking, follow = false }: {
  turns: Turn[]; caption: Caption; live: boolean; thinking: boolean; follow?: boolean;
}) {
  const box = useRef<HTMLElement | null>(null);
  const end = useRef<HTMLDivElement | null>(null);
  const atBottom = useRef(true);
  useEffect(() => { if (live) atBottom.current = true; }, [live]);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must return
  // nothing (or a cleanup function).
  useEffect(() => {
    if (!follow) return;
    if (live) {
      // Instantly, not smoothly: a smooth scroll fires scroll events on its way down, which would
      // look like the owner scrolling up and stop the following.
      if (atBottom.current && box.current) box.current.scrollTop = box.current.scrollHeight;
      return;
    }
    const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    end.current?.scrollIntoView({ behavior, block: "end" });
  }, [turns.length, caption.heard, caption.said, thinking, live]);

  if (!turns.length && !live && !thinking) return null;
  return (
    <section ref={box} className={`v2-transcript${live ? " is-live" : ""}`} aria-label="This conversation"
      onScroll={(e) => { const el = e.currentTarget; atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
      <ol className="v2-thread" aria-live="polite">
        {turns.map((t) => (
          <li key={t.id} className={`v2-turn ${t.role}`}>
            {t.attachment && t.attachment.kind !== "voice" && (
              <span className="v2-attach">{ATTACHMENT[t.attachment.kind]}{t.attachment.name ? ` · ${t.attachment.name}` : ""}</span>
            )}
            {t.text && t.text !== "…" && <p>{t.text}</p>}
            {(t.proposals ?? []).filter((p) => p.status === "pending").map((p) => (
              <div key={p.id} className="v2-picked"><span className="v2-dot" aria-hidden="true" />For your review · {describe(p).title}</div>
            ))}
          </li>
        ))}
        {live && !turns.length && !caption.heard && !caption.said && (
          <li className="v2-turn assistant is-live"><p>Your words appear here as you talk.</p></li>
        )}
        {live && caption.heard && <li className="v2-turn owner is-live"><p>{caption.heard}</p></li>}
        {live && caption.said && <li className="v2-turn assistant is-live"><p>{caption.said}</p></li>}
        {thinking && <li className="v2-turn assistant is-live"><p>Saathi is thinking…</p></li>}
      </ol>
      <div ref={end} className="v2-transcript-end" />
    </section>
  );
}
