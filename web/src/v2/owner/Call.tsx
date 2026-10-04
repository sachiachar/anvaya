// The live call (v2): always navy, like a phone call. Large captions do the reading. Keep cards from
// this call rise as a sheet only while Saathi is quiet, so they never cut across what it's saying,
// and the call carries on while the owner decides.
import { useEffect, useRef, useState } from "react";
import type { Proposal } from "../../api";
import { Mic, ThreadMark } from "../icons";
import { type Decide, KeepCard, plainKeep } from "./KeepCard";
import type { Caption } from "./Talk";

export type CallState = "off" | "connecting" | "listening" | "speaking";

const STATUS: Record<Exclude<CallState, "off">, string> = {
  connecting: "Connecting…",
  listening: "Listening · talk whenever you're ready",
  speaking: "Saathi is speaking · just talk to interrupt",
};

export default function Call({ state, caption, muted, waiting, onMute, onEnd, onDecide, onKeepAll }: {
  state: Exclude<CallState, "off">; caption: Caption; muted: boolean; waiting: Proposal[];
  onMute: () => void; onEnd: () => void; onDecide: Decide; onKeepAll: (cards: Proposal[]) => void;
}) {
  const end = useRef<HTMLButtonElement | null>(null);
  const [seconds, setSeconds] = useState(0);
  useEffect(() => { end.current?.focus(); }, []);
  useEffect(() => {
    if (state === "connecting") return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state === "connecting"]);

  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const showSheet = state === "listening" && waiting.length > 0;
  const plain = waiting.filter(plainKeep);

  return (
    <div className="v2-call" role="dialog" aria-modal="true" aria-label="Call with Saathi">
      <div className="v2-call-top">
        <p className="v2-call-who">Saathi</p>
        <p className="v2-call-status" role="status">{muted ? "Muted · tap Unmute to talk" : STATUS[state]}</p>
        {state !== "connecting" && <p className="v2-call-clock" aria-hidden="true">{clock}</p>}
      </div>

      <div className={`v2-orb is-${state}`} aria-hidden="true"><ThreadMark /></div>

      <div className="v2-captions" aria-live="polite">
        {caption.said && <p className="v2-said">{caption.said}</p>}
        {caption.heard && <p className="v2-heard"><span>You:</span> {caption.heard}</p>}
        {!caption.said && !caption.heard && state !== "connecting" && (
          <p className="v2-heard">Tell Saathi a story, an account, a wish, or anything your family should know.</p>
        )}
      </div>

      {showSheet && (
        <section className="v2-call-sheet" aria-label="Waiting for your Keep">
          <p className="v2-call-sheet-note">Saathi added this to your list to review. Nothing is kept until you say so.</p>
          <KeepCard p={waiting[0]} onDecide={onDecide} />
          {waiting.length > 1 && (
            <div className="v2-row">
              <span className="v2-muted v2-small">{waiting.length - 1} more waiting</span>
              {plain.length > 1 && <button className="v2-link" onClick={() => onKeepAll(plain)}>Keep all {plain.length}</button>}
            </div>
          )}
        </section>
      )}

      <div className="v2-call-controls">
        <button className={`v2-ctl${muted ? " is-on" : ""}`} onClick={onMute} aria-pressed={muted}>
          <Mic />
          {muted ? "Unmute" : "Mute"}
        </button>
        <button ref={end} className="v2-ctl end" onClick={onEnd}>
          <span className="v2-end-glyph" aria-hidden="true" />
          End
        </button>
      </div>
    </div>
  );
}
