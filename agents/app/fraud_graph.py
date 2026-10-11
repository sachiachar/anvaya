"""The fraud knowledge as a graph for the admin portal's Fraud page (an Obsidian-style graph view).

Nodes are the guides and what they hold (warning signs, tactics, validated scenarios, sources), the
safeguards that catch them, and the research records still outside a guide (scenarios being
checked, gaps, drafts waiting for review). Links say how they connect: an item belongs to a guide,
a tactic step is caught by a sign or a safeguard, an item cites a source, a scenario was imagined
from a tactic. Fraud knowledge holds no owner data, so the portal may show it in full. A common-gap
cue appears only as its count (rule 4).
"""

from __future__ import annotations

from . import fraud_guides
from .fraud_bundle import records

HIDDEN_REVIEWS = {"faded"}  # no source after 3 runs: noise, not knowledge
RULED_OUT = {"removed", "rejected"}


def _short(text: str, n: int = 48) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.links: dict[tuple[str, str, str], dict] = {}

    def node(self, nid: str, group: str, title: str, **extra) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "group": group, "label": _short(title), "title": str(title or ""), **extra}
        return nid

    def link(self, source: str, target: str, kind: str) -> None:
        if source != target and source in self.nodes and target in self.nodes:
            self.links.setdefault((source, target, kind), {"source": source, "target": target, "kind": kind})

    def safeguard(self, name: str) -> str:
        return self.node(f"safeguard:{name.strip().lower()}", "safeguard", name.strip())

    def source(self, url: str, title: str) -> str:
        return self.node(f"source:{url}", "source", title or url, url=url)


def build(guide_key: str | None = None) -> dict:
    g = _Graph()
    guides = [x for x in fraud_guides.current_all() if guide_key in (None, x.key)]
    for guide in guides:
        gid = g.node(f"guide:{guide.key}", "guide", guide.title, version=guide.version)
        urls = {s.id: s for s in guide.sources}

        def cite(nid: str, ids: list, urls: dict = urls) -> None:
            for sid in ids:
                if (src := urls.get(sid)) is not None:
                    g.link(nid, g.source(src.url, src.title), "cites")

        signs = {s.id for s in guide.signs}
        for s in guide.signs:
            nid = g.node(f"item:{guide.key}/signs/{s.id}", "sign", s.look_for, status=s.status, label_kind=s.label,
                         auto=s.auto, text=f"A forgery shows: {s.look_for}\nA genuine one shows: {s.genuine}",
                         history={"guide": guide.key, "item": f"{guide.key}/signs/{s.id}"})
            g.link(nid, gid, "in guide")
            cite(nid, s.sources)
        for t in guide.tactics:
            nid = g.node(f"item:{guide.key}/tactics/{t.id}", "tactic", t.name, status=t.status, label_kind=t.label,
                         auto=t.auto, text="\n".join(f"{i}. {st.text}" for i, st in enumerate(t.steps, 1)),
                         history={"guide": guide.key, "item": f"{guide.key}/tactics/{t.id}"})
            g.link(nid, gid, "in guide")
            cite(nid, t.sources)
            for st in t.steps:
                target = (f"item:{guide.key}/signs/{st.caught_by}" if st.caught_by in signs
                          else g.safeguard(st.caught_by) if st.caught_by else None)
                if target:
                    g.link(nid, target, "caught by")
        for sc in guide.scenarios:
            sid = str(sc.get("id") or "")
            nid = g.node(f"item:{guide.key}/scenarios/{sid}", "scenario", sc.get("title") or sc.get("scenario", ""),
                         status=sc.get("status", "active"), auto=bool(sc.get("auto")), coverage=sc.get("coverage"),
                         history={"guide": guide.key, "item": f"{guide.key}/scenarios/{sid}"})
            g.link(nid, gid, "in guide")
            cite(nid, sc.get("sources") or [])
            if sc.get("caught_by"):
                g.link(nid, g.safeguard(sc["caught_by"]), "caught by")

    keys = {x.key for x in guides}
    for sc in records.list("fraud_scenarios"):
        review = sc.get("review")
        if sc.get("guide_key") not in keys or review in HIDDEN_REVIEWS:
            continue
        in_guide = f"item:{sc['guide_key']}/scenarios/{sc['id']}"
        nid = in_guide if in_guide in g.nodes else g.node(
            f"record:fraud_scenarios:{sc['id']}",
            "ruled_out" if review in RULED_OUT else "gap" if sc.get("coverage") == "gap" else "candidate",
            sc.get("scenario", ""), status=review, coverage=sc.get("coverage"),
            text="\n".join(f"{i}. {x}" for i, x in enumerate(sc.get("steps") or [], 1)) or sc.get("claim", ""),
            cue=({"owners": sc["cue"].get("owners")} if isinstance(sc.get("cue"), dict) else None),
            history={"collection": "fraud_scenarios", "item": sc["id"]})
        g.link(nid, f"guide:{sc['guide_key']}", "in guide")
        if sc.get("sequence_id"):
            g.link(nid, f"item:{sc['guide_key']}/tactics/{sc['sequence_id']}", "imagined from")
        if sc.get("caught_by") and sc.get("coverage") != "gap":
            g.link(nid, g.safeguard(sc["caught_by"]), "caught by")
        for src in sc.get("sources") or []:
            if isinstance(src, dict) and src.get("url"):
                g.link(nid, g.source(src["url"], src.get("title", "")), "cites")
    for d in records.list("fraud_drafts", review="pending"):
        if d.get("guide_key") not in keys:
            continue
        title = d.get("tactic") or (d.get("sign") or {}).get("look_for", "")
        nid = g.node(f"record:fraud_drafts:{d['id']}", "draft", title, status="pending", draft_kind=d.get("kind"),
                     history={"collection": "fraud_drafts", "item": d["id"]})
        g.link(nid, f"guide:{d['guide_key']}", "in guide")
        if d.get("tactic_id"):
            g.link(nid, f"item:{d['guide_key']}/tactics/{d['tactic_id']}", "sign for")
        for src in d.get("sources") or []:
            if isinstance(src, dict) and src.get("url"):
                g.link(nid, g.source(src["url"], src.get("title", "")), "cites")

    return {"nodes": list(g.nodes.values()), "links": list(g.links.values()),
            "guides": [{"id": x.key, "title": x.title} for x in fraud_guides.current_all()]}


def history(*, guide: str | None = None, collection: str | None = None, item: str) -> list[dict]:
    """The changes to one node, oldest first: a guide item from the guide's events, a research record from its own."""
    if guide:
        return fraud_guides.history(guide, item)
    return records.history(collection or "", item)
