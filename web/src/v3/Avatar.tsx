import { useEffect, useRef, type MutableRefObject } from "react";

// Saathi's face (v3 trial, option A): an SVG drawn in code, animated frame by frame from signals the
// call already has. No library, nothing sent anywhere:
//   - the mouth follows Saathi's own voice as it plays, read every frame from the call's analyser:
//     how loud opens it, and how bright the sound is shapes it (round for "oh", wide for "ee", "s");
//   - while the owner speaks, the face nods a little and the gold rings swell with their voice;
//   - the expression (smile, eyes, brows, cheeks) eases toward the tone of the plan's current step;
//   - it blinks and breathes at rest, and glances up while it thinks.

export type AvatarState = "idle" | "connecting" | "listening" | "thinking" | "speaking";
export type Tone = "warm" | "gentle" | "light" | "clear" | "reassuring" | "patient";
export type Levels = { saathi: number; you: number };

type Face = { smile: number; eye: number; browIn: number; browOut: number; blush: number; speed: number };
// Smile: how much the mouth curves up; eye: how open; brows: inner and outer ends (negative = up).
const TONES: Record<Tone, Face> = {
  warm: { smile: 10, eye: 1, browIn: 0, browOut: 0, blush: 0.35, speed: 1 },
  gentle: { smile: 5, eye: 0.82, browIn: -6, browOut: 2, blush: 0.25, speed: 0.7 },
  light: { smile: 16, eye: 0.72, browIn: -3, browOut: -3, blush: 0.55, speed: 1.2 },
  clear: { smile: 6, eye: 1, browIn: 0, browOut: 0, blush: 0.2, speed: 1 },
  reassuring: { smile: 8, eye: 0.9, browIn: -2, browOut: 1, blush: 0.3, speed: 0.8 },
  patient: { smile: 7, eye: 0.88, browIn: -3, browOut: 1, blush: 0.3, speed: 0.6 },
};

const C = 160; // centre of the drawing
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** How loud (0..1) and how bright (0 dark "oo" .. 1 bright "ee", "s") Saathi's voice is right now. */
function readVoice(analyser: AnalyserNode, wave: Float32Array<ArrayBuffer>, bins: Uint8Array<ArrayBuffer>): [number, number] {
  analyser.getFloatTimeDomainData(wave);
  let sum = 0;
  for (const v of wave) sum += v * v;
  const loud = Math.min(1, Math.sqrt(sum / wave.length) * 5);
  analyser.getByteFrequencyData(bins);
  let weight = 0, total = 0;
  for (let i = 2; i < bins.length; i++) { weight += i * bins[i]; total += bins[i]; }
  const centre = total ? weight / total / bins.length : 0;  // the spectral centroid, as a share of the range
  return [loud, Math.min(1, Math.max(0, (centre - 0.08) / 0.22))];
}

export default function Avatar({ state, tone, levels, voice }: {
  state: AvatarState; tone: Tone; levels: MutableRefObject<Levels>; voice?: MutableRefObject<AnalyserNode | null>;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const props = useRef({ state, tone });
  props.current = { state, tone };

  useEffect(() => {
    const root = svg.current;
    if (!root) return;
    const get = (id: string) => root.querySelector(`[data-part="${id}"]`) as SVGElement;
    const head = get("head"), mouth = get("mouth"), browL = get("brow-l"), browR = get("brow-r");
    const eyeL = get("eye-l"), eyeR = get("eye-r"), cheekL = get("cheek-l"), cheekR = get("cheek-r");
    const rings = [get("ring-1"), get("ring-2"), get("ring-3")];
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const face: Face = { ...TONES[props.current.tone] };
    let mouthOpen = 0, mouthWide = 1, ringSwell = 0, gaze = 0, nextBlink = performance.now() + 2500, blinkUntil = 0, frame = 0;
    let wave = new Float32Array(1024), bins = new Uint8Array(512);

    const draw = (now: number) => {
      const { state: s, tone: t } = props.current;
      const target = TONES[t];
      for (const k of Object.keys(face) as (keyof Face)[]) face[k] = lerp(face[k], target[k], 0.06);
      const time = now / 1000 * face.speed;

      // The mouth follows Saathi's voice as it plays: the analyser each frame when there is one (a
      // call), otherwise the reported loudness with a little flutter (the preview).
      let loud = 0, bright = 0.4;
      const analyser = voice?.current;
      if (s === "speaking" && analyser) {
        if (wave.length !== analyser.fftSize) { wave = new Float32Array(analyser.fftSize); bins = new Uint8Array(analyser.frequencyBinCount); }
        [loud, bright] = readVoice(analyser, wave, bins);
      } else if (s === "speaking") {
        loud = Math.min(1, levels.current.saathi * 2.2) * (0.7 + 0.3 * Math.sin(now / 55));
      }
      const open = loud < 0.04 ? 0 : loud;  // silence between words closes the mouth
      mouthOpen = lerp(mouthOpen, open * 20 * (1.1 - bright * 0.5), 0.5);  // "oh" opens taller than "ee"
      mouthWide = lerp(mouthWide, open ? 0.82 + bright * 0.36 : 1, 0.35);   // "ee" and "s" spread wider
      const y0 = 205, hw = 32 * mouthWide, smile = face.smile * (1 - Math.min(1, mouthOpen / 30));
      mouth.setAttribute("d", `M${C - hw},${y0} Q${C},${y0 + smile} ${C + hw},${y0} Q${C},${y0 + smile + 4 + mouthOpen} ${C - hw},${y0} Z`);

      // Eyes: open by tone, a blink every few seconds, a glance up while thinking.
      if (now > nextBlink) { blinkUntil = now + 130; nextBlink = now + 2500 + Math.random() * 3500; }
      const blink = now < blinkUntil ? 0.08 : 1;
      gaze = lerp(gaze, s === "thinking" ? 1 : 0, 0.08);
      for (const [eye, x] of [[eyeL, 125], [eyeR, 195]] as const) {
        eye.setAttribute("cx", String(x - gaze * 4));
        eye.setAttribute("cy", String(152 - gaze * 6));
        eye.setAttribute("ry", String(13 * face.eye * blink));
      }
      browL.setAttribute("d", `M108,${126 + face.browOut - gaze * 3} Q125,${118 - gaze * 3} 140,${126 + face.browIn - gaze * 3}`);
      browR.setAttribute("d", `M180,${126 + face.browIn - gaze * 3} Q195,${118 - gaze * 3} 212,${126 + face.browOut - gaze * 3}`);
      for (const cheek of [cheekL, cheekR]) cheek.setAttribute("opacity", face.blush.toFixed(2));

      // The head breathes at rest, nods a little while listening, and bobs gently while speaking.
      const you = s === "listening" ? Math.min(1, levels.current.you * 2.5) : 0;
      const breathe = still ? 0 : Math.sin(time * 1.6) * 1.2;
      const nod = still ? 0 : (s === "listening" ? Math.sin(time * 3) * 2 * (0.3 + you) : 0) + (s === "speaking" ? Math.sin(time * 4) * 1.2 : 0);
      head.setAttribute("transform", `translate(0 ${breathe.toFixed(2)}) rotate(${nod.toFixed(2)} ${C} ${C})`);

      // Gold rings: swell with the owner's voice, turn while connecting, rest otherwise.
      ringSwell = lerp(ringSwell, you, 0.25);
      rings.forEach((ring, i) => {
        const r = 112 + i * 14 + (still ? 0 : ringSwell * (6 + i * 5));
        ring.setAttribute("r", r.toFixed(1));
        ring.setAttribute("opacity", (s === "idle" ? 0.25 : 0.35 + ringSwell * 0.4 - i * 0.08).toFixed(2));
        ring.setAttribute("transform", s === "connecting" && !still ? `rotate(${(now / (12 + i * 6)) % 360} ${C} ${C})` : "");
      });
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [levels]);

  return (
    <svg ref={svg} className="v3-avatar" viewBox="0 0 320 320" aria-hidden="true" focusable="false">
      {[1, 2, 3].map((i) => (
        <circle key={i} data-part={`ring-${i}`} cx={C} cy={C} r={112 + (i - 1) * 14} className="v3-ring"
          strokeDasharray={i === 2 ? "6 10" : undefined} />
      ))}
      <g data-part="head">
        <circle cx={C} cy={C} r={100} className="v3-face" />
        <circle data-part="cheek-l" cx={112} cy={188} r={15} className="v3-cheek" />
        <circle data-part="cheek-r" cx={208} cy={188} r={15} className="v3-cheek" />
        <path data-part="brow-l" className="v3-brow" d="M108,126 Q125,118 140,126" />
        <path data-part="brow-r" className="v3-brow" d="M180,126 Q195,118 212,126" />
        <ellipse data-part="eye-l" cx={125} cy={152} rx={9} ry={13} className="v3-eye" />
        <ellipse data-part="eye-r" cx={195} cy={152} rx={9} ry={13} className="v3-eye" />
        <path data-part="mouth" className="v3-mouth" d="M128,205 Q160,215 192,205 Q160,219 128,205 Z" />
      </g>
    </svg>
  );
}
