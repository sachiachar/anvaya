// One recording for the owner's voice check: a few seconds of the microphone as 16 kHz mono 16-bit
// PCM, the live call's own format (the same recorder worklet). It goes to the server, which keeps
// only the numbers that describe the voice, never the recording.
import { loudness, MicError, openMic } from "./liveCall";

const RATE = 16000;
const SPARE_SECONDS = 5; // a microphone that sends nothing for this long after the recording should end has stalled

/** Record `seconds` of the microphone. onLevel gets the loudness (0 to 1) about ten times a second.
 *  Aborting the signal stops it at once, with an AbortError. Throws MicError when the microphone
 *  can't be opened or sends nothing. */
export async function recordSample(seconds: number, onLevel?: (level: number) => void, signal?: AbortSignal): Promise<ArrayBuffer> {
  const stream = await openMic();
  const ctx = new AudioContext({ sampleRate: RATE });
  try {
    await ctx.resume(); // made after the microphone opened, so a browser may start it suspended
    await ctx.audioWorklet.addModule("/pcm-recorder.js");
    const recorder = new AudioWorkletNode(ctx, "pcm-recorder");
    const chunks: Int16Array[] = [];
    const wanted = seconds * RATE;
    await new Promise<void>((done, fail) => {
      let have = 0;
      const stalled = setTimeout(() => fail(new MicError("stalled", "The microphone stopped sending sound.")),
        (seconds + SPARE_SECONDS) * 1000);
      const stop = () => { clearTimeout(stalled); fail(new DOMException("Recording stopped", "AbortError")); };
      if (signal?.aborted) return stop();
      signal?.addEventListener("abort", stop, { once: true });
      recorder.port.onmessage = (e) => {
        const chunk = new Int16Array(e.data as ArrayBuffer);
        chunks.push(chunk);
        have += chunk.length;
        onLevel?.(loudness(chunk));
        if (have >= wanted) { clearTimeout(stalled); signal?.removeEventListener("abort", stop); done(); }
      };
      ctx.createMediaStreamSource(stream).connect(recorder);
    });
    const pcm = new Int16Array(chunks.reduce((n, c) => n + c.length, 0));
    let at = 0;
    for (const c of chunks) { pcm.set(c, at); at += c.length; }
    return pcm.buffer;
  } finally {
    stream.getTracks().forEach((t) => { t.stop(); });
    ctx.close().catch(() => {});
  }
}
