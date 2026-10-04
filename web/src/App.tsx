import { useEffect, useState } from "react";
import { AuthError, api, setActingAs, type DemoPerson, type PersonId } from "./api";
import { authEnabled, signInWithGoogle, signOutUser, watchUser } from "./auth";
import { ShowAutonomy } from "./ui";
import Family from "./Family";
import KnowledgeMap from "./KnowledgeMap";
import Owner from "./Owner";
import Team from "./Team";

// The platform admin opens the separate admin portal (PR-12.9).
const ADMIN_URL = import.meta.env.VITE_ADMIN_URL ?? (import.meta.env.DEV ? "/admin.html" : "https://anvaya-admin.web.app");

const ROLE_TEXT: Record<DemoPerson["role"], string> = {
  owner: "Owner", family: "Family", reviewer: "Reviewer", platform_admin: "Platform admin",
};

// Each person sees their own screen; the server checks who is acting on every request (PR-1.3).
function render(person: DemoPerson) {
  switch (person.role) {
    case "owner": return <Owner />;
    case "family": return <Family />;
    case "reviewer": return <Team reviewer={person.name} />;
    case "platform_admin": return <AdminLink />;
  }
}

/** Asked once, after the first sign-in. Optional: skipping is an answer too. */
function NicknamePrompt({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const answer = (nickname: string | null) => {
    setBusy(true);
    api.setNickname(nickname).catch(() => undefined).finally(onDone);
  };
  return (
    <section className="card" aria-labelledby="nickname-title">
      <h2 id="nickname-title">What should we call you?</h2>
      <p className="muted">Optional, and it doesn't need to be your real name. Only the Anvaya team sees it, to tell demo copies apart.</p>
      <div className="row">
        <input value={value} onChange={(e) => setValue(e.target.value)} maxLength={30} placeholder="A nickname" aria-label="Nickname" />
        <button disabled={busy || !value.trim()} onClick={() => answer(value)}>Save</button>
        <button className="secondary" disabled={busy} onClick={() => answer(null)}>Skip</button>
      </div>
    </section>
  );
}

function AdminLink() {
  return (
    <section className="card">
      <h2>Platform admin</h2>
      <p>The governance team works in a separate portal: spend from Google Cloud billing, every agent and its limits, refused calls, budget requests, guardian interventions and the audit log.</p>
      <p>There you're the platform admin of your own demo: when Vikram asks for more allowance, approve or reject it under Approvals.</p>
      <p className="muted">It opens in a new tab. Sign in there with the same Google account.</p>
      <p><a className="button" href={ADMIN_URL} target="_blank" rel="noreferrer">Open the admin portal</a></p>
    </section>
  );
}

export default function App() {
  const [people, setPeople] = useState<DemoPerson[]>([]);
  const [askNickname, setAskNickname] = useState(false);
  const [acting, setActing] = useState<PersonId>("vikram");
  const switchTo = (id: PersonId) => { setActingAs(id); setActing(id); };
  const person = people.find((p) => p.id === acting);
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
    setReady(false); // a different account has a different demo copy: load its owner first
  }), []);

  useEffect(() => {
    if (session !== "checking" && session !== "in") return;
    if (authEnabled && !email) return;
    api.me()
      .then((m) => { setPeople(m.people); setAskNickname(m.ask_nickname); return api.owner().catch(() => api.seed()); })
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
          <img className="signin-logo" src="/logo.webp" alt="Anvaya" width={265} height={262} />
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

  // The demo's knowledge map opens in its own window, always as the owner.
  if (location.hash === "#knowledge-map") {
    return ready ? <KnowledgeMap /> : <p className="muted">{error ?? "Loading…"}</p>;
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <img src="/icon-192.png" alt="" width={40} height={40} />
          <div>
            <h1>Anvaya</h1>
            <p className="muted">Powered by Saathi · demo</p>
          </div>
        </div>
        <div className="top-links">
          <a href="/v2.html">Try the new design</a>
          <a href="/v2.html#model-key" title="Optional: use your own Gemini key for everything you do in this demo">Your own model key</a>
          <button className="link" onClick={toggleAi} aria-pressed={showAi}>{showAi ? "Hide AI labels" : "Show how the AI works"}</button>
          <button className="link" onClick={() => api.seed().then(() => location.reload())}
            title="Restores your own copy of the demo to the start. Nobody else's is affected.">Reset my demo</button>
          {authEnabled && email && (
            <button className="link" onClick={() => signOutUser()} title={email}>Sign out</button>
          )}
        </div>
      </header>
      {person && (
        <p className="acting" role="status">You are acting as <strong>{person.name}</strong> · {ROLE_TEXT[person.role]}</p>
      )}
      <main>
        {error && <p className="error">Cannot reach the agents API: {error}</p>}
        {ready && askNickname && <NicknamePrompt onDone={() => setAskNickname(false)} />}
        <ShowAutonomy.Provider value={showAi}>
          {ready && person && <div key={person.id}>{render(person)}</div>}
        </ShowAutonomy.Provider>
      </main>
      <nav className="tabs" aria-label="Switch person">
        {people.map((p) => (
          <button key={p.id} className={p.id === acting ? "active" : ""} aria-current={p.id === acting ? "page" : undefined}
            onClick={() => switchTo(p.id)}>
            <strong>{p.id === "admin" ? "Admin" : p.name}</strong>
            <span>{p.role === "platform_admin" ? "Portal" : ROLE_TEXT[p.role]}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
