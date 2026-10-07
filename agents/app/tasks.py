"""Next steps from the readiness loop: kept on the owner's list until done or dismissed.

Each readiness run rewrites the open list. Code (not just the prompt) makes sure:
- near-duplicates in one run are merged ("Secure the cupboard keys" / "Plan for the keys"),
- a step the owner finished or dismissed never comes back, even reworded,
- at most 5 steps are open, most important first.
"""

from __future__ import annotations

import re
import time

from . import step_guard
from .store import new_id, store
from .tone import step_problems

MAX_OPEN = 5
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_STOP = set("""a an the and or of to for in on at your you is are be it this that with
    if may can will would should have has had do does not no their them they his her
    currently important make create set up ensure plan secure family needs need
    information details access update updating get""".split()) - {"will"}


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _names(title: str) -> set[str]:
    """Institutions, places and people in a title: 'LIC', 'SBI', 'Mysore'."""
    words = re.findall(r"[A-Za-z]+", title)
    return {w.lower() for i, w in enumerate(words) if (w.isupper() and len(w) > 1) or (i > 0 and w[0].isupper())}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _same(a: dict, b: dict) -> bool:
    """Same step if the titles share most key words, or the reasons are nearly the same,
    unless the titles name different things (LIC vs SBI)."""
    names_a, names_b = _names(a.get("title", "")), _names(b.get("title", ""))
    if names_a and names_b and not names_a & names_b:
        return False
    return (_overlap(_content(a.get("title", "")), _content(b.get("title", ""))) >= 0.5
            or _overlap(_content(a.get("why_it_matters", "")), _content(b.get("why_it_matters", ""))) >= 0.7)


def refresh(owner_id: str, gaps: list[dict]) -> None:
    all_tasks = store.list("tasks", owner_id=owner_id)
    closed = [t for t in all_tasks if t["status"] != "open"]
    open_now = [t for t in all_tasks if t["status"] == "open"]

    new_list: list[dict] = []
    for gap in sorted(gaps, key=lambda g: SEVERITY_ORDER.get(g["severity"], 3)):
        if any(_same(gap, c) for c in closed) or any(_same(gap, k) for k in new_list):
            continue
        new_list.append(gap)
    # Never pursue a step that could be part of a fraud (PR-13.3): it's dropped before it's stored.
    new_list = step_guard.pursue(owner_id, [step_guard.typed(g) for g in new_list])[:MAX_OPEN]

    # Keep ids stable for steps that are still there; drop the ones that no longer apply.
    for task in open_now:
        match = next((g for g in new_list if _same(g, task) and "_id" not in g), None)
        if match:
            store.update("tasks", task["id"], {k: v for k, v in match.items() if k != "_id"})
            match["_id"] = task["id"]
        else:
            store.delete("tasks", task["id"])
    for gap in new_list:
        if "_id" not in gap:
            store.put("tasks", {"id": new_id("task"), "owner_id": owner_id, "status": "open",
                                "created_at": time.time(), **gap})


def open_tasks(owner_id: str) -> list[dict]:
    # Steps saved before the tone check (or that slipped past it) never reach the owner.
    # A step stored before a tactic was approved is checked again on every read (PR-13.3).
    tactics = step_guard.approved_tactics()
    tasks = [t for t in store.list("tasks", owner_id=owner_id, status="open")
             if not step_problems(t) and step_guard.check(step_guard.typed(t), tactics) is None]
    return sorted(tasks, key=lambda t: (SEVERITY_ORDER.get(t["severity"], 3), t["created_at"]))
