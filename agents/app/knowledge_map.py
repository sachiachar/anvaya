"""The knowledge map: the owner's kept knowledge as a graph, for the demo (PR-7).

Read-only, built from the bundle's catalog on every request, so it fills in live as the owner
taps Keep. Nodes are the owner, kept concepts, institutions named by accounts, and people a
story mentions who aren't kept yet. Links are the concepts' typed links (PR-7.9) once they
exist; until then the links the cards already imply, marked `derived`:

    story or wish -> each person it mentions          ("mentions"), by name or relationship
                                                      ("my wife"), in its people or its text
    story or wish -> an account or bank it names      ("about")
    account or policy -> its nominee, its holder      ("nominee", "holder")
    account or policy -> its institution              ("with")
    person -> the owner                               (their relationship)

Gaps: an account or policy with no nominee (the full gap check is PR-7.10).
Only the owner's own copy, through the same guard as every route; never the vault.
"""

from __future__ import annotations

import re

from . import knowledge

NOMINEE_EXPECTED = {"Bank Account", "Insurance Policy", "Investment", "Retirement Account"}
_POSSESSIVE = re.compile(r"^(?:my|his|her|their|our|[a-z]+'s)\s+")
GROUPS = {"Story": "story", "Advice": "story", "Practical Fact": "story", "Wish": "wish", "Person": "person"}


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def _group(okf_type: str) -> str:
    return GROUPS.get(okf_type, "account")


def build(owner_id: str, owner_name: str) -> dict:
    cat = knowledge.catalog(owner_id)
    concepts = {cid: c for cid, c in cat.get("concepts", {}).items() if c["graph"]["status"] == "stable"}
    nodes: list[dict] = [{"id": "owner", "label": owner_name, "group": "owner", "type": "Owner"}]
    edges: list[dict] = []
    gaps: list[dict] = []

    # People first, so links from stories and accounts can find them. A full name must match; a
    # single word ("Ajay") matches a kept person's first name only if exactly one has it.
    people: dict[str, str] = {}
    first_names: dict[str, list[str]] = {}
    relations: dict[str, list[str]] = {}
    for cid, c in concepts.items():
        if c["graph"]["type"] == "Person":
            name = _key(c["record"].get("name") or c["title"])
            people.setdefault(name, cid)
            first_names.setdefault(name.split(" ")[0], []).append(cid)
            rel = _key(c["record"].get("relationship") or "")
            if rel:
                relations.setdefault(rel, []).append(cid)
    owner_key = _key(owner_name)

    def person(name: str) -> str | None:
        """The owner, a kept person, or a 'mentioned' one the owner hasn't kept yet."""
        key = _POSSESSIVE.sub("", _key(name))  # "my wife", "Vikram's wife" -> "wife"
        if not key:
            return None
        single = " " not in key
        if key == owner_key or (single and key == owner_key.split(" ")[0]):
            return "owner"
        if key in people:
            return people[key]
        if single and len(first_names.get(key, [])) == 1:
            return first_names[key][0]
        if len(relations.get(key, [])) == 1:  # "wife" -> the one kept person who is the wife
            return relations[key][0]
        mid = f"mention:{key}"
        if not any(n["id"] == mid for n in nodes):
            nodes.append({"id": mid, "label": name.strip(), "group": "mentioned", "type": "Mentioned"})
        return mid

    linked: set[tuple[str, str, str]] = set()

    def link(a: str, b: str, label: str, derived: bool = True) -> None:
        if (a, b, label) not in linked:
            linked.add((a, b, label))
            edges.append({"from": a, "to": b, "label": label, "derived": derived})

    institutions: dict[str, str] = {}
    moments: list[tuple[str, str]] = []  # (id, text), linked to people and accounts they name below
    accounts: dict[str, list[str]] = {}  # institution key -> account ids
    for cid, c in concepts.items():
        okf_type, record = c["graph"]["type"], c["record"]
        nodes.append({"id": cid, "label": c["title"], "group": _group(okf_type), "type": okf_type,
                      "description": c.get("description", ""), "kept_at": c.get("kept_at"),
                      "verified": c.get("verified", False)})
        typed = c["graph"].get("links") or {}
        for name, targets in typed.items():  # PR-7.9, when it lands
            for target in targets if isinstance(targets, list) else [targets]:
                if target in concepts:
                    link(cid, target, name, derived=False)
        if okf_type == "Person":
            link(cid, "owner", record.get("relationship") or "family")
        elif c["collection"] == "moments":
            for name in record.get("people") or []:
                target = person(name)
                if target:
                    link(cid, target, "mentions")
            moments.append((cid, f"{c['title']} {c.get('description', '')} {record.get('summary', '')}"))
        elif c["collection"] == "inventory":
            inst = (record.get("institution") or "").strip()
            if inst:
                accounts.setdefault(_key(inst), []).append(cid)
                iid = institutions.setdefault(_key(inst), f"institution:{_key(inst)}")
                if not any(n["id"] == iid for n in nodes):
                    nodes.append({"id": iid, "label": inst, "group": "institution", "type": "Institution"})
                link(cid, iid, "with")
            for field, label in (("nominee_or_beneficiary", "nominee"), ("holder_name", "holder")):
                target = person(record.get(field) or "")
                if target and target != "owner":
                    link(cid, target, label)
            if okf_type in NOMINEE_EXPECTED and not record.get("nominee_or_beneficiary"):
                gaps.append({"node": cid, "missing": "nominee"})

    # Stories and wishes also link to the kept people and the accounts their own text names, so
    # "Transfer of SBI account to my wife" reaches the SBI account and the wife.
    for cid, text in moments:
        words = f" {_key(re.sub(r'[^A-Za-z0-9 ]', ' ', text))} "
        for name, pid in [*people.items(), *((r, ids[0]) for r, ids in relations.items() if len(ids) == 1)]:
            if len(name) > 2 and f" {name} " in words:
                link(cid, pid, "mentions")
        for inst, ids in accounts.items():
            if f" {inst} " in words:
                for aid in ids:
                    link(cid, aid, "about")

    titles = {cid: c["title"] for cid, c in cat.get("concepts", {}).items()}
    recent = [{"at": e["at"], "action": e["action"], "id": e["id"], "title": e.get("title") or titles.get(e["id"], "")}
              for e in cat.get("events", [])[-20:]][::-1]
    return {"nodes": nodes, "edges": edges, "gaps": gaps, "recent": recent,
            "counts": {"concepts": len(concepts), "links": len(edges), "gaps": len(gaps)}}
