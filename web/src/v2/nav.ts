import { createContext } from "react";
import type { DemoPerson, PersonId } from "../api";

export type OwnerTab = "talk" | "kept" | "family" | "vault";

/** Who is acting in the demo, and a way to go to someone else's screen (PR-14.2): wherever the next
 *  step belongs to another person, the screen offers to switch to them, so the demo never depends
 *  on the Demo menu. The server still checks the person on every request (PR-1.3). */
export const Nav = createContext<{
  people: DemoPerson[];
  acting: PersonId;
  /** Show the app as this person; for the owner, optionally on this tab. */
  switchTo: (person: PersonId, tab?: OwnerTab) => void;
}>({ people: [], acting: "vikram", switchTo: () => {} });
