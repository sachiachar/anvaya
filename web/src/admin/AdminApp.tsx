import { useEffect, useState } from "react";
import { AuthError } from "../api";
import { authEnabled, signInWithGoogle, signOutUser, watchUser } from "../auth";
import { fmt, platform, type Me } from "./api";
import { AgentsPage, ApprovalsPage, AuditPage, DemosPage, FraudPage, GuardianPage, ModelKeysPage, OverviewPage } from "./pages";

const PAGES = {
  overview: { label: "Overview", render: (me: Me, fresh: boolean) => <OverviewPage me={me} fresh={fresh} /> },
  agents: { label: "Agents", render: () => <AgentsPage /> },
  approvals: { label: "Approvals", render: (me: Me) => <ApprovalsPage me={me} /> },
  guardian: { label: "Guardian", render: (me: Me) => <GuardianPage me={me} /> },
  fraud: { label: "Fraud", render: (me: Me) => <FraudPage me={me} /> },
  demos: { label: "Demos", render: (me: Me) => <DemosPage me={me} /> },
  keys: { label: "Model keys", render: (me: Me) => <ModelKeysPage me={me} /> },
  audit: { label: "Audit log", render: () => <AuditPage /> },
} as const;

type Page = keyof typeof PAGES;

// "#/guardian?ev.q=voice": the page, then its lists' search, filters and page (listControls.tsx).
const pageFromHash = (): Page => {
  const h = location.hash.replace("#/", "").split("?")[0] as Page;
  return h in PAGES ? h : "overview";
};

export default function AdminApp() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [session, setSession] = useState<"checking" | "signed-out" | "denied" | "in">(authEnabled ? "checking" : "in");
  const [email, setEmail] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Refresh: remount the page so it loads again; the overview also asks for fresh billing data.
  const [refreshed, setRefreshed] = useState(0);
  const [freshFor, setFreshFor] = useState<Page | null>(null);
  const [loadedAt, setLoadedAt] = useState(() => new Date());
  const refresh = () => { setRefreshed((n) => n + 1); setFreshFor(page); setLoadedAt(new Date()); };

  useEffect(() => {
    const onHash = () => { setPage(pageFromHash()); setFreshFor(null); setLoadedAt(new Date()); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => watchUser((user) => {
    if (!authEnabled) return;
    setEmail(user?.email ?? null);
    setSession(user ? "checking" : "signed-out");
  }), []);

  useEffect(() => {
    if (session !== "checking" && session !== "in") return;
    if (authEnabled && !email) return;
    platform.me()
      .then((m) => { setMe(m); setSession("in"); })
      .catch((e) => {
        setMessage(e instanceof AuthError ? e.message : String(e));
        setSession("denied");
      });
  }, [email, session === "checking"]);

  if (session !== "in" || !me) {
    return (
      <div className="pa-signin">
        <img src="/icon-192.png" alt="" width={72} height={72} />
        <h1>Anvaya platform admin</h1>
        <p>For the Anvaya governance team. Roles are checked on the server, and every action is logged.</p>
        {session === "checking" && <p className="pa-muted">Checking your access…</p>}
        {session === "signed-out" && (
          <button onClick={() => signInWithGoogle().catch((e) => setMessage(String(e)))}>Sign in with Google</button>
        )}
        {session === "denied" && (
          <>
            <p className="pa-error" role="alert">{message}</p>
            {authEnabled && <button onClick={() => signOutUser()}>Sign in with a different account</button>}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="pa">
      <aside className="pa-side">
        <div className="pa-brand">
          <img src="/icon-192.png" alt="" width={36} height={36} />
          <div>
            <strong>Anvaya</strong>
            <span>Platform admin</span>
          </div>
        </div>
        <nav aria-label="Portal sections">
          {(Object.keys(PAGES) as Page[]).map((p) => (
            <a key={p} href={`#/${p}`} className={p === page ? "active" : ""} aria-current={p === page ? "page" : undefined}>
              {PAGES[p].label}
            </a>
          ))}
        </nav>
        <div className="pa-who">
          <span className="pa-role">{fmt.label(me.role)}</span>
          <span className="pa-muted" title={me.email ?? undefined}>{me.email ?? "Local development"}</span>
          {authEnabled && <button className="pa-link" onClick={() => signOutUser()}>Sign out</button>}
        </div>
      </aside>
      <main className="pa-main">
        <div className="pa-head">
          <h1>{PAGES[page].label}</h1>
          <div className="pa-refresh">
            <span className="pa-muted">Loaded {loadedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
            <button onClick={refresh}>Refresh</button>
          </div>
        </div>
        <div key={`${page}:${refreshed}`}>{PAGES[page].render(me, freshFor === page)}</div>
      </main>
    </div>
  );
}
