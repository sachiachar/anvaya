"""One conversation's audit chain (PR-2.12.5): turn → guardian decision → card → Keep → plan version →
next step, followed by ids, for a risk reviewer on the portal's Guardian page.

Everything here is ids, kinds, responses, rule names and times: never what anyone said (rule 4).
The owner's turn is read only for its id, time, channel and decision ids; a plan step is shown by
its kind (a gap's kind, a safe route's kind), never by words that could name an account or person.
"""

from __future__ import annotations

from .store import store

RECENT = 20


def safe_step(step_id: str | None) -> str | None:
    """A plan step by its kind only: initial steps keep their fixed id; a gap shows its kind (not the
    account it's about); a safe-route or mood step its kind; a suggestion just that."""
    if not step_id:
        return None
    source, _, rest = step_id.partition(":")
    if source == "initial":
        return step_id
    if source == "gap":
        return f"gap:{rest.split(':')[0]}"
    if source in ("safe", "mood"):
        return f"{source}:{rest.split(':')[0]}"
    return "suggestion" if source == "task" else source


def _decision(a: dict) -> dict:
    return {"id": a["id"], "at": a["at"], "response": a["response"], "risk": a.get("risk"),
            "detectors": a.get("detectors", []), "event_id": a.get("event_id")}


def _cause(cause: dict, card_ids: set[str]) -> dict:
    out = {k: v for k, v in cause.items() if k in ("kind", "turn", "guardian", "mood", "outcome")}
    if "step" in cause:
        out["step"] = safe_step(cause["step"])
    if cause.get("ref") in card_ids:
        out["card"] = cause["ref"]
    elif cause.get("kind") == "outcome" and cause.get("ref"):
        out["step"] = safe_step(cause["ref"])
    return out


def for_turn(turn_id: str) -> dict | None:
    """The chain from one owner turn, or None if there's no such turn."""
    turn = store.get("turns", turn_id)
    if not turn or turn.get("role") != "owner":
        return None
    statements = sorted((a for a in store.list("guardian_actions", action="owner_statement")
                         if (a.get("refs") or {}).get("turn") == turn_id), key=lambda a: a["at"])
    replies = [t for t in store.list("turns", owner_id=turn["owner_id"], role="assistant") if t["at"] > turn["at"]]
    reply = min(replies, key=lambda t: t["at"]) if replies else None
    proposals = (reply or {}).get("proposals") or {}
    cards_in = [(collection, cid) for collection in ("moments", "inventory", "people") for cid in proposals.get(collection, [])]
    card_ids = {cid for _, cid in cards_in}
    keeps = {a["refs"]["card"]: a for a in store.list("guardian_actions", action="keep_card")
             if (a.get("refs") or {}).get("card") in card_ids}
    cards = []
    for collection, cid in cards_in:
        doc, keep = store.get(collection, cid), keeps.get(cid)
        status = ("waiting" if doc and doc.get("status") == "pending"
                  else "kept" if keep and keep["response"] in ("continue", "add_check") and keep["refs"].get("kept")
                  else "refused at Keep" if keep else "not kept")
        cards.append({"id": cid, "collection": collection, "status": status,
                      "statement": (doc or {}).get("guardian_action") or (keep or {}).get("refs", {}).get("statement"),
                      "keep": {**_decision(keep), "kept": keep["refs"].get("kept")} if keep else None})
    plans = sorted((v for v in store.list("plan_versions", owner_id=turn["owner_id"])
                    if (v.get("cause") or {}).get("turn") == turn_id or (v.get("cause") or {}).get("ref") in card_ids),
                   key=lambda v: v["version"])
    on_call = (turn.get("attachment") or {}).get("name") == "live call"
    return {
        "turn": {"id": turn_id, "at": turn["at"], "channel": "live call" if on_call else "app",
                 "held_back": turn.get("held_back", 0), "user_key": statements[0]["user_key"] if statements else None},
        "statements": [{**_decision(a), "card": (a.get("refs") or {}).get("card")} for a in statements],
        "cards": cards,
        "plans": [{"version": v["version"], "at": v["at"], "cause": _cause(v.get("cause") or {}, card_ids),
                   "current": safe_step(v.get("current")),
                   "steps": [{"step": safe_step(s["id"]), "opens": s.get("opens"), "tone": s.get("tone")}
                             for s in v.get("steps", [])]} for v in plans],
        "reply": {"id": reply["id"], "at": reply["at"], "step": safe_step(reply.get("step"))} if reply else None,
    }


def for_event(event_id: str) -> dict | None:
    """The chain from a guardian event about something the owner said."""
    action = next(iter(store.list("guardian_actions", event_id=event_id)), None)
    turn = ((action or {}).get("refs") or {}).get("turn")
    return for_turn(turn) if turn else None


def recent(user_key: str | None = None) -> list[dict]:
    """The latest owner turns the guardian decided on, newest first: when, whose (a hashed key), and
    how many decisions and how many held back. A demo admin sees only their own copy's."""
    turns: dict[str, dict] = {}
    for a in store.list("guardian_actions", action="owner_statement"):
        if user_key and a.get("user_key") != user_key:
            continue
        tid = (a.get("refs") or {}).get("turn")
        if not tid:
            continue
        row = turns.setdefault(tid, {"turn": tid, "at": a["at"], "user_key": a.get("user_key"), "decisions": 0, "held": 0})
        row["decisions"] += 1
        row["held"] += a["response"] in ("deflect", "block")
        row["at"] = max(row["at"], a["at"])
    return sorted(turns.values(), key=lambda r: -r["at"])[:RECENT]
