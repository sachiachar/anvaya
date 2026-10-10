import { useEffect, useState } from "react";
import { api, AuthError } from "../api";
import { authEnabled, signInWithGoogle, watchUser } from "../auth";
import Talk from "./Talk";

type Session = "checking" | "signed-out" | "denied" | "in";

/** Sign-in and the demo copy, as in the app, then the avatar trial, always as the owner. */
export default function App() {
  const [session, setSession] = useState<Session>(authEnabled ? "checking" : "in");
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");

  useEffect(() => watchUser((user) => {
    if (!authEnabled) return;
    setEmail(user?.email ?? null);
    setSession(user ? "checking" : "signed-out");
    setReady(false);
  }), []);

  useEffect(() => {
    if (session !== "checking" && session !== "in") return;
    if (authEnabled && !email) return;
    api.me()
      .then(() => api.owner().catch((e) => {
        // Set up the demo copy only when it doesn't exist yet, never because of a passing error.
        if (/owner not found/.test(String(e?.message))) return api.seed().then(() => api.owner());
        throw e;
      }))
      .then((o) => { setName(String(o.name ?? "").split(" ")[0]); setSession("in"); setReady(true); })
      .catch((e) => {
        if (e instanceof AuthError) setSession("denied");
        setError(String(e?.message ?? e));
      });
  }, [email, session === "checking"]);

  if (session === "signed-out" || session === "denied") {
    return (
      <main className="v3 v3-center">
        <p className="v3-eyebrow">Saathi · avatar trial</p>
        <h1>Talk with Saathi</h1>
        {session === "denied" && <p className="v3-error" role="alert">{error}</p>}
        <button type="button" className="v3-btn" onClick={() => signInWithGoogle().catch((e) => setError(String(e)))}>Sign in with Google</button>
      </main>
    );
  }
  if (!ready) {
    return <main className="v3 v3-center"><p className="v3-muted">{error ?? "Getting Saathi ready…"}</p></main>;
  }
  return <Talk name={name} />;
}
