import { useEffect, useRef, useState } from "react";
import { api, type InventoryItem, type Moment, type Person, type Proposal, type Turn } from "./api";
import { LiveCall, type CallEvent } from "./liveCall";

const GREETING: Turn = {
  id: "greeting",
  role: "assistant",
  text: "Namaste Vikram ji. Tap the microphone and just talk to me, like a phone call. You can also type, or share a photo or document.",
  language: "en-IN",
};

const ATTACHMENT_LABEL = { voice: "🎙 Voice", photo: "📷 Photo", document: "📄 Document" } as const;

/**
 * One conversation for everything. The microphone starts a live, hands-free
 * conversation (Gemini Live): no record or stop buttons, and you can interrupt.
 * Typing, photos and documents work alongside it.
 */
export default function Conversation() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [thinking, setThinking] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const call = useRef<LiveCall | null>(null);
  const [callState, setCallState] = useState<"off" | "connecting" | "listening" | "speaking">("off");
  const [caption, setCaption] = useState({ heard: "", said: "" });

  useEffect(() => {
    api.conversation().then((r) => setTurns(r.turns)).catch((e) => setError(String(e)));
  }, []);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and an
  // effect must return nothing (or a cleanup function).
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, thinking, caption]);
  useEffect(() => () => call.current?.end(), []);

  function stopSpeaking() {
    player.current?.pause();
    window.speechSynthesis?.cancel();
  }

  /** Spoken reply to a typed message or shared file (live calls speak for themselves). */
  async function sayAloud(turn: Turn) {
    if (!speak) return;
    stopSpeaking();
    try {
      const url = URL.createObjectURL(await api.speech(turn.id));
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      player.current = audio;
      await audio.play();
    } catch {
      if (!("speechSynthesis" in window)) return;
      const u = new SpeechSynthesisUtterance(turn.text);
      if (turn.language) u.lang = turn.language;
      window.speechSynthesis.speak(u);
    }
  }

  /** Add new turns; an item that shows up again moves to its latest mention. */
  function appendTurns(...added: Turn[]) {
    const ids = new Set(added.flatMap((t) => t.proposals?.map((p) => p.id) ?? []));
    setTurns((all) => [
      ...all.map((t) => (t.proposals ? { ...t, proposals: t.proposals.filter((p) => !ids.has(p.id)) } : t)),
      ...added,
    ]);
  }

  async function send(message: string | null, file?: Blob, fileName?: string) {
    setError(null);
    setThinking(true);
    try {
      const r = await api.say(message, file, fileName);
      appendTurns(r.owner_turn, r.assistant_turn);
      sayAloud(r.assistant_turn);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setThinking(false);
    }
  }

  function onCallEvent(e: CallEvent) {
    switch (e.type) {
      case "ready": setCallState("listening"); break;
      case "speaking": setCallState((s) => (s === "off" ? s : e.speaking ? "speaking" : "listening")); break;
      case "heard": setCaption((c) => ({ heard: c.said ? e.text : c.heard + e.text, said: c.said ? "" : c.said })); break;
      case "said": setCaption((c) => ({ ...c, said: c.said + e.text })); break;
      case "turn":
        appendTurns(e.owner_turn, e.assistant_turn);
        setCaption({ heard: "", said: "" });
        break;
      case "error": setError(e.detail); break;
      case "closed": call.current = null; setCallState("off"); setCaption({ heard: "", said: "" }); break;
    }
  }

  async function startCall() {
    setError(null);
    stopSpeaking();
    setCallState("connecting");
    const c = new LiveCall(onCallEvent);
    call.current = c;
    try {
      await c.start();
    } catch {
      setError("Couldn't start the conversation. Check microphone permission.");
      c.end();
      call.current = null;
      setCallState("off");
    }
  }

  function endCall() {
    call.current?.end();
    call.current = null;
    setCallState("off");
    setCaption({ heard: "", said: "" });
  }

  function decide(p: Proposal, decision: "approve" | "reject"): Promise<void> {
    return api.decide(p.id, p.collection, decision).then(() => {
      setTurns((all) => all.map((t) => ({
        ...t,
        proposals: t.proposals
          ?.map((x) => (x.id === p.id ? { ...x, status: "approved" as const } : x))
          .filter((x) => !(x.id === p.id && decision === "reject")),
      })));
    }).catch(() => {
      // The card is out of date (e.g. the server was reset): reload the real state.
      api.conversation().then((r) => setTurns(r.turns)).catch(() => {});
      setError("That suggestion is no longer available, so the conversation has been refreshed.");
    });
  }

  const inCall = callState !== "off";
  const shown = turns.length ? turns : [GREETING];

  return (
    <div className="convo">
      <div className="convo-head">
        <button className="link" onClick={() => { setSpeak(!speak); stopSpeaking(); }}
          aria-pressed={speak}>{speak ? "🔊 Replies read aloud" : "🔈 Replies muted"}</button>
      </div>

      <ol className="thread-list" aria-live="polite">
        {shown.map((t) => (
          <li key={t.id} className={`bubble ${t.role}`}>
            {t.attachment && <span className="attach">{ATTACHMENT_LABEL[t.attachment.kind]}</span>}
            <p>{t.text}</p>
            {t.proposals?.map((p) => <ProposalCard key={p.id} p={p} onDecide={decide} />)}
            {(t.proposals?.filter((p) => p.status === "pending").length ?? 0) > 1 && (
              <button className="link keep-all" onClick={async () => {
                // One at a time: family cards all update the same family list.
                for (const p of t.proposals!.filter((x) => x.status === "pending")) await decide(p, "approve");
              }}>
                Keep all {t.proposals!.filter((p) => p.status === "pending").length}
              </button>
            )}
          </li>
        ))}
        {inCall && (caption.heard || caption.said) && (
          <>
            {caption.heard && <li className="bubble owner live-caption"><p>{caption.heard}</p></li>}
            {caption.said && <li className="bubble assistant live-caption"><p>{caption.said}</p></li>}
          </>
        )}
        {thinking && <li className="bubble assistant typing" aria-label="Thinking">…</li>}
      </ol>
      <div ref={bottom} />
      {error && <p className="error" role="alert">{error}</p>}

      {inCall ? (
        <section className="composer call-panel" aria-label="Live conversation">
          <div className={`call-orb ${callState}`} aria-hidden="true" />
          <p className="call-status">
            {callState === "connecting" ? "Connecting…"
              : callState === "speaking" ? "Speaking… just talk to interrupt"
              : "Listening… talk whenever you're ready"}
          </p>
          <button className="danger end-call" onClick={endCall}>End</button>
        </section>
      ) : (
        <div className="composer">
          <label className={`icon-btn${thinking ? " disabled" : ""}`} title="Share a photo" aria-label="Share a photo">
            📷
            <input type="file" accept="image/*" capture="environment" hidden disabled={thinking}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) send(null, f, f.name); e.target.value = ""; }} />
          </label>
          <label className={`icon-btn${thinking ? " disabled" : ""}`} title="Share a document" aria-label="Share a document">
            📄
            <input type="file" accept="application/pdf,image/*" hidden disabled={thinking}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) send(null, f, f.name); e.target.value = ""; }} />
          </label>
          <input className="say" value={text} onChange={(e) => setText(e.target.value)} disabled={thinking}
            placeholder="Type, or tap the mic to talk" aria-label="Message"
            onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) { send(text.trim()); setText(""); } }} />
          {text.trim() ? (
            <button className="mic" disabled={thinking} onClick={() => { send(text.trim()); setText(""); }} aria-label="Send">➤</button>
          ) : (
            <button className="mic" disabled={thinking} onClick={startCall} aria-label="Start talking">🎤</button>
          )}
        </div>
      )}
    </div>
  );
}

function ProposalCard({ p, onDecide }: { p: Proposal; onDecide: (p: Proposal, d: "approve" | "reject") => void }) {
  const prefix = p.updates ? "Update · " : "";
  let title: string;
  let detail: string | null = null;
  let warning: string | null = null;
  if (p.collection === "people") {
    const person = p as Person;
    title = `${prefix}Family: ${person.name} (${person.relationship})`;
  } else if (p.collection === "moments") {
    const m = p as Moment;
    title = `${prefix}${m.kind}: ${m.title}`;
    detail = m.summary;
  } else {
    const i = p as InventoryItem;
    title = `${prefix}${i.institution} · ${i.product} ${i.account_hint}`;
    detail = i.nominee_or_beneficiary ? `Nominee: ${i.nominee_or_beneficiary}` : null;
    warning = i.missing_fields.length ? `Missing: ${i.missing_fields.join(", ")}` : null;
  }
  return (
    <div className="proposal">
      <p className="proposal-title">{title}</p>
      {detail && <p className="muted">{detail}</p>}
      {warning && <p className="warn">{warning}</p>}
      {p.status === "approved" ? (
        <p className="ok">✓ Saved</p>
      ) : (
        <div className="row">
          <button onClick={() => onDecide(p, "approve")}>Keep</button>
          <button className="secondary" onClick={() => onDecide(p, "reject")}>Not now</button>
        </div>
      )}
    </div>
  );
}
