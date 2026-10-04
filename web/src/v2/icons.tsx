// Line icons for v2, drawn to one grid (24 × 24, 1.8 stroke, round ends) so they look the same on
// every phone, unlike emoji. They take the text colour. Decorative: the button or label names them.
import type { ReactNode } from "react";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export const Mic = () => <Icon><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Icon>;
export const Camera = () => <Icon><path d="M4 8h3l2-3h6l2 3h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></Icon>;
export const Doc = () => <Icon><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></Icon>;
export const Speaker = () => <Icon><path d="M4 10v4h4l5 4V6L8 10z" /><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11" /></Icon>;
export const SpeakerOff = () => <Icon><path d="M4 10v4h4l5 4V6L8 10z" /><path d="M17 10l4 4M21 10l-4 4" /></Icon>;
export const Lock = () => <Icon><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></Icon>;
export const Book = () => <Icon><path d="M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z" /><path d="M4 20a2 2 0 0 0 2 1h13v-3M8 7h7" /></Icon>;
export const People = () => <Icon><circle cx="9" cy="8" r="3.2" /><path d="M3 20a6 6 0 0 1 12 0" /><circle cx="17" cy="9" r="2.6" /><path d="M16 14.2a5 5 0 0 1 5 5.8" /></Icon>;
export const Home = () => <Icon><path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" /></Icon>;
export const Check = () => <Icon><path d="M5 12.5l4.5 4.5L19 7.5" /></Icon>;
export const Close = () => <Icon><path d="M6 6l12 12M18 6L6 18" /></Icon>;
export const Chevron = () => <Icon><path d="M9 6l6 6-6 6" /></Icon>;
export const Send = () => <Icon><path d="M4 12l16-8-6 16-2.5-6.5z" /></Icon>;
export const Shield = () => <Icon><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" /><path d="M9 12l2 2 4-4" /></Icon>;

/** The unbroken thread (docs/brand.md): a heartbeat that rises into the "A" and loops on. */
export function ThreadMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 120 80" fill="none" stroke="#D4AF37" strokeWidth={4.2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M4 50h14l5-11 6 22 6-30 5 19c10 0 14-12 19-26l5-13c2-5 7-5 9 0l14 42c4 11 20 12 25 2 5-11-6-20-17-14-14 8-34 22-54 20" />
    </svg>
  );
}
