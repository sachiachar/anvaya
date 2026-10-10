// A live voice call with the companion (Gemini Live API, relayed by our server).
// Microphone: 16 kHz 16-bit PCM in 100 ms chunks. Voice back: 24 kHz 16-bit PCM.
import { currentOwnerId, currentPerson, type Turn } from "./api";
import { idToken } from "./auth";

export type CallEvent =
  | { type: "ready" }
  | { type: "heard" | "said"; text: string }
  | { type: "interrupted" }
  | { type: "turn"; owner_turn: Turn; assistant_turn: Turn }
  | { type: "speaking"; speaking: boolean }
  | { type: "error"; detail: string }
  | { type: "closed" };

// In production, Firebase Hosting can't proxy WebSockets, so point this at the
// Cloud Run URL (e.g. wss://life-after-agents-xxxx.a.run.app).
const BASE = import.meta.env.VITE_LIVE_URL ?? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

export class LiveCall {
  private ws: WebSocket | null = null;
  private micCtx: AudioContext | null = null;
  private speakerCtx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private playing = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private levelTimers = new Set<ReturnType<typeof setTimeout>>();
  // Saathi's voice passes through one gain node at full volume, which an analyser listens to, so an
  // animation can follow the voice frame by frame as it plays (the v3 avatar trial). Same sound.
  private speakerOut: GainNode | null = null;
  private analyser: AnalyserNode | null = null;

  /** Saathi's voice as it plays, for an animation that follows it closely; null before the call starts. */
  get saathiAnalyser(): AnalyserNode | null {
    return this.analyser;
  }

  /** onLevel (optional): how loud each side is, 0 to 1, about ten times a second, for an animation. */
  constructor(private onEvent: (e: CallEvent) => void, private onLevel?: (who: "you" | "saathi", level: number) => void) {}

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.micCtx = new AudioContext({ sampleRate: 16000 });
    this.speakerCtx = new AudioContext({ sampleRate: 24000 });
    this.speakerOut = this.speakerCtx.createGain();
    this.speakerOut.connect(this.speakerCtx.destination);
    this.analyser = this.speakerCtx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.35;
    this.speakerOut.connect(this.analyser);
    await this.micCtx.audioWorklet.addModule("/pcm-recorder.js");

    // Browsers can't send headers on a WebSocket, so the sign-in token goes in the URL.
    const token = await idToken();
    const query = new URLSearchParams({ acting_as: currentPerson(), ...(token ? { token } : {}) });
    const ws = new WebSocket(`${BASE}/api/owners/${currentOwnerId()}/live?${query}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (m) => {
      if (m.data instanceof ArrayBuffer) return this.play(m.data);
      const event = JSON.parse(m.data) as CallEvent;
      if (event.type === "interrupted") this.stopPlayback();
      this.onEvent(event);
    };
    ws.onclose = (e) => {
      this.cleanup();
      if (e.code === 4401 || e.code === 4403) this.onEvent({ type: "error", detail: e.reason || "Please sign in again." });
      this.onEvent({ type: "closed" });
    };
    ws.onerror = () => this.onEvent({ type: "error", detail: "Could not connect the call." });

    const source = this.micCtx.createMediaStreamSource(this.stream);
    const recorder = new AudioWorkletNode(this.micCtx, "pcm-recorder");
    recorder.port.onmessage = (e) => {
      if (this.onLevel) this.onLevel("you", loudness(new Int16Array(e.data)));
      if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
    };
    source.connect(recorder);
  }

  end() {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "end" }));
    this.ws?.close();
    this.cleanup();
  }

  private play(data: ArrayBuffer) {
    const ctx = this.speakerCtx;
    if (!ctx) return;
    const pcm = new Int16Array(data);
    const buffer = ctx.createBuffer(1, pcm.length, 24000);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(this.speakerOut ?? ctx.destination);
    this.nextStart = Math.max(this.nextStart, ctx.currentTime);
    if (this.onLevel) {  // reported when this piece plays, not when it arrives
      const level = loudness(pcm);
      const timer = setTimeout(() => { this.levelTimers.delete(timer); this.onLevel?.("saathi", level); },
        (this.nextStart - ctx.currentTime) * 1000);
      this.levelTimers.add(timer);
    }
    node.start(this.nextStart);
    this.nextStart += buffer.duration;
    this.playing.add(node);
    this.onEvent({ type: "speaking", speaking: true });
    node.onended = () => {
      this.playing.delete(node);
      if (this.playing.size === 0) this.onEvent({ type: "speaking", speaking: false });
    };
  }

  /** Mute or unmute the microphone without ending the call (the new design's Mute button). */
  setMuted(muted: boolean) {
    this.stream?.getAudioTracks().forEach((t) => { t.enabled = !muted; });
  }

  /** The owner started talking: stop the assistant mid-sentence, like a real call. */
  private stopPlayback() {
    this.playing.forEach((n) => { try { n.stop(); } catch { /* already stopped */ } });
    this.playing.clear();
    this.levelTimers.forEach((t) => { clearTimeout(t); });
    this.levelTimers.clear();
    this.nextStart = 0;
    this.onEvent({ type: "speaking", speaking: false });
  }

  private cleanup() {
    this.stopPlayback();
    this.stream?.getTracks().forEach((t) => { t.stop(); });
    this.micCtx?.close().catch(() => {});
    this.speakerCtx?.close().catch(() => {});
    this.stream = null;
    this.micCtx = null;
    this.speakerCtx = null;
    this.speakerOut = null;
    this.analyser = null;
  }
}

/** Root-mean-square loudness of 16-bit PCM, scaled to 0..1 (speech sits around 0.2 to 0.6). */
function loudness(pcm: Int16Array): number {
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += (pcm[i] / 0x8000) ** 2;
  return Math.min(1, Math.sqrt(sum / Math.max(1, pcm.length)) * 4);
}
