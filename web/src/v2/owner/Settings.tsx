// The owner's Settings (voice fingerprinting): for now, the voice check. When it's on, a call that
// doesn't sound like the owner holds what Saathi picked up until they say it was them (Review.tsx).
// Turning it on asks them to read three short sentences; the server keeps only numbers that describe
// the voice, never the recording, and turning it off deletes them.
import { useEffect, useRef, useState } from "react";
import { api, type VoiceCheck as Status, type VoiceSample } from "../../api";
import { MicError } from "../../liveCall";
import { useAction } from "../../ui";
import { recordSample } from "../../voiceSample";
import { friendlyError } from "../copy";
import { Close, Mic } from "../icons";

const SECONDS = 5;
// Plain sentences with a mix of sounds; any of them works, in any order. Synthetic, like all demo data.
const SENTENCES = [
  "The monsoon came early this year, and the garden is green again.",
  "My daughter calls me every Sunday evening, after dinner.",
  "I learned to ride a bicycle on the road behind our old school.",
  "Please remember to water the tulsi plant on the balcony.",
  "We bought our first radio the year the cricket team toured England.",
];

export default function Settings({ owner, onClose }: { owner: string; onClose: () => void }) {
  // A native modal dialog, like the Demo menu: it traps focus, closes on Escape and dims the page.
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (!d.open) d.showModal();
    title.current?.focus();
    const onBackdrop = (e: MouseEvent) => { if (e.target === d) d.close(); };
    d.addEventListener("click", onBackdrop);
    return () => d.removeEventListener("click", onBackdrop);
  }, []);
  return (
    <dialog ref={dialog} className="v2-sheet" aria-labelledby="settings-title" onClose={onClose}>
      <header className="v2-sheet-top">
        <div className="v2-grab" aria-hidden="true" />
        <div className="v2-sheet-head">
          <div>
            <h2 id="settings-title" ref={title} tabIndex={-1}>Settings</h2>
            <p className="v2-sheet-sub">{owner.split(" ")[0]}'s own settings</p>
          </div>
          <button className="v2-close" onClick={() => dialog.current?.close()} aria-label="Close"><Close /></button>
        </div>
      </header>
      <div className="v2-sheet-scroll">
        <VoiceCheck />
      </div>
    </dialog>
  );
}

type Step = { kind: "idle" } | { kind: "recording"; left: number } | { kind: "checking" };

/** Turn the voice check on (three recordings), record again, or turn it off. */
export function VoiceCheck() {
  const [status, setStatus] = useState<Status | null>(null);
  const [registering, setRegistering] = useState(false);
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [level, setLevel] = useState(0);
  const [last, setLast] = useState<VoiceSample | null>(null);
  const [confirm, setConfirm] = useState<"off" | "again" | null>(null); // both delete the voiceprint: ask first
  const { busy, error, run } = useAction();
  const [micError, setMicError] = useState<string | null>(null);
  useEffect(() => { void run(async () => setStatus(await api.voiceCheck())); }, []);
  // Closing Settings stops a recording at once; nothing half-recorded is sent.
  const recordingNow = useRef<AbortController | null>(null);
  useEffect(() => () => recordingNow.current?.abort(), []);

  async function record() {
    setMicError(null);
    setLast(null);
    const started = Date.now();
    setStep({ kind: "recording", left: SECONDS });
    const tick = setInterval(() => setStep({ kind: "recording", left: Math.max(0, SECONDS - Math.floor((Date.now() - started) / 1000)) }), 250);
    const stop = new AbortController();
    recordingNow.current = stop;
    let pcm: ArrayBuffer;
    try {
      pcm = await recordSample(SECONDS, setLevel, stop.signal);
    } catch (e) {
      if (stop.signal.aborted) return;
      setMicError(e instanceof MicError && e.reason === "NotAllowedError"
        ? "The microphone is blocked for this site. Allow it in the browser's site settings, then try again."
        : `The microphone couldn't be used. ${friendlyError(e)}`);
      setStep({ kind: "idle" });
      return;
    } finally {
      clearInterval(tick);
      setLevel(0);
      recordingNow.current = null;
    }
    if (stop.signal.aborted) return;
    setStep({ kind: "checking" });
    await run(async () => {
      const added = await api.addVoiceSample(pcm);
      setLast(added);
      setStatus(added);
      if (added.on) setRegistering(false);
    });
    setStep({ kind: "idle" });
  }

  const startOver = (thenRegister: boolean) => run(async () => {
    setStatus(await api.forgetVoiceCheck());
    setLast(null);
    setConfirm(null);
    setRegistering(thenRegister);
  });

  if (!status) {
    return <section aria-label="Voice check">{error ? <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p> : <p className="v2-muted">Loading…</p>}</section>;
  }
  const recording = step.kind !== "idle";
  const sentence = SENTENCES[status.samples % SENTENCES.length];
  return (
    <section className="v2-voice-check" aria-labelledby="voice-check-title">
      <p id="voice-check-title" className="v2-group-label">Voice check</p>
      <div className="v2-panel v2-stack">
        {!status.set_up ? (
          <p className="v2-muted">The voice check isn't available on this server.</p>
        ) : status.on && !registering ? (
          <>
            <p className="v2-voice-state is-on"><Mic /> <b>On.</b> Calls are checked against your voice.</p>
            <p className="v2-muted v2-small">If a call doesn't sound like you, what Saathi picked up from it waits until you say it was you. If it gets your own voice wrong, record again in a quiet room, or turn it off.</p>
            {confirm === "off" ? (
              <div className="v2-confirm" role="alert">
                <p><strong>Turn off the voice check?</strong> The numbers that describe your voice are deleted. Calls won't be checked.</p>
                <div className="v2-row">
                  <button className="v2-btn danger" disabled={busy} onClick={() => void startOver(false)}>Turn it off</button>
                  <button className="v2-btn quiet" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              </div>
            ) : confirm === "again" ? (
              <div className="v2-confirm" role="alert">
                <p><strong>Record again?</strong> Your recordings so far are deleted, and calls aren't checked until you've read all three sentences.</p>
                <div className="v2-row">
                  <button className="v2-btn" disabled={busy} onClick={() => void startOver(true)}>Start recording again</button>
                  <button className="v2-btn quiet" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="v2-row">
                <button className="v2-btn quiet" disabled={busy} onClick={() => setConfirm("again")}>Record again</button>
                <button className="v2-link danger" disabled={busy} onClick={() => setConfirm("off")}>Turn off</button>
              </div>
            )}
          </>
        ) : !registering && status.samples === 0 ? (
          <>
            <p>Saathi can check that it's your voice on a call. If someone else speaks for you, what Saathi picked up waits until you say it was you.</p>
            <ul className="v2-small v2-voice-points">
              <li>You read three short sentences aloud, five seconds each.</li>
              <li>We keep only numbers that describe your voice, never the recording, and never with your name.</li>
              <li>You can turn it off at any time. That deletes them.</li>
            </ul>
            <button className="v2-btn" onClick={() => setRegistering(true)}>Turn on the voice check</button>
          </>
        ) : (
          <>
            <p className="v2-eyebrow">Recording {Math.min(status.samples + 1, status.needed)} of {status.needed}</p>
            <p>Read this aloud, at your normal voice, in a quiet place:</p>
            <p className="v2-quote v2-voice-sentence">“{sentence}”</p>
            {step.kind === "recording" && (
              <div className="v2-voice-meter">
                <span className="v2-voice-bar" style={{ transform: `scaleX(${Math.min(1, level * 1.6)})` }} aria-hidden="true" />
                <span role="status">Listening…</span>
                <span aria-hidden="true">{step.left}s</span>
              </div>
            )}
            {step.kind === "checking" && <p className="v2-muted" role="status">Checking…</p>}
            {last && !recording && (
              <p className={`v2-voice-result is-${last.kept ? last.consistency ?? "good" : "different"}`} role="status">
                {!last.kept ? "That didn't sound like your earlier recordings. Try again in a quiet place, or start over."
                  : last.consistency === "fair" ? "Kept. A quieter room helps."
                  : last.samples === 1 ? "Kept. Now the next sentence." : "Kept. That sounds like you."}
              </p>
            )}
            {micError && <p className="v2-error" role="alert">{micError}</p>}
            <div className="v2-row">
              <button className="v2-btn" disabled={busy || recording} onClick={() => void record()}>
                <Mic /> {recording ? "Recording…" : `Record ${SECONDS} seconds`}
              </button>
              {status.samples > 0 && <button className="v2-link" disabled={busy || recording} onClick={() => void startOver(true)}>Start over</button>}
              {status.samples === 0 && <button className="v2-link" disabled={recording} onClick={() => setRegistering(false)}>Not now</button>}
            </div>
          </>
        )}
        {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
      </div>
    </section>
  );
}
