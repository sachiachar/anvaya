// Talk (v2): the owner's conversation with Saathi, call first. Home is one sentence and one big
// button; the call fills the screen with large captions and asks Keep only while the owner is
// quiet; typing, photos and documents live in the chat. The conversation logic follows the classic
// Conversation.tsx (copied, so the classic app stays untouched).
import { useEffect, useRef, useState } from "react";
import { type Allowance, api, type Proposal, type Turn } from "../../api";
import { LiveCall, type CallEvent } from "../../liveCall";
import { friendlyError } from "../copy";
import { Camera, Doc, Mic } from "../icons";
import Call, { type CallState } from "./Call";
import Chat from "./Chat";
import { plainKeep } from "./KeepCard";

export type Caption = { heard: string; said: string };

export default function Talk() {
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [view, setView] = useState<"home" | "chat">("home");
  const [thinking, setThinking] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const spokenNoticeShown = useRef(false);

  const call = useRef<LiveCall | null>(null);
  const [callState, setCallState] = useState<CallState>("off");
  const [caption, setCaption] = useState<Caption>({ heard: "", said: "" });
  const [muted, setMuted] = useState(false);
  const [callTurns, setCallTurns] = useState<string[]>([]); // Saathi's turns from this call, for the Keep sheet

  function refreshAllowance() {
    api.allowance().then((a) => {
      setAllowance(a);
      const spoken = a.pools.owner?.spoken_replies;
      if (spoken && spoken.state !== "ok" && !spokenNoticeShown.current) {
        spokenNoticeShown.current = true;
        setSpeak(false);
      }
    }).catch(() => { /* the notice is a nicety; never block the conversation */ });
  }

  useEffect(() => {
    api.conversation().then((r) => setTurns(r.turns)).catch((e) => { setTurns([]); setError(friendlyError(e)); });
    api.owner().then((o) => setName(String(o.name ?? "").split(" ")[0])).catch(() => {});
    // Today's topic: the first open step Saathi can talk about, else the most harmful gap. Both are
    // already worked out; this never starts a new readiness check.
    api.tasks()
      .then((r) => r.tasks.find((t) => (t.action === "talk" || t.action === "family") && t.question_for_owner)?.question_for_owner ?? null)
      .then(async (q) => q ?? (await api.gaps()).gaps[0]?.text ?? null)
      .then(setTopic)
      .catch(() => setTopic(null));
    refreshAllowance();
  }, []);
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
      ...(all ?? []).map((t) => (t.proposals ? { ...t, proposals: t.proposals.filter((p) => !ids.has(p.id)) } : t)),
      ...added,
    ]);
  }

  async function send(message: string | null, file?: Blob, fileName?: string) {
    setError(null);
    setView("chat");
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

  function onCallEvent(e: CallEvent) {
    switch (e.type) {
      case "ready": setCallState("listening"); break;
      case "speaking": setCallState((s) => (s === "off" ? s : e.speaking ? "speaking" : "listening")); break;
      case "heard": setCaption((c) => ({ heard: c.said ? e.text : c.heard + e.text, said: c.said ? "" : c.said })); break;
      case "said": setCaption((c) => ({ ...c, said: c.said + e.text })); break;
      case "turn":
        appendTurns(e.owner_turn, e.assistant_turn);
        setCallTurns((ids) => [...ids, e.assistant_turn.id]);
        break;
      case "error": setError(friendlyError(new Error(e.detail))); break;
      case "closed": call.current = null; setCallState("off"); setCaption({ heard: "", said: "" }); break;
    }
  }

  async function startCall() {
    setError(null);
    stopSpeaking();
    setMuted(false);
    setCallTurns([]);
    setCaption({ heard: "", said: "" });
    setCallState("connecting");
    const c = new LiveCall(onCallEvent);
    call.current = c;
    try {
      await c.start();
    } catch {
      setError("Saathi couldn't start the call. Please allow the microphone for this site, then try again.");
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

  // However a call ends (End, or the server closing it), show what was said and any cards still waiting.
  const wasInCall = useRef(false);
  useEffect(() => {
    if (callState !== "off") { wasInCall.current = true; return; }
    if (wasInCall.current && callTurns.length) setView("chat");
    wasInCall.current = false;
  }, [callState]);

  function toggleMute() {
    setMuted((m) => { call.current?.setMuted(!m); return !m; });
  }

  /** "Talk about this": Saathi asks the question first, then the call carries on from it. */
  async function talkAbout(question: string) {
    setError(null);
    try {
      appendTurns(await api.companionAsks(question));
    } catch (e) {
      setError(friendlyError(e));
      return;
    }
    void startCall();
  }

  function decide(p: Proposal, decision: "approve" | "reject", replace = false): Promise<void> {
    return api.decide(p.id, p.collection, decision, replace).then(() => {
      setTurns((all) => (all ?? []).map((t) => ({
        ...t,
        proposals: t.proposals
          ?.map((x) => (x.id === p.id ? { ...x, status: "approved" as const } : x))
          .filter((x) => !(x.id === p.id && decision === "reject")),
      })));
    }).catch(() => {
      // The card is out of date (e.g. the demo was reset): reload the real state.
      api.conversation().then((r) => setTurns(r.turns)).catch(() => {});
      setError("That card is no longer available, so the conversation has been refreshed.");
    });
  }

  /** One at a time: family cards all update the same family list. Cards that look like a kept
   *  item wait for the owner's own choice: Replace it or Keep both. */
  async function keepAll(cards: Proposal[]) {
    for (const p of cards.filter(plainKeep)) await decide(p, "approve");
  }

  const all = turns ?? [];
  const waiting = all.flatMap((t) => t.proposals ?? []).filter((p) => p.status === "pending");
  const fromCall = all.filter((t) => callTurns.includes(t.id)).flatMap((t) => t.proposals ?? []).filter((p) => p.status === "pending");
  const lastSaid = [...all].reverse().find((t) => t.role === "owner" && t.text)?.text ?? null;

  return (
    <>
      {view === "home" ? (
        <section className="v2-home" aria-labelledby="talk-hello">
          <div className="v2-hello">
            <h1 id="talk-hello">Namaste{name ? `, ${name} ji` : ""}.</h1>
            <p>{topic ? "Shall we talk about this today?" : "Whenever you're ready, just talk. Saathi is listening."}</p>
          </div>
          {topic && (
            <div className="v2-topic">
              <p className="v2-eyebrow">Today, perhaps</p>
              <p className="v2-topic-q">{topic}</p>
              <button className="v2-link" disabled={callState !== "off"} onClick={() => talkAbout(topic)}>Talk about this</button>
            </div>
          )}
          <div className="v2-callwrap">
            <button className="v2-bigcall" onClick={startCall} disabled={callState !== "off"}>
              <Mic />
              <span>Talk to Saathi</span>
            </button>
          </div>
          <div className="v2-alts">
            <button className="v2-alt" onClick={() => setView("chat")}>Type instead</button>
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
          {waiting.length > 0 && (
            <button className="v2-waiting" onClick={() => setView("chat")}>
              <b>{waiting.length === 1 ? "1 thing is" : `${waiting.length} things are`} waiting for your Keep</b>
              <span>See them in the conversation</span>
            </button>
          )}
          {lastSaid && waiting.length === 0 && (
            <button className="v2-last" onClick={() => setView("chat")}>
              <b>Last time you said</b>
              <span>“{lastSaid.length > 90 ? `${lastSaid.slice(0, 90).trimEnd()}…` : lastSaid}”</span>
            </button>
          )}
          {error && <p className="v2-error" role="alert">{error}</p>}
        </section>
      ) : (
        <Chat turns={all} thinking={thinking} error={error} speak={speak} allowance={allowance}
          onBack={() => setView("home")} onSend={send} onCall={startCall} calling={callState !== "off"}
          onToggleSpeak={() => { setSpeak(!speak); stopSpeaking(); }} onDecide={decide} onKeepAll={keepAll} />
      )}

      {callState !== "off" && (
        <Call state={callState} caption={caption} muted={muted} waiting={fromCall}
          onMute={toggleMute} onEnd={endCall} onDecide={decide} onKeepAll={keepAll} />
      )}
    </>
  );
}
