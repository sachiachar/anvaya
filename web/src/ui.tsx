import { createContext, useContext, useState, type ReactNode } from "react";
import type { Autonomy } from "./api";

const AUTONOMY_TEXT: Record<Autonomy, { label: string; hint: string }> = {
  act_within_limits: { label: "AI acts within limits", hint: "The AI does this on its own, inside fixed rules." },
  propose: { label: "AI proposes · you decide", hint: "Nothing happens until a person approves it." },
  assist: { label: "AI assists · a person decides", hint: "The AI prepares information; a person makes the call." },
};

export function AutonomyBadge({ level }: { level: Autonomy }) {
  const t = AUTONOMY_TEXT[level];
  return (
    <span className={`badge badge-${level}`} title={t.hint}>
      {t.label}
    </span>
  );
}

/** The autonomy labels explain the AI-first design (for demos); hidden for everyday use. */
export const ShowAutonomy = createContext(false);

export function Card({ title, level, children }: { title: string; level?: Autonomy; children: ReactNode }) {
  const show = useContext(ShowAutonomy);
  return (
    <section className="card">
      <header className="card-head">
        <h2>{title}</h2>
        {show && level && <AutonomyBadge level={level} />}
      </header>
      {children}
    </section>
  );
}

/** Runs an async action with a busy flag and an inline error message. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

export function ErrorLine({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function FilePick({ label, accept, capture, onFile, disabled }: {
  label: string;
  accept: string;
  capture?: "environment" | "user";
  onFile: (f: File) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`button secondary${disabled ? " disabled" : ""}`}>
      {label}
      <input
        type="file"
        accept={accept}
        capture={capture}
        disabled={disabled}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </label>
  );
}
