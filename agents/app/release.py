"""Vault release state machine: the human boundaries, enforced in code.

    reported --(team confirms death)--> death_confirmed
             --(team confirms legal authority)--> waiting
             --(waiting period passes, owner silent)--> released
    Any state before `released` --(owner responds)--> cancelled

The AI can pre-check documents (Assist) but only a person moves the case forward,
two different people confirm the death and the legal authority, going against the
AI's "not valid" needs a written reason, and nothing is released before the
waiting period ends.
"""

from __future__ import annotations

import time
from typing import Any

from .config import settings
from .governance import core as governance

CHECKER = "activation.checker"  # the agent whose document checks a reviewer confirms or overrides
STATES = ("reported", "death_confirmed", "waiting", "released", "cancelled")


class ReleaseError(Exception):
    pass


def new_case(case_id: str, owner_id: str, certificate_check: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    return {
        "id": case_id,
        "owner_id": owner_id,
        "state": "reported",
        "certificate_check": certificate_check,
        "authority_check": None,
        "wait_until": None,
        "history": [{"at": now, "event": "death reported; AI pre-check attached"}],
    }


def _log(case: dict[str, Any], event: str) -> None:
    case["history"].append({"at": time.time(), "event": event})


def _check_override(check: dict[str, Any] | None, what: str, reviewer: str, reason: str | None) -> str:
    """Going against the AI's "not valid" needs a written reason, kept in the audit log."""
    if check and check.get("verdict") == "not_valid":
        if not reason or len(reason.strip()) < 10:
            raise ReleaseError(f"The AI marked the {what} as not valid. A written reason is needed to override it.")
        return f"; OVERRIDE of AI 'not valid' by {reviewer}: {reason.strip()}"
    return ""


def _record_override(case: dict[str, Any], what: str) -> None:
    """The guardian records an override for a risk reviewer (PR-15.6): which check and which case,
    never the reviewer's name or their written reason (those stay in the case's audit trail)."""
    governance.record_intervention(CHECKER, "add_check", f"A reviewer went against the AI's 'not valid' on the {what}",
                                   case_key=case["id"])


def confirm_death(case: dict[str, Any], reviewer: str, override_reason: str | None = None) -> dict[str, Any]:
    if case["state"] != "reported":
        raise ReleaseError(f"cannot confirm death from state {case['state']}")
    note = _check_override(case.get("certificate_check"), "death certificate", reviewer, override_reason)
    case["state"] = "death_confirmed"
    case["death_confirmed_by"] = reviewer
    _log(case, f"death confirmed by {reviewer}{note}")
    if note:
        _record_override(case, "death certificate")
    return case


def attach_authority_check(case: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    if case["state"] != "death_confirmed":
        raise ReleaseError("legal authority can only be submitted after the death is confirmed")
    case["authority_check"] = check
    _log(case, "proof of legal authority submitted; AI pre-check attached")
    return case


def confirm_authority(case: dict[str, Any], reviewer: str, override_reason: str | None = None,
                      now: float | None = None) -> dict[str, Any]:
    if case["state"] != "death_confirmed" or case.get("authority_check") is None:
        raise ReleaseError("need a confirmed death and a submitted proof of authority")
    if reviewer.strip().casefold() == (case.get("death_confirmed_by") or "").strip().casefold():
        raise ReleaseError("A different reviewer must confirm legal authority (two-person rule).")
    note = _check_override(case.get("authority_check"), "proof of legal authority", reviewer, override_reason)
    now = time.time() if now is None else now
    case["state"] = "waiting"
    case["wait_until"] = now + settings.release_wait_seconds
    _log(case, f"legal authority confirmed by {reviewer}{note}; waiting period started; owner devices alerted")
    if note:
        _record_override(case, "proof of legal authority")
    return case


def owner_responded(case: dict[str, Any]) -> dict[str, Any]:
    if case["state"] in ("released", "cancelled"):
        raise ReleaseError(f"case already {case['state']}")
    case["state"] = "cancelled"
    _log(case, "owner responded during checks; release cancelled")
    return case


def refresh(case: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Advance `waiting` to `released` once the waiting period has passed."""
    now = time.time() if now is None else now
    if case["state"] == "waiting" and case["wait_until"] is not None and now >= case["wait_until"]:
        case["state"] = "released"
        _log(case, "waiting period ended with no response from owner; vault released to recipient")
    return case


def is_released(case: dict[str, Any] | None) -> bool:
    return bool(case) and case["state"] == "released"
