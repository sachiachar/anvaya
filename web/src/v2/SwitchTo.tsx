import { type ReactNode, useContext } from "react";
import type { DemoPerson, PersonId } from "../api";
import { Chevron } from "./icons";
import { Nav, type OwnerTab } from "./nav";

/** "Switch to Priya": goes to the person whose turn it is, named by id or, for the owner and the
 *  family, by role. Hidden when they're already acting, or aren't in this copy. */
export default function SwitchTo({ to, as, tab, children }: {
  to?: PersonId; as?: DemoPerson["role"]; tab?: OwnerTab; children?: ReactNode;
}) {
  const nav = useContext(Nav);
  const person = nav.people.find((p) => (to ? p.id === to : p.role === as));
  if (!person || nav.acting === person.id) return null;
  return (
    <button className="v2-switch" onClick={() => nav.switchTo(person.id, tab)}>
      <span>{children ?? `Switch to ${person.name}`}</span>
      <Chevron />
    </button>
  );
}
