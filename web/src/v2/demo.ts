import { createContext } from "react";

/** "Show how Saathi works" (Demo drawer): reveals the autonomy labels (ASSIST, PROPOSE, ACT on each
 *  card, next step and check, PR-14.6) for judges and demos. The readiness score shows for everyone
 *  once worked out (PR-14.10). Hidden in everyday use, where customers meet Saathi, not the machinery. */
export const ShowHow = createContext(false);
