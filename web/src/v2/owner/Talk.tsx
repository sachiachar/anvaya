// Talk (v2): one screen, one look, from hello to saved. The microphone goes live in place, with
// rings that move with whoever is speaking, and the conversation runs underneath it. Nothing asks
// for a decision mid-conversation: at the end Saathi sums it up, everything it picked up is marked
// to save, and the owner removes what they don't want, saves the rest in one tap, or discards the
// whole conversation. Typing and photos use the same screen and the same review.
import { useEffect, useRef, useState } from "react";
import { MORE, requestText, useAskForMore } from "../../allowance";
import { type Allowance, api, type MoreKind, type Turn } from "../../api";
import { LiveCall, type CallEvent } from "../../liveCall";
import { friendlyError } from "../copy";
import { Camera, Check, Doc, Mic, Send, Speaker, SpeakerOff } from "../icons";
import Review, { type ReviewChoice } from "./Review";
import Transcript from "./Transcript";

export type Caption = { heard: string; said: string };
type CallState = "off" | "connecting" | "listening" | "speaking";
type Phase = "talk" | "gathering" | "review" | "saved";

const MEASURE: Record<string, string> = { tokens: "conversation", voice_minutes: "voice minutes", spoken_replies: "spoken replies" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const pending = (turns: Turn[]) => turns.flatMap((t) => t.proposals ?? []).filter((p) => p.status === "pending");

export default function Talk() {
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [baseline, setBaseline] = useState<ReadonlySet<string> | null>(null); // turns from before this conversation
  const [phase, setPhase] = useState<Phase>("talk");
  const [thinking, setThinking] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const speakRef = useRef(true);
  const speech = useRef(0); // bumped by stopSpeaking, so a reply still loading never plays after it
  const spokenNoticeShown = useRef(false);

  const call = useRef<LiveCall | null>(null);
  const [callState, setCallState] = useState<CallState>("off");
  const [caption, setCaption] = useState<Caption>({ heard: "", said: "" });
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  const [asking, setAsking] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const callStarted = useRef(0);
  const orb = useRef<HTMLDivElement | null>(null);
  const saathiSpeaking = useRef(false); // while Saathi talks, the microphone mostly hears Saathi

  // The review: which turns it covers, the summary, and saving.
  const [reviewTurns, setReviewTurns] = useState<string[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryState, setSummaryState] = useState<"loading" | "ready" | "failed">("loading");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [savedCount, setSavedCount] = useState(0);
  const [callMinutes, setCallMinutes] = useState<number | null>(null);
  const [reviewEarlier, setReviewEarlier] = useState(false); // items left from before: no summary, no Discard
  const [retryReview, setRetryReview] = useState(false); // the conversation didn't load after a call

  function refreshAllowance() {
    api.allowance().then((a) => {
      setAllowance(a);
      const spoken = a.pools.owner?.spoken_replies;
      if (spoken && spoken.state !== "ok" && !spokenNoticeShown.current) {
        spokenNoticeShown.current = true;
        speakRef.current = false;
        setSpeak(false);
      }
    }).catch(() => { /* the notice is a nicety; never block the conversation */ });
  }

  // Until the conversation has loaded, nobody can start one: the review (and Discard) covers only
  // what's new since then, so without it Discard could reach the whole history.
  function loadConversation() {
    setError(null);
    api.conversation()
      .then((r) => { setTurns(r.turns); setBaseline(new Set(r.turns.map((t) => t.id))); })
      .catch((e) => setError(`${friendlyError(e)} Your conversation hasn't loaded yet.`));
  }

  useEffect(() => {
    loadConversation();
    api.owner().then((o) => setName(String(o.name ?? "").split(" ")[0])).catch(() => {});
    // Today's topic: the first open step Saathi can talk about, else the most harmful gap. Both are
    // already worked out; this never starts a new readiness check.
    api.tasks()
      .then((r) => r.tasks.find((t) => (t.action === "talk" || t.action === "family") && t.question_for_owner)?.question_for_owner ?? null)
      .then(async (q) => q ?? (await api.gaps()).gaps[0]?.text ?? null)
      .then(setTopic)
      .catch(() => setTopic(null));
    refreshAllowance();
    // Leaving Talk ends any call, even one still connecting, and any reply being read aloud.
    return () => { const c = call.current; call.current = null; c?.end(); stopSpeaking(); };
  }, []);

  // The call clock.
  useEffect(() => {
    if (callState === "off" || callState === "connecting") return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [callState === "off" || callState === "connecting"]);

  const all = turns ?? [];
  const session = baseline ? all.filter((t) => !baseline.has(t.id)) : [];
  const earlier = baseline ? all.filter((t) => baseline.has(t.id)) : [];
  const live = callState !== "off";

  // ---- Speaking typed replies aloud --------------------------------------------------------------
  function stopSpeaking() {
    speech.current += 1;
    player.current?.pause();
    player.current = null;
    window.speechSynthesis?.cancel();
  }

  async function sayAloud(turn: Turn) {
    if (!speakRef.current) return;
    stopSpeaking();
    const mine = speech.current;
    // Play only if nothing stopped speech while it loaded: a call, the speaker switch, or leaving Talk.
    const stillWanted = () => mine === speech.current && speakRef.current && !call.current;
    try {
      const blob = await api.speech(turn.id);
      if (!stillWanted()) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      player.current = audio;
      await audio.play();
    } catch {
      if (!("speechSynthesis" in window) || !stillWanted()) return;
      const u = new SpeechSynthesisUtterance(turn.text);
      if (turn.language) u.lang = turn.language;
      window.speechSynthesis.speak(u);
    }
  }

  // ---- Conversation --------------------------------------------------------------------------------
  /** Add new turns; an item that shows up again moves to its latest mention. */
  function appendTurns(...added: Turn[]) {
    const ids = new Set(added.flatMap((t) => t.proposals?.map((p) => p.id) ?? []));
    setTurns((prev) => [
      ...(prev ?? []).map((t) => (t.proposals ? { ...t, proposals: t.proposals.filter((p) => !ids.has(p.id)) } : t)),
      ...added,
    ]);
  }

  async function send(message: string | null, file?: Blob, fileName?: string) {
    if (!baseline) return;
    setError(null);
    setNotice(null);
    setThinking(true);
    try {
      const r = await api.say(message, file, fileName);
      appendTurns(r.owner_turn, r.assistant_turn);
      void sayAloud(r.assistant_turn).finally(refreshAllowance);
    } catch (e) {
      setError(friendlyError(e));
      refreshAllowance();
    } finally {
      setThinking(false);
    }
  }

  // ---- The live call -----------------------------------------------------------------------------
  function onCallEvent(e: CallEvent) {
    switch (e.type) {
      case "ready": setCallState("listening"); callStarted.current = Date.now(); break;
      case "speaking":
        saathiSpeaking.current = e.speaking;
        setCallState((s) => (s === "off" ? s : e.speaking ? "speaking" : "listening"));
        break;
      case "heard": setCaption((c) => ({ heard: c.said ? e.text : c.heard + e.text, said: c.said ? "" : c.said })); break;
      case "said": setCaption((c) => ({ ...c, said: c.said + e.text })); break;
      case "turn":
        appendTurns(e.owner_turn, e.assistant_turn);
        // The saved exchange now shows in the transcript; drop its live caption if it's still up.
        setCaption((c) => (c.said.trim() === e.assistant_turn.text.trim()
          || c.heard.trim() === (e.owner_turn.heard ?? e.owner_turn.text).trim() ? { heard: "", said: "" } : c));
        break;
      case "error": setError(friendlyError(new Error(e.detail))); break;
      case "closed": call.current = null; setCallState("off"); void afterCall(); break;
    }
  }

  /** Moves the rings with whoever is speaking, without re-rendering ten times a second. While Saathi
   *  speaks, the microphone mostly hears Saathi's own voice back, so its level is ignored. */
  function onLevel(who: "you" | "saathi", level: number) {
    const el = orb.current;
    if (!el || (who === "you" && saathiSpeaking.current)) return;
    el.style.setProperty("--level", (who === "you" && mutedRef.current ? 0 : level).toFixed(2));
    el.dataset.who = who;
  }

  async function startCall() {
    if (call.current || !baseline) return; // one call at a time, and only once the conversation has loaded
    setError(null);
    setNotice(null);
    stopSpeaking();
    setMuted(false);
    mutedRef.current = false;
    setSeconds(0);
    callStarted.current = Date.now();
    saathiSpeaking.current = false;
    setCaption({ heard: "", said: "" });
    setCallState("connecting");
    // Events from a call that has ended (or been replaced) are ignored, so a late "closed" from the
    // last call can't close this one.
    const c: LiveCall = new LiveCall((e) => { if (call.current === c) onCallEvent(e); }, onLevel);
    call.current = c;
    try {
      await c.start();
      if (call.current !== c) { c.end(); return; } // ended while connecting: close what just opened
      c.setMuted(mutedRef.current);
    } catch {
      c.end();
      if (call.current !== c) return;
      setError("Saathi couldn't start the call. Please allow the microphone for this site, then try again.");
      call.current = null;
      setCallState("off");
    }
  }

  function endCall() {
    const c = call.current;
    call.current = null; // first, so startCall sees the call has ended if it's still connecting
    c?.end();
    setCallState("off");
    void afterCall();
  }

  function toggleMute() {
    const next = !mutedRef.current;
    mutedRef.current = next;
    call.current?.setMuted(next);
    setMuted(next);
  }

  /** After a call, Saathi may still be reading the last exchange: the server stores each exchange
   *  straight away, marked "saving" until its cards are ready. Wait until nothing from this
   *  conversation is still saving (at most about 30 seconds), then review all of it. */
  async function afterCall() {
    setCaption({ heard: "", said: "" });
    saathiSpeaking.current = false;
    if (!baseline) { setPhase("talk"); return; }
    setCallMinutes(Math.max(1, Math.round((Date.now() - callStarted.current) / 60000)));
    setPhase("gathering");
    let latest: Turn[] | null = null;
    for (let i = 0; i < 20; i++) {
      await sleep(1500);
      try {
        latest = (await api.conversation()).turns;
        if (!latest.some((t) => !baseline.has(t.id) && t.saving)) break;
      } catch { /* try again */ }
    }
    if (!latest) {
      // Nothing loaded at all: the call is saved on the server, so offer to try again rather than
      // saying nothing was heard.
      setPhase("talk");
      setError("Saathi couldn't load your conversation just now. What you said is saved.");
      setRetryReview(true);
      return;
    }
    setRetryReview(false);
    setTurns(latest);
    const ids = latest.filter((t) => !baseline.has(t.id)).map((t) => t.id);
    if (!ids.length) {
      setPhase("talk");
      setNotice("Saathi didn't catch anything in that call. Try again whenever you're ready.");
      return;
    }
    openReview(ids, true);
  }

  // ---- Review: save the rest, or discard ------------------------------------------------------------
  function openReview(ids: string[], summarize: boolean) {
    setReviewEarlier(!summarize);
    setReviewTurns(ids);
    setReviewError(null);
    setSummary(null);
    setPhase("review");
    if (!summarize) { setSummaryState("failed"); return; }
    setSummaryState("loading");
    api.summarizeConversation(ids)
      .then((r) => { setSummary(r.summary); setSummaryState("ready"); })
      .catch(() => setSummaryState("failed")); // the list below is enough on its own
  }

  /** A fresh start: everything so far counts as earlier conversation. */
  async function closeConversation() {
    try {
      const r = await api.conversation();
      setTurns(r.turns);
      setBaseline(new Set(r.turns.map((t) => t.id)));
    } catch {
      setBaseline(new Set(all.map((t) => t.id)));
    }
    setCallMinutes(null);
  }

  async function save(choices: ReviewChoice[]) {
    setReviewBusy(true);
    setReviewError(null);
    let saved = 0;
    const failed: string[] = [];
    let updateRefused = false;
    for (const { p, save: keep, replace } of choices) {
      try {
        await api.decide(p.id, p.collection, keep ? "approve" : "reject", keep && replace);
        if (keep) saved += 1;
      } catch {
        failed.push(p.id);
        if (keep && replace) updateRefused = true;
      }
    }
    setReviewBusy(false);
    if (failed.length) {
      // Show what's still waiting (some may have been decided elsewhere, e.g. after a demo reset).
      const r = await api.conversation().catch(() => null);
      if (r) setTurns(r.turns);
      setReviewError(updateRefused
        ? "An earlier item you chose to update isn't there any more, so it couldn't be updated. Choose Save as new for it, then save again."
        : `${failed.length === 1 ? "One item" : `${failed.length} items`} couldn't be saved. The list shows what's still waiting; try again.`);
      return;
    }
    setSavedCount(saved);
    setPhase("saved");
    await closeConversation();
  }

  async function discard() {
    setReviewBusy(true);
    setReviewError(null);
    try {
      await api.forgetConversation(reviewTurns);
      await closeConversation();
      setPhase("talk");
      setNotice("The conversation is deleted. Nothing from it was kept.");
    } catch (e) {
      setReviewError(friendlyError(e));
    } finally {
      setReviewBusy(false);
    }
  }

  async function later() {
    await closeConversation();
    setPhase("talk");
    setNotice("Nothing is saved yet. These will wait for you here next time.");
  }

  /** "Talk about this": Saathi asks the question first, then the call carries on from it. */
  async function talkAbout(question: string) {
    if (asking || call.current || !baseline) return;
    setError(null);
    setAsking(true);
    try {
      appendTurns(await api.companionAsks(question));
    } catch (e) {
      setError(friendlyError(e));
      return;
    } finally {
      setAsking(false);
    }
    void startCall();
  }

  // ---- What shows --------------------------------------------------------------------------------
  const sessionWaiting = pending(session);
  const earlierWaiting = pending(earlier);
  const reviewItems = pending(all.filter((t) => reviewTurns.includes(t.id)));
  const lastSaid = [...earlier].reverse().find((t) => t.role === "owner" && t.text && t.text !== "…")?.text ?? null;
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  const heading = callState === "connecting" ? "Connecting…"
    : callState === "speaking" ? "Saathi is speaking"
    : callState === "listening" ? (muted ? "You're muted" : "Saathi is listening")
    : phase === "gathering" ? "Gathering what you said…"
    : `Namaste${name ? `, ${name} ji` : ""}.`;
  const sub = callState === "connecting" ? "Getting Saathi on the line."
    : callState === "speaking" ? "Just talk to interrupt, like a phone call."
    : callState === "listening" ? (muted ? "Tap Unmute to talk again." : "Talk whenever you're ready. Your words appear below.")
    : phase === "gathering" ? "Saathi is finishing the last thing you said. This takes a few seconds."
    : topic ? "Shall we talk about this today?" : "Whenever you're ready, just talk. Saathi is listening.";

  if (phase === "review") {
    const meta = reviewEarlier ? "From earlier" : callMinutes ? `Your call · ${callMinutes} min` : "Your conversation";
    return (
      <div className="v2-talk">
        <Review items={reviewItems} summary={summary} summaryState={summaryState} meta={meta}
          busy={reviewBusy} error={reviewError} onSave={save} onLater={later} onDiscard={reviewEarlier ? null : discard} />
        <details className="v2-earlier">
          <summary><b>The whole conversation</b><span>What you and Saathi said</span></summary>
          <Transcript turns={all.filter((t) => reviewTurns.includes(t.id))} caption={{ heard: "", said: "" }} live={false} thinking={false} />
        </details>
      </div>
    );
  }

  if (phase === "saved") {
    return (
      <div className="v2-talk">
        <section className="v2-saved" aria-live="polite">
          <div className="v2-saved-beads" aria-hidden="true">
            {Array.from({ length: Math.max(1, Math.min(savedCount, 7)) }, (_, i) => <span key={i} style={{ animationDelay: `${i * 90}ms` }} />)}
          </div>
          <h2>{savedCount === 0 ? "Nothing was saved" : savedCount === 1 ? "Saved 1 thing for your family" : `Saved ${savedCount} things for your family`}</h2>
          <p className="v2-muted">{savedCount ? "They're in your book, under Kept. You can remove anything there later." : "That's fine. You can talk again whenever you like."}</p>
          <button className="v2-btn" onClick={() => setPhase("talk")}>Back to Talk</button>
        </section>
      </div>
    );
  }

  return (
    <div className={`v2-talk${live ? " is-live" : ""}`}>
      <header className="v2-talk-head" aria-live="polite">
        <h1>{heading}</h1>
        <p>{sub}</p>
        {live && callState !== "connecting" && <p className="v2-clock" aria-hidden="true">{clock}</p>}
      </header>

      {!live && phase === "talk" && topic && (
        <div className="v2-topic">
          <p className="v2-eyebrow">Today, perhaps</p>
          <p className="v2-topic-q">{topic}</p>
          <button className="v2-link" disabled={!baseline || asking} onClick={() => talkAbout(topic)}>
            {asking ? "Saathi is getting ready…" : "Talk about this"}
          </button>
        </div>
      )}

      <div className="v2-stage">
        <div ref={orb} className={`v2-orb is-${phase === "gathering" ? "gathering" : callState}${muted ? " is-muted" : ""}`}>
          <span className="v2-ring r1" aria-hidden="true" />
          <span className="v2-ring r2" aria-hidden="true" />
          <span className="v2-ring r3" aria-hidden="true" />
          <button className="v2-mic" onClick={startCall} disabled={!baseline || live || asking || phase !== "talk"}
            aria-label={live ? "Call in progress" : "Talk to Saathi"}>
            <Mic />
            {!live && phase === "talk" && <span>Talk to Saathi</span>}
          </button>
        </div>
        {live && (
          <div className="v2-call-controls">
            <button className="v2-pill" onClick={toggleMute} aria-pressed={muted}><Mic />{muted ? "Unmute" : "Mute"}</button>
            <button className="v2-pill end" onClick={endCall}>End call</button>
          </div>
        )}
      </div>

      {notice && !live && <p className="v2-notice-card" role="status">{notice}</p>}
      {error && <p className="v2-error" role="alert">{error}</p>}
      {!baseline && error && <button className="v2-btn quiet" onClick={loadConversation}>Try again</button>}
      {retryReview && !live && <button className="v2-btn quiet" onClick={() => { setError(null); void afterCall(); }}>Review the call</button>}

      {!live && phase === "talk" && baseline && session.length === 0 && (
        <div className="v2-alts">
          <button className="v2-alt" onClick={() => input.current?.focus()}>Type instead</button>
          <label className="v2-alt">
            <Camera /> Share a photo
            <input type="file" accept="image/*" capture="environment" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(null, f, f.name); e.target.value = ""; }} />
          </label>
          <label className="v2-alt">
            <Doc /> Share a document
            <input type="file" accept="application/pdf,image/*" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(null, f, f.name); e.target.value = ""; }} />
          </label>
        </div>
      )}

      {!live && phase === "talk" && session.length === 0 && earlierWaiting.length > 0 && (
        <button className="v2-waiting" onClick={() => openReview(earlier.filter((t) => pending([t]).length).map((t) => t.id), false)}>
          <b>{earlierWaiting.length === 1 ? "1 thing from earlier is" : `${earlierWaiting.length} things from earlier are`} waiting</b>
          <span>Review and save them</span>
        </button>
      )}

      <Transcript turns={session} caption={caption} live={live} thinking={thinking} />

      {!live && phase === "talk" && session.length === 0 && lastSaid && (
        <details className="v2-earlier">
          <summary>
            <b>Last time you said</b>
            <span>“{lastSaid.length > 90 ? `${lastSaid.slice(0, 90).trimEnd()}…` : lastSaid}”</span>
          </summary>
          <Transcript turns={earlier.slice(-24)} caption={{ heard: "", said: "" }} live={false} thinking={false} />
        </details>
      )}

      {allowance && <AllowanceNotice allowance={allowance} speak={speak} onAsked={refreshAllowance} />}

      {!live && phase === "talk" && baseline && (
        <div className="v2-dock">
          {session.length > 0 && (
            <div className="v2-review-bar">
              <span>{sessionWaiting.length ? `${sessionWaiting.length === 1 ? "1 thing" : `${sessionWaiting.length} things`} to review` : "When you're done"}</span>
              <button className="v2-btn" onClick={() => openReview(session.map((t) => t.id), true)}>
                <Check />{sessionWaiting.length ? "Review and save" : "Finish"}
              </button>
            </div>
          )}
          <div className="v2-composer">
            <button className="v2-icon-btn" onClick={() => { speakRef.current = !speak; setSpeak(!speak); stopSpeaking(); }}
              aria-pressed={speak} aria-label={speak ? "Replies are read aloud. Turn off" : "Replies aren't read aloud. Turn on"}>
              {speak ? <Speaker /> : <SpeakerOff />}
            </button>
            <input ref={input} id="v2-say" className="v2-say" value={text} onChange={(e) => setText(e.target.value)} disabled={thinking}
              placeholder="Type to Saathi" aria-label="Message"
              onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) { void send(text.trim()); setText(""); } }} />
            <button className="v2-round" disabled={thinking || !text.trim()} aria-label="Send"
              onClick={() => { void send(text.trim()); setText(""); }}><Send /></button>
          </div>
        </div>
      )}
    </div>
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
            " Replies aren't read aloud automatically now; tap the speaker to turn it back on."}
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
