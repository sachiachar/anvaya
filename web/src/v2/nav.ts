import { createContext } from "react";
import type { DemoPerson, PersonId } from "../api";
import { ROLE_TEXT } from "./copy";

// The platform admin opens the separate admin portal (PR-12.9), as does the tour's last step.
export const ADMIN_URL: string = import.meta.env.VITE_ADMIN_URL ?? (import.meta.env.DEV ? "/admin.html" : "https://anvaya-admin.web.app");

export type OwnerTab = "talk" | "kept" | "family" | "vault";

/** Who is acting in the demo, and a way to go to someone else's screen (PR-14.2): wherever the next
 *  step belongs to another person, the screen offers to switch to them, so the demo never depends
 *  on the Demo menu. The server still checks the person on every request (PR-1.3). */
export const Nav = createContext<{
  people: DemoPerson[];
  acting: PersonId;
  /** Show the app as this person; for the owner, optionally on this tab. */
  switchTo: (person: PersonId, tab?: OwnerTab) => void;
  /** Open the owner's Settings (the voice check), as the owner. */
  openSettings: () => void;
}>({ people: [], acting: "vikram", switchTo: () => {}, openSettings: () => {} });

/** The same person by name, as the server compares reviewers for the two-person rule (strip, casefold). */
export const samePerson = (a: string | null | undefined, b: string | null | undefined) =>
  (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();

/** The demo's knowledge map, in its own window (App.tsx), from the Demo menu and from Kept. */
export const KNOWLEDGE_MAP_URL = "/#knowledge-map";

/** How a person is named in the demo's lists, and what they do there. */
export const personLabel = (p: DemoPerson) => (p.role === "platform_admin" ? "Platform admin" : p.name);
export const personRole = (p: DemoPerson) => (p.role === "platform_admin" ? "Opens the admin portal" : ROLE_TEXT[p.role]);
