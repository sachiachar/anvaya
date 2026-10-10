import { type ReactNode, useContext } from "react";
import type { PersonId } from "../api";
import { Chevron } from "./icons";
import { Nav, type OwnerTab } from "./nav";

/** "Switch to Priya": goes to the person whose turn it is. Hidden when they're already acting, or
 *  aren't in this copy. */
export default function SwitchTo({ to, tab, children }: { to: PersonId; tab?: OwnerTab; children?: ReactNode }) {
  const nav = useContext(Nav);
  const person = nav.people.find((p) => p.id === to);
  if (!person || nav.acting === to) return null;
  return (
    <button className="v2-switch" onClick={() => nav.switchTo(to, tab)}>
      <span>{children ?? `Switch to ${person.name}`}</span>
      <Chevron />
    </button>
  );
}
