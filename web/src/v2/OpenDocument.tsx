// Open a kept photo or document: fetched with the person's sign-in from the owner's private storage,
// shown in a new tab from a temporary link that is released straight after. Nothing is cached.
import { useState } from "react";
import { api } from "../api";

export default function OpenDocument({ id, title }: { id: string; title: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = async () => {
    setBusy(true);
    setError(null);
    // Open the tab first, inside the tap, so the phone doesn't block it; then fill it.
    const tab = window.open("", "_blank");
    try {
      const url = URL.createObjectURL(await api.documentFile(id));
      if (tab) tab.location.href = url;
      else window.location.assign(url);
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      tab?.close();
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button className="v2-btn quiet v2-small" disabled={busy} onClick={open} aria-label={`Open “${title}”`}>
        {busy ? "Opening…" : "Open"}
      </button>
      {error && <p className="v2-error v2-small" role="alert">{error}</p>}
    </>
  );
}
