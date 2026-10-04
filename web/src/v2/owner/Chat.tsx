// The typed side of Talk (v2): the whole conversation along the gold thread, with Saathi's words in
// the serif, the owner's in plain type, and Keep cards where they came up. Kept cards become beads.
import { useEffect, useRef, useState } from "react";
import { MORE, requestText, useAskForMore } from "../../allowance";
import type { Allowance, MoreKind, Proposal, Turn } from "../../api";
import { Camera, Chevron, Doc, Mic, Send, Speaker, SpeakerOff } from "../icons";
import { type Decide, KeepCard, plainKeep } from "./KeepCard";

const GREETING: Turn = {
  id: "greeting",
  role: "assistant",
  text: "Namaste. Tap the microphone and just talk to me, like a phone call. You can also type, or share a photo or a document.",
};

const ATTACHMENT = { voice: "Voice note", photo: "Photo", document: "Document" } as const;

const MEASURE: Record<string, string> = {
  tokens: "conversation",
  voice_minutes: "voice minutes",
  spoken_replies: "spoken replies",
};

export default function Chat({ turns, thinking, error, speak, allowance, onAsked, calling, onBack, onSend, onCall, onToggleSpeak, onDecide, onKeepAll, deciding }: {
  turns: Turn[]; thinking: boolean; error: string | null; speak: boolean; allowance: Allowance | null; onAsked: () => void; calling: boolean;
  onBack: () => void; onSend: (text: string | null, file?: Blob, name?: string) => void; onCall: () => void;
  onToggleSpeak: () => void; onDecide: Decide; onKeepAll: (cards: Proposal[]) => void; deciding: ReadonlySet<string>;
}) {
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement | null>(null);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must return
  // nothing (or a cleanup function).
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, thinking]);

  const shown = turns.length ? turns : [GREETING];
  const submit = () => { if (text.trim()) { onSend(text.trim()); setText(""); } };

  return (
    <section className="v2-chat" aria-label="Conversation with Saathi">
      <div className="v2-chat-head">
        <button className="v2-back" onClick={onBack}><span className="v2-flip"><Chevron /></span>Talk</button>
        <button className="v2-speak" onClick={onToggleSpeak} aria-pressed={speak}>
          {speak ? <Speaker /> : <SpeakerOff />}
          {speak ? "Replies read aloud" : "Replies not read aloud"}
        </button>
      </div>

      <ol className="v2-thread" aria-live="polite">
        {shown.map((t) => {
          const cards = t.proposals ?? [];
          const plain = cards.filter(plainKeep);
          return (
            <li key={t.id} className={`v2-turn ${t.role}`}>
              {t.attachment && <span className="v2-attach">{ATTACHMENT[t.attachment.kind]}{t.attachment.name ? ` · ${t.attachment.name}` : ""}</span>}
              {t.text && <p>{t.text}</p>}
              {cards.map((p) => <KeepCard key={p.id} p={p} onDecide={onDecide} busy={deciding.has(p.id)} />)}
              {plain.length > 1 && (
                <button className="v2-link v2-keep-all" disabled={deciding.size > 0} onClick={() => onKeepAll(plain)}>Keep all {plain.length}</button>
              )}
            </li>
          );
        })}
        {thinking && <li className="v2-turn assistant typing" aria-label="Saathi is thinking">Saathi is thinking…</li>}
      </ol>
      {allowance && <AllowanceNotice allowance={allowance} speak={speak} onAsked={onAsked} />}
      {error && <p className="v2-error" role="alert">{error}</p>}
      <div ref={bottom} className="v2-chat-end" />

      <div className="v2-composer">
        <label className={`v2-icon-btn${thinking ? " is-disabled" : ""}`} title="Share a photo">
          <Camera />
          <span className="v2-visually-hidden">Share a photo</span>
          <input type="file" accept="image/*" capture="environment" hidden disabled={thinking}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onSend(null, f, f.name); e.target.value = ""; }} />
        </label>
        <label className={`v2-icon-btn${thinking ? " is-disabled" : ""}`} title="Share a document">
          <Doc />
          <span className="v2-visually-hidden">Share a document</span>
          <input type="file" accept="application/pdf,image/*" hidden disabled={thinking}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onSend(null, f, f.name); e.target.value = ""; }} />
        </label>
        <input id="v2-say" className="v2-say" value={text} onChange={(e) => setText(e.target.value)} disabled={thinking}
          placeholder="Type, or tap the microphone" aria-label="Message"
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        {text.trim() ? (
          <button className="v2-round" disabled={thinking} onClick={submit} aria-label="Send"><Send /></button>
        ) : (
          <button className="v2-round" disabled={thinking || calling} onClick={onCall} aria-label="Talk to Saathi"><Mic /></button>
        )}
      </div>
    </section>
  );
}

/** A gentle note near or at today's allowance (PR-10.4), with "Ask for more" (PR-10.6). It never blocks the screen. */
function AllowanceNotice({ allowance, speak, onAsked }: { allowance: Allowance; speak: boolean; onAsked: () => void }) {
  const f = useAskForMore(allowance, onAsked);
  const { owner, requests, asking, kind, reason, error, waiting } = f;
  const reached = f.reachedKeys.map((k) => MEASURE[k] ?? k);
  const near = f.nearKeys.map((k) => MEASURE[k] ?? k);
  if (!reached.length && !near.length && !requests.length) return null;
  return (
    <div className="v2-notice v2-stack" role="status">
      {(reached.length > 0 || near.length > 0) && (
        <p>
          {reached.length > 0
            ? `You've used today's ${reached.join(" and ")}. It resets tomorrow, or you can ask for more.`
            : `You've talked a lot with Saathi today: you're close to today's ${near.join(" and ")}.`}
          {!speak && owner.spoken_replies && owner.spoken_replies.state !== "ok" &&
            " Replies aren't read aloud automatically now; tap “Replies not read aloud” to turn it back on."}
        </p>
      )}
      {requests.map((r) => <p key={r.id}>{requestText(r)}</p>)}
      {(reached.length > 0 || near.length > 0) && !asking && (
        <div><button className="v2-link" onClick={f.open}>Ask for more</button></div>
      )}
      {asking && (
        <div className="v2-stack">
          <select className="v2-input" value={kind} onChange={(e) => f.setKind(e.target.value as MoreKind)} aria-label="What you need more of">
            {Object.values(MORE).map((m) => <option key={m.kind} value={m.kind}>{m.step} more {m.unit}</option>)}
          </select>
          <input className="v2-input" value={reason} onChange={(e) => f.setReason(e.target.value)} maxLength={300}
            placeholder="Why? e.g. recording a long family story" aria-label="Why you need more" />
          <div className="v2-row">
            <button className="v2-btn" disabled={!f.canAsk} onClick={f.ask}>{waiting ? "Already asked" : "Ask"}</button>
            <button className="v2-btn quiet" onClick={f.close}>Cancel</button>
          </div>
        </div>
      )}
      {error && <p className="v2-error">{error}</p>}
    </div>
  );
}
