"""Kept knowledge as an Open Knowledge Format (OKF v0.2) bundle (PR-7).

Tapping Keep is the commit: a kept card becomes a concept file in the owner's bundle, and
the draft leaves Firestore, which keeps workflow data only. Everything that reads kept items
(merging, readiness, the archive, legal needs, the claims checklist, the family's assistant)
reads them from here.

The bundle lives in the owner's folder of the private media bucket (`<owner>/kept/`):

    index.md          okf_version "0.2"; titles and one-line descriptions, rebuilt after each Keep
    log.md            what changed, newest first, rebuilt from the events
    stories/ advice/ practical/ wishes/ accounts/ policies/ property/ people/   concepts
    .catalog.json     machine copy of every concept (rebuildable from the concept files)
    .events/          one small file per change, so two Keeps never write the same file

Each concept keeps its **graph part** (`type`, `status`, typed links) apart from its content,
so a fixed-code reader can use the graph without ever reading the text or sources (the gap
check, PR-7.10, and the anonymous fraud cues in docs/proposals/fraud-foresight.md section 5).

Rules enforced here: only owner-confirmed items enter (verified by `human:owner-<id>`); only
stories, accounts and people (never the vault); long numbers are masked to the last 4 digits.
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import yaml

from . import media
from .config import settings

ROOT = "kept"
CATALOG = f"{ROOT}/.catalog.json"
OKF_VERSION = "0.2"
STALE_AFTER_DAYS = 365  # accounts and policies ask to be re-confirmed after a year (PR-7.6)
RETRIES = 20  # conditional writes retry with a short random pause when Keeps overlap

# collection -> how each kept item becomes a concept: OKF type and folder.
MOMENT_TYPES = {"story": ("Story", "stories"), "advice": ("Advice", "advice"),
                "practical": ("Practical Fact", "practical"), "wish": ("Wish", "wishes")}
ACCOUNT_TYPES = {
    "bank_account": ("Bank Account", "accounts"), "investment": ("Investment", "accounts"),
    "retirement": ("Retirement Account", "accounts"), "insurance": ("Insurance Policy", "policies"),
    "property": ("Property", "property"), "loan": ("Loan", "accounts"),
    "subscription": ("Subscription", "accounts"), "digital_account": ("Digital Account", "accounts"),
    "other": ("Account", "accounts"),
}
PERSON_TYPE = ("Person", "people")
SECTIONS = [("stories", "Stories"), ("advice", "Advice"), ("practical", "Practical facts"), ("wishes", "Wishes"),
            ("people", "People"), ("accounts", "Accounts"), ("policies", "Insurance policies"),
            ("property", "Property")]
COLLECTIONS = ("moments", "inventory", "people")
# Fields of a card that are workflow, not knowledge.
WORKFLOW_FIELDS = {"owner_id", "status", "updates", "missing_fields"}
UNMASKED = {"id", "recording", "document"}


class NotKnowledge(ValueError):
    """Only stories, accounts and people can be kept in the bundle."""


# ---- Masking -----------------------------------------------------------------------

_LONG_NUMBER = re.compile(r"\d[\d\s-]{4,}\d")


def mask(value: Any) -> Any:
    """Account, card, policy and ID numbers keep only their last 4 digits, anywhere in a value."""
    if isinstance(value, str):
        def last4(m: re.Match) -> str:
            digits = re.sub(r"\D", "", m.group())
            return "••" + digits[-4:] if len(digits) >= 6 else m.group()
        return _LONG_NUMBER.sub(last4, value)
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    return value


# ---- Concepts ------------------------------------------------------------------------


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kind_of(collection: str, item: dict) -> tuple[str, str]:
    """(OKF type, folder) for a kept item."""
    if collection == "moments":
        return MOMENT_TYPES.get(item.get("kind", ""), ("Story", "stories"))
    if collection == "inventory":
        return ACCOUNT_TYPES.get(item.get("category", ""), ("Account", "accounts"))
    if collection == "people":
        return PERSON_TYPE
    raise NotKnowledge(collection)


def _title(collection: str, item: dict) -> str:
    if collection == "moments":
        return item.get("title") or "Untitled"
    if collection == "inventory":
        return " ".join(x for x in (item.get("institution"), item.get("product"), item.get("account_hint")) if x)
    return f"{item.get('name', '')} ({item.get('relationship', '')})".strip()


def _description(collection: str, item: dict) -> str:
    if collection == "moments":
        return item.get("summary", "")
    if collection == "inventory":
        nominee = item.get("nominee_or_beneficiary")
        return f"{item.get('product', 'Account')} with {item.get('institution', 'an institution')}" + \
            (f"; nominee: {nominee}" if nominee else "; no nominee recorded")
    return f"{item.get('name', '')}, {item.get('relationship', '')} of the owner"


def _body(collection: str, item: dict, title: str, description: str) -> str:
    lines = [f"# {title}", "", description]
    if collection == "moments" and item.get("quote"):
        lines += ["", "In their own words:", "", *[f"> {q}" for q in item["quote"].splitlines()]]
    if collection == "inventory":
        rows = [("Institution", item.get("institution")), ("Product", item.get("product")),
                ("Account", item.get("account_hint")), ("Holder", item.get("holder_name")),
                ("Nominee or beneficiary", item.get("nominee_or_beneficiary")),
                ("Approximate value", item.get("approximate_value"))]
        lines += ["", *[f"- **{k}:** {v}" for k, v in rows if v]]
    return "\n".join(lines) + "\n"


def concept(collection: str, item: dict, owner_id: str, *, at: float,
            generated_by: str | None = None, verified: bool = True) -> tuple[dict, str]:
    """The concept for a kept item: (frontmatter, Markdown body). Content is masked."""
    if collection not in COLLECTIONS:
        raise NotKnowledge(collection)
    # Content is masked; ids and file paths (which can contain long digit runs) are not.
    record = {k: v if k in UNMASKED else mask(v) for k, v in item.items() if k not in WORKFLOW_FIELDS}
    okf_type, _ = kind_of(collection, record)
    title, description = _title(collection, record), _description(collection, record)
    sources = [{"resource": "anvaya:conversation", "title": "Conversation with Saathi"}]
    for key, label in (("recording", "Recording"), ("document", "Uploaded document")):
        if record.get(key):
            sources.append({"resource": record[key], "title": label})
    front: dict[str, Any] = {
        "type": okf_type,
        "title": title,
        "description": description,
        "tags": [collection, record.get("kind") or record.get("category") or "person"],
        "sources": sources,
        "generated": {"by": generated_by or f"saathi/{settings.flash_model}", "at": _iso(at)},
        "status": "stable",
        "id": record["id"],
        "collection": collection,
        "links": {},  # typed links (PR-7.9)
        "record": record,  # the card's fields, so the catalog can always be rebuilt from the files
    }
    if verified:
        front["verified"] = {"by": f"human:owner-{owner_id}", "at": _iso(at)}
    if okf_type in ("Bank Account", "Insurance Policy", "Investment", "Retirement Account", "Loan"):
        front["stale_after"] = _iso(at + STALE_AFTER_DAYS * 86400)
    return front, _body(collection, record, title, description)


def render(front: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True) + "---\n\n" + body


def parse(text: str) -> tuple[dict, str]:
    """Split a concept file into frontmatter and body (OKF section 3)."""
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter")
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end]) or {}, text[end + 5:].lstrip("\n")


# ---- The catalog (machine copy) ---------------------------------------------------------


def _entry(front: dict, path: str) -> dict:
    """What the catalog keeps per concept: its graph part apart from its content."""
    return {
        "path": path,
        "graph": {"type": front["type"], "status": front.get("status", "stable"), "links": front.get("links", {})},
        "collection": front["collection"],
        "title": front.get("title", ""),
        "description": front.get("description", ""),
        "record": front.get("record", {}),
        "kept_at": front.get("verified", {}).get("at") or front.get("generated", {}).get("at"),
        "superseded_by": front.get("superseded_by"),
    }


def _empty() -> dict:
    return {"okf_version": OKF_VERSION, "concepts": {}}


def catalog(owner_id: str) -> dict:
    data, _ = media.load_versioned(owner_id, CATALOG)
    return json.loads(data) if data else _empty()


def _change_catalog(owner_id: str, change) -> dict:
    """Apply a change to the catalog with a conditional write, retrying if someone else wrote."""
    for _ in range(RETRIES):
        data, version = media.load_versioned(owner_id, CATALOG)
        cat = json.loads(data) if data else _empty()
        change(cat)
        try:
            media.save_if(owner_id, CATALOG, json.dumps(cat, ensure_ascii=False).encode(), "application/json", version)
            return cat
        except media.Changed:
            time.sleep(random.uniform(0.01, 0.08))
    raise RuntimeError("the kept knowledge is busy; try again")


# ---- Writing: Keep is the commit --------------------------------------------------------------


def _event(owner_id: str, at: float, action: str, front: dict, path: str) -> None:
    name = f"{ROOT}/.events/{_iso(at).replace(':', '')}-{front['id']}-{action.lower()}.json"
    event = {"at": _iso(at), "action": action, "id": front["id"], "title": front.get("title", ""), "path": path}
    media.save(owner_id, name, json.dumps(event, ensure_ascii=False).encode(), "application/json")


def _write(owner_id: str, front: dict, body: str) -> str:
    _, folder = kind_of(front["collection"], front["record"])
    path = f"{folder}/{front['id']}.md"
    media.save(owner_id, f"{ROOT}/{path}", render(front, body).encode(), "text/markdown")
    return path


def commit(collection: str, item: dict, owner_id: str, *, replaces: str | None = None,
           generated_by: str | None = None, verified: bool = True, at: float | None = None) -> dict:
    """Keep: write the item as a concept, mark the version it replaces deprecated, and rebuild
    the index and log. Returns the kept item, as readers see it."""
    at = at or time.time()
    front, body = concept(collection, item, owner_id, at=at, generated_by=generated_by, verified=verified)
    path = _write(owner_id, front, body)
    _event(owner_id, at, "Update" if replaces else "Creation", front, path)
    old_front: dict | None = None
    if replaces:
        old = catalog(owner_id)["concepts"].get(replaces)
        if old:
            text = media.load(owner_id, f"{ROOT}/{old['path']}")
            if text:
                old_front, old_body = parse(text.decode())
                old_front |= {"status": "deprecated", "superseded_by": front["id"]}
                media.save(owner_id, f"{ROOT}/{old['path']}", render(old_front, old_body).encode(), "text/markdown")
                _event(owner_id, at, "Deprecation", old_front, old["path"])

    def change(cat: dict) -> None:
        cat["concepts"][front["id"]] = _entry(front, path)
        if old_front and replaces in cat["concepts"]:
            cat["concepts"][replaces] = _entry(old_front, cat["concepts"][replaces]["path"])

    write_indexes(owner_id, _change_catalog(owner_id, change))
    return as_item(front["id"], _entry(front, path), owner_id)


# ---- index.md and log.md ------------------------------------------------------------------------


def _index(cat: dict) -> str:
    lines = ["---", f'okf_version: "{OKF_VERSION}"', "---", "", "# Kept knowledge", "",
             "What the owner chose to keep for their family. Each item was confirmed by the owner.", ""]
    stable = [(cid, c) for cid, c in cat["concepts"].items() if c["graph"]["status"] == "stable"]
    for folder, heading in SECTIONS:
        items = sorted((c for _, c in stable if c["path"].startswith(f"{folder}/")), key=lambda c: c["title"].lower())
        if items:
            lines += [f"# {heading}", *[f"* [{c['title']}](/{c['path']}) - {c['description']}" for c in items], ""]
    return "\n".join(lines).rstrip() + "\n"


def _log(owner_id: str) -> str:
    events = []
    for name in media.names(owner_id, f"{ROOT}/.events/"):
        data = media.load(owner_id, name)
        if data:
            events.append(json.loads(data))
    lines = ["# Log", ""]
    by_day: dict[str, list[dict]] = {}
    for e in sorted(events, key=lambda e: (e["at"], e["id"]), reverse=True):
        by_day.setdefault(e["at"][:10], []).append(e)
    for day, items in by_day.items():
        lines += [f"## {day}", *[f"* **{e['action']}** [{e['title']}](/{e['path']})" for e in items], ""]
    return "\n".join(lines).rstrip() + "\n"


def write_indexes(owner_id: str, cat: dict | None = None) -> None:
    cat = cat if cat is not None else catalog(owner_id)
    media.save(owner_id, f"{ROOT}/index.md", _index(cat).encode(), "text/markdown")
    media.save(owner_id, f"{ROOT}/log.md", _log(owner_id).encode(), "text/markdown")


def rebuild(owner_id: str) -> dict:
    """Rebuild the catalog, index.md and log.md from the concept files alone (PR-7.4)."""
    cat = _empty()
    for name in media.names(owner_id, f"{ROOT}/"):
        rel = name[len(ROOT) + 1:]
        if not rel.endswith(".md") or rel in ("index.md", "log.md") or rel.startswith("."):
            continue
        front, _ = parse((media.load(owner_id, name) or b"").decode())
        cat["concepts"][front["id"]] = _entry(front, rel)
    media.save(owner_id, CATALOG, json.dumps(cat, ensure_ascii=False).encode(), "application/json")
    write_indexes(owner_id, cat)
    return cat


# ---- Reading ----------------------------------------------------------------------------------------


def as_item(cid: str, entry: dict, owner_id: str) -> dict:
    """A kept concept in the shape the rest of the app uses for cards."""
    return {**entry["record"], "id": cid, "owner_id": owner_id, "status": "approved",
            "concept": entry["path"], "okf_status": entry["graph"]["status"]}


def items(owner_id: str, collection: str, *, include_replaced: bool = False, **where: Any) -> list[dict]:
    """Kept items of one collection (current versions only, unless asked), filtered by field."""
    out = []
    for cid, entry in catalog(owner_id)["concepts"].items():
        if entry["collection"] != collection:
            continue
        if entry["graph"]["status"] != "stable" and not include_replaced:
            continue
        item = as_item(cid, entry, owner_id)
        if all(item.get(k) == v for k, v in where.items()):
            out.append(item)
    return sorted(out, key=lambda i: i.get("concept", ""))


def get(owner_id: str, cid: str) -> dict | None:
    entry = catalog(owner_id)["concepts"].get(cid)
    return as_item(cid, entry, owner_id) if entry else None


def family(owner_id: str) -> list[str]:
    """The owner's family as "Name (relationship)", from the kept Person concepts."""
    return [f"{p['name']} ({p['relationship']})" for p in items(owner_id, "people")]


def read(owner_id: str, path: str) -> str | None:
    """One file of the bundle by its bundle path (e.g. "index.md", "/stories/m_1.md"), or None.
    Only Markdown inside the bundle: never the machine files or anything outside it."""
    rel = path.lstrip("/")
    if not rel.endswith(".md") or ".." in rel.split("/") or rel.startswith("."):
        return None
    data = media.load(owner_id, f"{ROOT}/{rel}")
    return data.decode() if data else None
