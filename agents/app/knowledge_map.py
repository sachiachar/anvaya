"""The knowledge map: the owner's kept knowledge as a graph, for the demo (PR-7).

Read-only, built from the bundle's catalog on every request, so it fills in live as the owner
taps Keep. Nodes are the owner, kept concepts, institutions named by accounts, and people a
story mentions who aren't kept yet. Links are the concepts' typed links (PR-7.9) once they
exist; until then the links the cards already imply, marked `derived`:

    story or wish -> each person it mentions          ("mentions")
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

    # People first, so links from stories and accounts can find them by name or first name.
    people: dict[str, str] = {}
    for cid, c in concepts.items():
        if c["graph"]["type"] == "Person":
            name = c["record"].get("name") or c["title"]
            people.setdefault(_key(name), cid)
            people.setdefault(_key(name).split(" ")[0], cid)

    def person(name: str) -> str | None:
        """A kept person, or a 'mentioned' one the owner hasn't kept yet."""
        if not _key(name):
            return None
        if _key(name) in (_key(owner_name), _key(owner_name).split(" ")[0]):
            return "owner"
        found = people.get(_key(name)) or people.get(_key(name).split(" ")[0])
        if found:
            return found
        mid = f"mention:{_key(name)}"
        if not any(n["id"] == mid for n in nodes):
            nodes.append({"id": mid, "label": name.strip(), "group": "mentioned", "type": "Mentioned"})
        return mid

    institutions: dict[str, str] = {}
    for cid, c in concepts.items():
        okf_type, record = c["graph"]["type"], c["record"]
        nodes.append({"id": cid, "label": c["title"], "group": _group(okf_type), "type": okf_type,
                      "description": c.get("description", ""), "kept_at": c.get("kept_at"),
                      "verified": c.get("verified", False)})
        typed = c["graph"].get("links") or {}
        for name, targets in typed.items():  # PR-7.9, when it lands
            for target in targets if isinstance(targets, list) else [targets]:
                if target in concepts:
                    edges.append({"from": cid, "to": target, "label": name, "derived": False})
        if okf_type == "Person":
            edges.append({"from": cid, "to": "owner", "label": record.get("relationship") or "family",
                          "derived": True})
        elif c["collection"] == "moments":
            for name in record.get("people") or []:
                target = person(name)
                if target:
                    edges.append({"from": cid, "to": target, "label": "mentions", "derived": True})
        elif c["collection"] == "inventory":
            inst = (record.get("institution") or "").strip()
            if inst:
                iid = institutions.setdefault(_key(inst), f"institution:{_key(inst)}")
                if not any(n["id"] == iid for n in nodes):
                    nodes.append({"id": iid, "label": inst, "group": "institution", "type": "Institution"})
                edges.append({"from": cid, "to": iid, "label": "with", "derived": True})
            for field, label in (("nominee_or_beneficiary", "nominee"), ("holder_name", "holder")):
                target = person(record.get(field) or "")
                if target and target != "owner":
                    edges.append({"from": cid, "to": target, "label": label, "derived": True})
            if okf_type in NOMINEE_EXPECTED and not record.get("nominee_or_beneficiary"):
                gaps.append({"node": cid, "missing": "nominee"})

    titles = {cid: c["title"] for cid, c in cat.get("concepts", {}).items()}
    recent = [{"at": e["at"], "action": e["action"], "id": e["id"], "title": e.get("title") or titles.get(e["id"], "")}
              for e in cat.get("events", [])[-20:]][::-1]
    return {"nodes": nodes, "edges": edges, "gaps": gaps, "recent": recent,
            "counts": {"concepts": len(concepts), "links": len(edges), "gaps": len(gaps)}}
