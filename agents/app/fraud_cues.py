"""Cues for the fraud pipeline from the kept-knowledge graph, without private information
(docs/architecture.md §5).

Gaps in what owners keep, such as a policy with no nominee, are where a fraudster would push, so
they make good starting points for dreaming. This module turns them into anonymous counts:

  - Fixed code, not a model. It reads only the graph part of each owner's kept items
    (knowledge.graph_parts: type, status, links, which key fields are present). Never the text,
    names, sources or records.
  - A closed list of patterns, defined here and matching the gap check's kinds (gaps.py). Each cue
    is a pattern, an asset class and how many owners have it. No owner ids or keys, names,
    institutions, amounts or dates.
  - Only counts of at least the minimum (an admin portal setting, never below FLOOR) are
    reported; smaller ones are dropped.
  - The fraud agents never read bundles: they receive only these cues.

The demo has synthetic owners (ensure_synthetic_owners) so the counts can reach the minimum.
They're ordinary owners with clearly labelled synthetic data; there's no demo-only setting.
"""

from __future__ import annotations

import time
from collections import Counter

from . import knowledge, media
from .governance.core import platform_settings
from .store import new_id, store

# The minimum number of owners a cue needs is a setting a platform admin changes in the admin
# portal (Fraud page), audited. The privacy floor below is the one number kept in code: the
# setting can go higher, never lower, so a click can't make a count point at one owner.
SETTING = "cue_min_owners"
FLOOR = 5
CEILING = 1000
MAX_OWNERS = 2000  # owners scanned per run; the counts are a sample above this


def min_owners() -> int:
    """The current minimum: the admin portal's setting, never below FLOOR."""
    value = platform_settings().get(SETTING)
    return max(FLOOR, min(CEILING, int(value))) if isinstance(value, int) else FLOOR

# The closed list. Each pattern: what it means (shown to the dreamer), and the gap kind in gaps.py
# it corresponds to, so a validated scenario can later raise that gap's priority.
PATTERNS: dict[str, tuple[str, str | None]] = {
    "policy.nominee_missing": ("an insurance policy with no nominee recorded", "nominee"),
    "account.nominee_missing": ("a bank, investment or retirement account with no nominee recorded", "nominee"),
    "nominee.not_kept": ("an account or policy whose nominee isn't among the family the owner recorded", "nominee"),
    "property.heir_missing": ("property with nobody named to receive it", "heir"),
    "property.papers_missing": ("property with no record of where its papers are kept", "papers"),
    "person.relationship_missing": ("a family member whose relationship isn't recorded", "relationship"),
}
ACCOUNTS = {"Bank Account": "bank_account", "Investment": "investment", "Retirement Account": "retirement"}
PRIVACY_LINE = "We count common gaps, never the details, to protect everyone."


def patterns_of(graphs: list[dict]) -> set[tuple[str, str]]:
    """The (pattern, asset class) pairs one owner's graph shows. Reads only types, links and
    which fields are present."""
    found: set[tuple[str, str]] = set()
    for g in graphs:
        kind, has, links = g["type"], set(g.get("has", [])), g.get("links", {})
        if kind == "Insurance Policy" or kind in ACCOUNTS:
            asset = "insurance_policy" if kind == "Insurance Policy" else ACCOUNTS[kind]
            if "nominee_or_beneficiary" not in has:
                found.add(("policy.nominee_missing" if kind == "Insurance Policy" else "account.nominee_missing", asset))
            elif not links.get("nominee"):
                found.add(("nominee.not_kept", asset))
        elif kind == "Property":
            if "intended_for" not in has:
                found.add(("property.heir_missing", "property"))
            if "papers_at" not in has:
                found.add(("property.papers_missing", "property"))
        elif kind == "Person" and "relationship" not in has:
            found.add(("person.relationship_missing", "family"))
    return found


def cues(*, minimum: int | None = None) -> list[dict]:
    """Patterns at least the minimum number of owners have, most common first: [{pattern,
    asset_class, owners, means}]. Each owner counts once per pattern, however many items show it.

    The minimum is the admin portal's setting (min_owners()). Real owners join a count only when
    at least that many real owners share the pattern, and then rounded down to a multiple of it.
    The synthetic owners' counts are fixed in this file, so otherwise subtracting them would
    reveal a handful of real owners ("8 owners" = 7 synthetic + 1 real). Rounding also means one
    owner more or less never changes a count, so comparing runs, or the number of demo copies,
    reveals nothing about any one owner."""
    min_owners_ = minimum if minimum is not None else min_owners()
    real: Counter[tuple[str, str]] = Counter()
    synthetic: Counter[tuple[str, str]] = Counter()
    for owner in store.list("owners")[:MAX_OWNERS]:
        (synthetic if owner.get("synthetic") else real).update(patterns_of(knowledge.graph_parts(owner["id"])))
    counts = Counter({k: n - n % min_owners_ for k, n in real.items() if n >= min_owners_}) + synthetic
    return [{"pattern": p, "asset_class": a, "owners": n, "means": PATTERNS[p][0]}
            for (p, a), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= min_owners_]


def describe(found: list[dict]) -> str:
    """The cues as plain lines for the dreamer: counts and meanings only."""
    return "\n".join(f"- {c['pattern']}: {c['owners']} owners have {c['means']}" for c in found) or "none yet"


# ---- Synthetic owners for the demo ---------------------------------------------------------------

SYNTHETIC = "process:synthetic-owners"
# Twelve made-up owners. Their gaps are spread so that some patterns reach the minimum and one
# (a family member with no relationship, 3 owners) stays below it and is dropped.
_PLAN = [
    # (policy has nominee, property heir named, property papers recorded, person relationship known)
    (False, False, False, True), (False, False, True, True), (False, True, False, True),
    (False, False, False, False), (False, True, True, True), (False, False, False, True),
    (True, False, True, False), (True, True, False, True), (False, True, True, True),
    (True, True, True, False), (True, False, False, True), (True, True, True, True),
]


def synthetic_id(i: int) -> str:
    return f"owner_synthetic_{i:02d}"


def ensure_synthetic_owners() -> int:
    """Create the synthetic owners once. Returns how many were created."""
    made = 0
    for i, (nominee, heir, papers, relationship) in enumerate(_PLAN, 1):
        oid = synthetic_id(i)
        if store.get("owners", oid) and len(knowledge.graph_parts(oid)) == 3:
            continue  # complete; one left half-made by an error is made again
        media.delete_all(oid)
        seeded = {"generated_by": SYNTHETIC, "verified": False}
        knowledge.commit("people", {"id": new_id("p"), "name": f"Relative {i:02d}",
                                    "relationship": "son" if relationship else ""}, oid, **seeded)
        knowledge.commit("inventory", {"id": new_id("inv"), "category": "insurance", "institution": "Synthetic Insurer",
                                       "product": "Term life policy", "account_hint": "••0000",
                                       "nominee_or_beneficiary": f"Relative {i:02d}" if nominee else None}, oid, **seeded)
        knowledge.commit("inventory", {"id": new_id("inv"), "category": "property", "institution": "Self",
                                       "product": "Flat", "account_hint": "••0000",
                                       "intended_for": f"Relative {i:02d}" if heir else None,
                                       "papers_at": "a cupboard at home" if papers else None}, oid, **seeded)
        # The owner record last, so an owner that exists has all its items.
        store.put("owners", {"id": oid, "name": f"Synthetic owner {i:02d}", "synthetic": True, "created_at": time.time()})
        made += 1
    return made
