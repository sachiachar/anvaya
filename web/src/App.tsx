import { useEffect, useState } from "react";
import { AuthError, api } from "./api";
import { authEnabled, signInWithGoogle, signOutUser, watchUser } from "./auth";
import { ShowAutonomy } from "./ui";
import Family from "./Family";
import Owner from "./Owner";
import Team from "./Team";

const VIEWS = {
  owner: { label: "Vikram", sub: "Owner", render: () => <Owner /> },
  team: { label: "Our team", sub: "Review", render: () => <Team /> },
  family: { label: "Priya", sub: "Family", render: () => <Family /> },
} as const;

type View = keyof typeof VIEWS;

export default function App() {
  const [view, setView] = useState<View>("owner");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAi, setShowAi] = useState(() => {
    try { return localStorage.getItem("life-after.showAi") === "1"; } catch { return false; }
  });
  const toggleAi = () => setShowAi((v) => {
    try { localStorage.setItem("life-after.showAi", v ? "0" : "1"); } catch { /* not saved */ }
    return !v;
  });

  // Sign-in: "checking" until Firebase answers, then "signed-out", "denied" or "in".
  const [session, setSession] = useState<"checking" | "signed-out" | "denied" | "in">(authEnabled ? "checking" : "in");
  const [email, setEmail] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);

  useEffect(() => watchUser((user) => {
    if (!authEnabled) return;
    setEmail(user?.email ?? null);
    setSession(user ? "checking" : "signed-out");
  }), []);

  useEffect(() => {
    if (session !== "checking" && session !== "in") return;
    if (authEnabled && !email) return;
    api.me()
      .then(() => api.owner().catch(() => api.seed()))
      .then(() => { setSession("in"); setReady(true); })
      .catch((e) => {
        if (e instanceof AuthError) { setDenied(e.message); setSession("denied"); }
        else setError(String(e));
      });
  }, [email, session === "checking"]);

  if (session === "signed-out" || session === "denied") {
    return (
      <div className="app">
        <div className="signin">
          <img className="signin-logo" src="/logo.webp" alt="Life-After, powered by Saathi" width={265} height={271} />
          <p className="muted">Demo · synthetic data</p>
          {session === "denied" ? (
            <>
              <p className="error" role="alert">{denied}</p>
              <button onClick={() => signOutUser()}>Sign in with a different account</button>
            </>
          ) : (
            <>
              <p>This demo is open to invited people only. Sign in with the Google account you were invited with.</p>
              <button onClick={() => signInWithGoogle().catch((e) => setError(String(e)))}>Sign in with Google</button>
            </>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <img src="/icon-192.png" alt="" width={40} height={40} />
          <div>
            <h1>Life-After</h1>
            <p className="muted">Powered by Saathi · demo</p>
          </div>
        </div>
        <div className="top-links">
          <button className="link" onClick={toggleAi} aria-pressed={showAi}>{showAi ? "Hide AI labels" : "Show how the AI works"}</button>
          <button className="link" onClick={() => api.seed().then(() => location.reload())}>Reset demo</button>
          {authEnabled && email && (
            <button className="link" onClick={() => signOutUser()} title={email}>Sign out</button>
          )}
        </div>
      </header>
      <main>
        {error && <p className="error">Cannot reach the agents API: {error}</p>}
        <ShowAutonomy.Provider value={showAi}>{ready && VIEWS[view].render()}</ShowAutonomy.Provider>
      </main>
      <nav className="tabs" aria-label="Switch role">
        {(Object.keys(VIEWS) as View[]).map((v) => (
          <button key={v} className={v === view ? "active" : ""} aria-current={v === view ? "page" : undefined} onClick={() => setView(v)}>
            <strong>{VIEWS[v].label}</strong>
            <span>{VIEWS[v].sub}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
