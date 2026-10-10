import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { LiveCall, type CallEvent } from "../liveCall";
import Avatar, { type AvatarState, type Levels, type Tone } from "./Avatar";

const TONES: Tone[] = ["warm", "gentle", "light", "clear", "reassuring", "patient"];
const THINK_AFTER_MS = 700; // the owner quiet this long after speaking: Saathi is thinking

/** The avatar trial's one screen: tap Saathi to talk. The face follows the call as it happens. */
export default function Talk({ name }: { name: string }) {
  const [call, setCall] = useState<"off" | "connecting" | "on">("off");
  const [state, setState] = useState<AvatarState>("idle");
  const [tone, setTone] = useState<Tone>("warm");
  const [topic, setTopic] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [heard, setHeard] = useState("");
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<AvatarState | null>(null);
  const levels = useRef<Levels>({ saathi: 0, you: 0 });
  const voice = useRef<AnalyserNode | null>(null);
  const live = useRef<LiveCall | null>(null);
  const flags = useRef({ speaking: false, youAt: 0, heardSinceReply: false });

  // The plan's current step decides the tone (and with it the face); it can change after each exchange.
  const loadPlan = useCallback(() => {
    api.plan().then((p) => {
      setTone(p.current?.tone ?? "warm");
      setTopic(p.current && p.current.source !== "safe_route" ? p.current.topic : null);
    }).catch(() => {});
  }, []);
  useEffect(loadPlan, [loadPlan]);

  // Between call events, work out whether Saathi is listening or thinking from the owner's voice.
  useEffect(() => {
    if (call !== "on") return;
    const timer = setInterval(() => {
      const f = flags.current;
      if (f.speaking) return setState("speaking");
      const quiet = performance.now() - f.youAt;
      setState(quiet > THINK_AFTER_MS && f.heardSinceReply ? "thinking" : "listening");
    }, 120);
    return () => clearInterval(timer);
  }, [call]);

  const onEvent = useCallback((e: CallEvent) => {
    const f = flags.current;
    switch (e.type) {
      case "ready": setCall("on"); setState("listening"); break;
      case "heard": f.heardSinceReply = true; f.youAt = performance.now(); setHeard((h) => (h + e.text).slice(-160)); break;
      case "said": setSaid((s) => (s + e.text).slice(-200)); break;
      case "speaking":
        if (e.speaking && !f.speaking) { setSaid(""); f.heardSinceReply = false; }
        f.speaking = e.speaking;
        break;
      case "interrupted": f.speaking = false; levels.current.saathi = 0; break;  // the owner spoke over Saathi
      case "turn": setHeard(""); loadPlan(); break;  // the exchange is saved: the plan (and tone) may have moved on
      case "error": setError(e.detail); break;
      case "closed": setCall("off"); setState("idle"); voice.current = null; live.current = null; break;
    }
  }, [loadPlan]);

  const onLevel = useCallback((who: "you" | "saathi", level: number) => {
    levels.current[who] = level;
    if (who === "you" && level > 0.08) flags.current.youAt = performance.now();
  }, []);

  async function start() {
    setError(null); setSaid(""); setHeard(""); setPreview(null);
    setCall("connecting"); setState("connecting");
    const c = new LiveCall(onEvent, onLevel);
    live.current = c;
    try {
      await c.start();
      voice.current = c.saathiAnalyser;  // the mouth reads Saathi's voice from here, frame by frame
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setCall("off"); setState("idle");
    }
  }
  const end = () => live.current?.end();
  const toggleMute = () => { const m = !muted; setMuted(m); live.current?.setMuted(m); };

  // Without a call: try each state with a made-up voice, to see the face.
  useEffect(() => {
    if (!preview || call !== "off") return;
    const timer = setInterval(() => {
      const t = performance.now() / 1000;
      levels.current.saathi = preview === "speaking" ? Math.max(0, Math.sin(t * 7) * 0.35 + Math.sin(t * 2.3) * 0.15) : 0;
      levels.current.you = preview === "listening" ? Math.max(0, Math.sin(t * 5) * 0.3) : 0;
    }, 60);
    return () => clearInterval(timer);
  }, [preview, call]);

  const shown: AvatarState = call === "off" ? (preview ?? "idle") : state;
  const words = { idle: "Tap Saathi to talk", connecting: "Getting Saathi on the line…", listening: muted ? "You're muted" : "Saathi is listening",
    thinking: "Saathi is thinking", speaking: "Saathi is speaking" }[shown];

  return (
    <main className="v3">
      <header className="v3-head">
        <p className="v3-eyebrow">Saathi · avatar trial</p>
        <h1>{name ? `Namaste, ${name} ji` : "Namaste"}</h1>
      </header>

      <section className="v3-stage" aria-label="Saathi">
        <button type="button" className={`v3-avatar-btn v3-state-${shown}`} onClick={call === "off" ? start : end}
          aria-label={call === "off" ? "Start talking with Saathi" : "End the call"}>
          <Avatar state={shown} tone={tone} levels={levels} voice={voice} />
        </button>
        <p className="v3-status" aria-live="polite">{words}</p>
        {call !== "off" && (
          <div className="v3-controls">
            <button type="button" className="v3-btn quiet" onClick={toggleMute} aria-pressed={muted}>{muted ? "Unmute" : "Mute"}</button>
            <button type="button" className="v3-btn end" onClick={end}>End call</button>
          </div>
        )}
        {error && <p className="v3-error" role="alert">{error}</p>}
      </section>

      <section className="v3-captions" aria-live="polite">
        {said && <p className="v3-said"><span>Saathi</span>{said}</p>}
        {heard && <p className="v3-heard"><span>You</span>{heard}</p>}
        {call === "off" && topic && !said && <p className="v3-topic"><span>Today, perhaps</span>{topic}</p>}
      </section>

      <details className="v3-try">
        <summary>Try the expressions</summary>
        <p className="v3-muted">The face follows the tone of the plan's current step ({tone}). Try each one, and each state, without a call.</p>
        <fieldset className="v3-chips">
          <legend>Tone</legend>
          {TONES.map((t) => <button key={t} type="button" aria-pressed={tone === t} onClick={() => setTone(t)}>{t}</button>)}
        </fieldset>
        <fieldset className="v3-chips">
          <legend>State</legend>
          {(["idle", "listening", "thinking", "speaking"] as AvatarState[]).map((s) => (
            <button key={s} type="button" disabled={call !== "off"} aria-pressed={shown === s} onClick={() => setPreview(s === "idle" ? null : s)}>{s}</button>
          ))}
        </fieldset>
      </details>

      <p className="v3-foot">A trial of Saathi as a face. Everything works as in the app: what you say waits for Keep there.</p>
    </main>
  );
}
