import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AuthError, api, setActingAs, type Case, type DemoPerson, type GuardianHeld, type PersonId } from "../api";
import { authEnabled, signInWithGoogle, signOutUser, watchUser } from "../auth";
import { useAction } from "../ui";
import { ROLE_TEXT, friendlyError } from "./copy";
import { ShowHow } from "./demo";
import { ADMIN_URL, KNOWLEDGE_MAP_URL, Nav, type OwnerTab, personLabel, personRole } from "./nav";
import Tour, { forgetTour, reopenTour, tourWanted } from "./Tour";
import { Book as BookIcon, Check, Chevron, Close, Lock, Mic, People, ThreadMark } from "./icons";
import KnowledgeMap from "./KnowledgeMap";
import { ModelKey } from "./ModelKey";
import Book from "./owner/Book";
import FamilyTab from "./owner/FamilyTab";
import Talk from "./owner/Talk";
import VaultTab from "./owner/VaultTab";
import Journey from "./family/Journey";
import Team from "./team/Team";
import { forgetOwnerKeyOnThisDevice, forgetRecipientKeyOnThisDevice, hasOwnerDataKey, hasRecipientKeys } from "../vaultCrypto";

const SHOW_HOW_KEY = "anvaya.v2.showHow";

const OWNER_TABS: { id: OwnerTab; label: string; icon: () => ReactNode }[] = [
  { id: "talk", label: "Talk", icon: Mic },
  { id: "kept", label: "Kept", icon: BookIcon },
  { id: "family", label: "Family", icon: People },
  { id: "vault", label: "Vault", icon: Lock },
];

export default function App() {
  const [people, setPeople] = useState<DemoPerson[]>([]);
  const [askNickname, setAskNickname] = useState(false);
  const [acting, setActing] = useState<PersonId>("vikram");
  const person = people.find((p) => p.id === acting);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [tour, setTour] = useState(false); // "Start here" (PR-14.1), once the copy is known
  const [tourVisit, setTourVisit] = useState(0); // reopening starts it fresh, from step 1
  useEffect(() => { if (ready) setTour(tourWanted()); }, [ready]);
  const [tab, setTab] = useState<OwnerTab>("talk");
  const [talkTopic, setTalkTopic] = useState<string | null>(null); // "Talk about this" from the book
  const [topicVisit, setTopicVisit] = useState(0); // a fresh Talk for each topic picked; clearing it doesn't restart Talk
  const owner: OwnerNav = {
    tab, go: (t) => { setTab(t); setTalkTopic(null); window.scrollTo({ top: 0 }); },
    talkAbout: (q) => { setTalkTopic(q); setTopicVisit((v) => v + 1); setTab("talk"); window.scrollTo({ top: 0 }); },
    topic: talkTopic, visit: topicVisit, clearTopic: () => setTalkTopic(null),
  };
  const [showHow, setShowHow] = useState(() => {
    try { return localStorage.getItem(SHOW_HOW_KEY) === "1"; } catch { return false; }
  });
  const toggleShowHow = () => setShowHow((v) => {
    try { localStorage.setItem(SHOW_HOW_KEY, v ? "0" : "1"); } catch { /* not saved */ }
    return !v;
  });
  const switchTo = useCallback((id: PersonId, ownerTab?: OwnerTab) => {
    setActingAs(id); setActing(id); setDrawer(false); setTalkTopic(null);
    if (ownerTab) setTab(ownerTab);
    window.scrollTo({ top: 0 });
  }, []);
  const nav = useMemo(() => ({ people, acting, switchTo }), [people, acting, switchTo]);

  // Sign-in: "checking" until Firebase answers, then "signed-out", "denied" or "in".
  const [session, setSession] = useState<"checking" | "signed-out" | "denied" | "in">(authEnabled ? "checking" : "in");
  const [email, setEmail] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);

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
      .then((m) => { setPeople(m.people); setAskNickname(m.ask_nickname); return api.owner().catch((e) => {
        // Set up the demo copy only when it doesn't exist yet, never because of a passing error:
        // setting it up again would wipe everything in it.
        if (/owner not found/.test(String(e?.message))) return api.seed();
        throw e;
      }); })
      .then(() => { setSession("in"); setReady(true); })
      .catch((e) => {
        if (e instanceof AuthError) { setDenied(e.message); setSession("denied"); }
        else setError(friendlyError(e));
      });
  }, [email, session === "checking"]);

  if (session === "signed-out" || session === "denied") {
    return <SignIn denied={session === "denied" ? denied : null} error={error} onError={setError} />;
  }

  // The demo's knowledge map opens in its own window (#knowledge-map), always as the owner.
  if (location.hash === "#knowledge-map") {
    return ready ? <KnowledgeMap /> : <p className="v2-muted v2-map-wait">{error ?? "Loading…"}</p>;
  }

  return (
    <ShowHow.Provider value={showHow}>
    <Nav.Provider value={nav}>
      <div className="v2-app">
        <header className="v2-top">
          <a className="v2-brand" href="/" aria-label="Anvaya, home">
            <ThreadMark />
            <b>Anvaya</b>
          </a>
          <button className="v2-demo-pill" onClick={() => setDrawer(true)} aria-haspopup="dialog">
            <i aria-hidden="true" />
            Demo
          </button>
          {ready && people.length > 0 && <PeopleRow people={people} acting={acting} onSwitch={switchTo} />}
        </header>

        <main className="v2-main">
          {error && <p className="v2-error" role="alert">{error}</p>}
          {!ready && !error && <p className="v2-muted">Opening your demo…</p>}
          {ready && tour && <Tour key={tourVisit} onClose={() => setTour(false)} />}
          {ready && person && <div key={person.id} className="v2-stack">{screen(person, owner)}</div>}
        </main>

        {ready && person?.role === "owner" && (
          <nav className="v2-nav" aria-label="Main">
            <div className="v2-nav-inner">
              {OWNER_TABS.map((t) => (
                <button key={t.id} aria-current={tab === t.id ? "page" : undefined} onClick={() => owner.go(t.id)}>
                  {t.icon()}
                  {t.label}
                </button>
              ))}
            </div>
          </nav>
        )}

        {drawer && (
          <DemoDrawer people={people} acting={acting} onSwitch={switchTo} onClose={() => setDrawer(false)}
            showHow={showHow} onToggleShowHow={toggleShowHow} askNickname={askNickname}
            onNicknameDone={() => setAskNickname(false)} email={email}
            onShowTour={() => { reopenTour(); setTourVisit((v) => v + 1); setTour(true); setDrawer(false); }} />
        )}
      </div>
    </Nav.Provider>
    </ShowHow.Provider>
  );
}

type OwnerNav = { tab: OwnerTab; go: (t: OwnerTab) => void; talkAbout: (question: string) => void; topic: string | null;
  visit: number; clearTopic: () => void };

// Each person sees their own screens; the server checks who is acting on every request (PR-1.3).
function screen(person: DemoPerson, owner: OwnerNav) {
  switch (person.role) {
    case "owner":
      return (
        <>
          <AliveAlert />
          <GuardianNotice />
          <OwnerTabScreen owner={owner} />
        </>
      );
    case "family":
      return <Journey me={person.name.split(" ")[0]} />;
    case "reviewer":
      return <Team reviewer={person.name} />;
    case "platform_admin":
      return (
        <section className="v2-panel">
          <h2>Platform admin</h2>
          <p>The governance team works in a separate portal: spending, every agent and its limits, refused calls, budget requests, guardian interventions and the audit log.</p>
          <p className="v2-muted v2-small">It opens in a new tab. Sign in there with the same Google account.</p>
          <a className="v2-btn" href={ADMIN_URL} target="_blank" rel="noreferrer">Open the admin portal</a>
        </section>
      );
  }
}

function OwnerTabScreen({ owner }: { owner: OwnerNav }) {
  switch (owner.tab) {
    case "talk":
      return <Talk key={owner.visit} topic={owner.topic} onTopicUsed={owner.clearTopic} />;
    case "kept":
      return <Book onTalkAbout={owner.talkAbout} onReview={() => owner.go("talk")} />;
    case "family":
      return <FamilyTab onVault={() => owner.go("vault")} />;
    case "vault":
      return <VaultTab onFamily={() => owner.go("family")} />;
  }
}

/** Shown if someone reports the owner's death: one tap stops the release. */
function AliveAlert() {
  const [open, setOpen] = useState<Case | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => {
    const check = () =>
      api.releaseStatus().then((r) => setOpen(r.case && r.case.state !== "released" ? r.case : null)).catch(() => {});
    check();
    const t = setInterval(check, 5000);
    return () => clearInterval(t);
  }, []);
  if (!open) return null;
  return (
    <section className="v2-alert" role="alert">
      <h2>Someone has told us that you have passed away.</h2>
      <p>If you are well, tap below. Your vault stays locked, nothing is handed over, and the report is cancelled.</p>
      <button className="v2-btn wide" disabled={busy} onClick={() => run(async () => { await api.ownerAlive(); setOpen(null); })}>
        I'm well, cancel this
      </button>
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}

/** What the guardian is holding for our team to check, or stopped this week (PR-2.7). */
function GuardianNotice() {
  const [held, setHeld] = useState<GuardianHeld[]>([]);
  useEffect(() => {
    const check = () => api.guardianHeld().then((r) => setHeld(r.held)).catch(() => {});
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, []);
  if (!held.length) return null;
  return (
    <section className="v2-alert" role="status">
      {held.map((h) => <p key={h.id}>{h.message}</p>)}
    </section>
  );
}

/** Everyone in the demo copy, always in view (PR-14.2): the judge switches person here, without the
 *  Demo menu. Hidden during a live call, which a switch would end (CSS). */
function PeopleRow({ people, acting, onSwitch }: { people: DemoPerson[]; acting: PersonId; onSwitch: (id: PersonId) => void }) {
  return (
    <nav className="v2-people" aria-label="Show the app as">
      <span className="v2-people-label" aria-hidden="true">Show as</span>
      {people.map((p) => (
        <button key={p.id} className="v2-person" aria-pressed={p.id === acting} onClick={() => onSwitch(p.id)}
          title={personRole(p)}>
          {p.id === "admin" ? "Admin" : p.name}
          <span className="v2-visually-hidden"> ({ROLE_TEXT[p.role]})</span>
        </button>
      ))}
    </nav>
  );
}

function SignIn({ denied, error, onError }: { denied: string | null; error: string | null; onError: (e: string) => void }) {
  return (
    <main className="v2-signin">
      <ThreadMark className="mark" />
      <h1>Anvaya</h1>
      <p className="tag">Nothing lost between generations.</p>
      {denied ? (
        <>
          <p className="v2-error" role="alert">{denied}</p>
          <button className="v2-btn gold" onClick={() => signOutUser()}>Sign in with a different account</button>
        </>
      ) : (
        <>
          <p className="note">This demo is open to invited people only. Sign in with the Google account you were invited with.</p>
          <button className="v2-btn gold" onClick={() => signInWithGoogle().catch((e) => onError(friendlyError(e)))}>Sign in with Google</button>
        </>
      )}
      {error && <p className="v2-error" role="alert">{error}</p>}
      <p className="note">Demo · synthetic data</p>
    </main>
  );
}

/** Everything that exists only for the demo, out of the customer's way: who is acting, reset,
 *  how Saathi works, the nickname and the knowledge map. */
function DemoDrawer({ people, acting, onSwitch, onClose, showHow, onToggleShowHow, askNickname, onNicknameDone, email, onShowTour }: {
  people: DemoPerson[]; acting: PersonId; onSwitch: (id: PersonId) => void; onClose: () => void;
  showHow: boolean; onToggleShowHow: () => void; askNickname: boolean; onNicknameDone: () => void; email: string | null;
  onShowTour: () => void;
}) {
  const [confirmReset, setConfirmReset] = useState(false);
  const [nickname, setNickname] = useState("");
  const { busy, error, run } = useAction();
  // A native modal dialog: it traps focus, closes on Escape and dims the page behind it. A click on
  // the dimmed page (the dialog itself, outside its body) closes it too.
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (!d.open) d.showModal();
    title.current?.focus(); // start on the title, not the close button
    const onBackdrop = (e: MouseEvent) => { if (e.target === d) d.close(); };
    d.addEventListener("click", onBackdrop);
    // No close() here: unmounting removes the dialog, and closing would fire onClose again.
    return () => d.removeEventListener("click", onBackdrop);
  }, []);
  const saveNickname = (value: string | null) =>
    run(async () => { await api.setNickname(value); onNicknameDone(); });

  const actingRole = people.find((p) => p.id === acting)?.role;
  // Only with his key kept in the cloud: otherwise forgetting it here would lose it for good.
  const [ownerKeyNote, setOwnerKeyNote] = useState<string | null>(null);
  const forgetOwnerKey = () => run(async () => {
    if (!(await api.ownerKeyBackup())) {
      setOwnerKeyNote("Make Vikram's recovery code on the Vault tab first. Without it, his key would be lost.");
      return;
    }
    forgetOwnerKeyOnThisDevice();
    onClose();
  });
  return (
    <dialog ref={dialog} className="v2-sheet" aria-labelledby="demo-title" onClose={onClose}>
      <header className="v2-sheet-top">
        <div className="v2-grab" aria-hidden="true" />
        <div className="v2-sheet-head">
          <div>
            <h2 id="demo-title" ref={title} tabIndex={-1}>Demo</h2>
            <p className="v2-sheet-sub">Synthetic data · this copy is yours alone</p>
          </div>
          <button className="v2-close" onClick={() => dialog.current?.close()} aria-label="Close"><Close /></button>
        </div>
      </header>

      <div className="v2-sheet-scroll">
        <section aria-labelledby="demo-people">
          <p id="demo-people" className="v2-group-label">Show the app as</p>
          <ul className="v2-group v2-who">
            {people.map((p) => {
              const on = p.id === acting;
              return (
                <li key={p.id}>
                  <button className="v2-row-btn" aria-pressed={on} onClick={() => onSwitch(p.id)}>
                    <span className={`v2-avatar${on ? " is-on" : ""}`} aria-hidden="true">{(p.id === "admin" ? "A" : p.name[0]).toUpperCase()}</span>
                    <span className="v2-row-text">
                      <b>{personLabel(p)}</b>
                      <span>{personRole(p)}</span>
                    </span>
                    {on && <span className="v2-row-tick"><Check /></span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        <section aria-labelledby="demo-options">
          <p id="demo-options" className="v2-group-label">Options</p>
          <div className="v2-group">
            <button className="v2-row-btn" onClick={onShowTour}>
              <span className="v2-row-text">
                <b>Show the tour</b>
                <span>Start here: the demo in five steps</span>
              </span>
              <span className="v2-row-chev" aria-hidden="true"><Chevron /></span>
            </button>
            <label className="v2-row-btn v2-toggle-row">
              <span className="v2-row-text">
                <b>Show how Saathi works</b>
                <span>How much each helper may do on its own</span>
              </span>
              <input type="checkbox" role="switch" aria-checked={showHow} className="v2-toggle" checked={showHow} onChange={onToggleShowHow} />
            </label>
            <a className="v2-row-btn" href={KNOWLEDGE_MAP_URL} target="_blank" rel="noopener">
              <span className="v2-row-text">
                <b>Open the knowledge map</b>
                <span>In a new window, filling in as things are kept</span>
              </span>
              <span className="v2-row-chev" aria-hidden="true"><Chevron /></span>
            </a>
            {actingRole === "family" && hasRecipientKeys() && (
              <button className="v2-row-btn" onClick={() => { forgetRecipientKeyOnThisDevice(); onClose(); }}>
                <span className="v2-row-text">
                  <b>Simulate a lost or new phone</b>
                  <span>Forgets Priya's key on this device</span>
                </span>
              </button>
            )}
            {actingRole === "owner" && hasOwnerDataKey() && (
              <button className="v2-row-btn" disabled={busy} onClick={forgetOwnerKey}>
                <span className="v2-row-text">
                  <b>Simulate a lost or new phone</b>
                  <span>{ownerKeyNote ?? "Forgets Vikram's key on this device"}</span>
                </span>
              </button>
            )}
            <ModelKey />
            {askNickname && (
              <details className="v2-disclosure">
                <summary className="v2-row-btn">
                  <span className="v2-row-text">
                    <b>A name for your demo copy</b>
                    <span>Optional · only the Anvaya team sees it</span>
                  </span>
                  <span className="v2-row-chev" aria-hidden="true"><Chevron /></span>
                </summary>
                <div className="v2-disclosure-body">
                  <p className="v2-muted v2-small">It doesn't need to be your real name. It tells demo copies apart.</p>
                  <input id="v2-nickname" className="v2-input" value={nickname} maxLength={30}
                    onChange={(e) => setNickname(e.target.value)} placeholder="A nickname" aria-label="Nickname" />
                  <div className="v2-row">
                    <button className="v2-btn" disabled={busy || !nickname.trim()} onClick={() => saveNickname(nickname.trim())}>Save</button>
                    <button className="v2-btn quiet" disabled={busy} onClick={() => saveNickname(null)}>Skip</button>
                  </div>
                </div>
              </details>
            )}
          </div>
        </section>

        <section aria-label="Your demo copy">
          <div className="v2-group">
            {confirmReset ? (
              <div className="v2-confirm v2-group-confirm" role="alert">
                <p>Restore your demo copy to the start? Everything you kept or changed in it is cleared. Nobody else's copy is affected.</p>
                <div className="v2-row">
                  <button className="v2-btn danger" disabled={busy} onClick={() => run(async () => { await api.seed(); forgetOwnerKeyOnThisDevice(); forgetRecipientKeyOnThisDevice(); forgetTour(); location.reload(); })}>Reset my demo</button>
                  <button className="v2-btn quiet" onClick={() => setConfirmReset(false)}>Keep it</button>
                </div>
              </div>
            ) : (
              <button className="v2-row-btn is-danger" onClick={() => setConfirmReset(true)}>
                <span className="v2-row-text"><b>Reset my demo</b><span>Start again from the beginning</span></span>
              </button>
            )}
            {authEnabled && email && (
              <button className="v2-row-btn" onClick={() => signOutUser()}>
                <span className="v2-row-text"><b>Sign out</b><span>{email}</span></span>
              </button>
            )}
          </div>
        </section>
        {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
      </div>
    </dialog>
  );
}
