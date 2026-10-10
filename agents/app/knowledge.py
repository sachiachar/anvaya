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
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import yaml

from . import media
from .governance import core as governance
from .schemas import PREDICATES, WISH_PREDICATES
from .store import store

ROOT = "kept"
CATALOG = f"{ROOT}/.catalog.json"
OKF_VERSION = "0.2"
STALE_AFTER_DAYS = 365  # accounts and policies ask to be re-confirmed after a year (PR-7.6)
RETRIES = 20  # conditional writes retry with a short random pause when Keeps overlap
MAX_EVENTS = 2000  # changes kept in the catalog for log.md; every change also has its own event file

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
WORKFLOW_FIELDS = {"owner_id", "status", "updates", "missing_fields", "looks_like"}
UNMASKED = {"id", "recording", "document"}


class NotKnowledge(ValueError):
    """Only stories, accounts and people can be kept in the bundle."""


# ---- Masking -----------------------------------------------------------------------

# Identifiers, not amounts or dates: 9 or more digits, possibly split by spaces, dashes, dots or
# slashes (account, card, policy and Aadhaar numbers), and PAN (5 letters, 4 digits, 1 letter).
# Dates ("12-05-1951") and amounts ("1500000", "15,00,000") have fewer digits or use commas.
_NUMBER_RUN = re.compile(r"\d(?:[\s./-]?\d){8,}")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def _last4(m: re.Match) -> str:
    return "••" + re.sub(r"\W", "", m.group())[-4:]


def mask(value: Any) -> Any:
    """Account, card, policy and ID numbers keep only their last 4 characters, anywhere in a value."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return mask(str(value)) if len(str(abs(value))) >= 9 else value
    if isinstance(value, str):
        return _PAN.sub(_last4, _NUMBER_RUN.sub(_last4, value))
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


# ---- Typed links (PR-7.3, PR-7.9) -------------------------------------------------------------
#
# Links are frontmatter fields with a meaning (OKF allows extra keys), each a list of bundle paths,
# and the same links appear in the body as ordinary Markdown links, so any OKF reader sees the
# graph. They're worked out from what the card says, against the people the owner kept:
#   account or policy -> nominee, holder;  property or wish -> intended_for;  story -> people.
# A full name must match a kept person; a single word ("Ajay") matches a first name only if
# exactly one kept person has it. Unmatched names stay in the card, just without a link.

LINK_LABELS = {"nominee": "Nominee", "holder": "Holder", "intended_for": "Meant for", "people": "Mentions",
               "wants": "Wants", "promised_to": "Promised to", "worries_about": "Worries about"}
# Statements (PR-13.1) that link a kept wish or worry to the people in it, by predicate.
STATEMENT_LINKS = {**{p: "wants" for p in WISH_PREDICATES if p != "promised_to"},
                   "promised_to": "promised_to", "worries_about": "worries_about"}
# Fields whose presence (never their value) the graph records, for the gap check (PR-7.10).
HAS_FIELDS = ("nominee_or_beneficiary", "holder_name", "intended_for", "papers_at", "relationship")
# Answers that mean "not known": the same as the gap check's (gaps.UNKNOWN; a test keeps them equal).
UNKNOWN = {"", "unknown", "not sure", "none", "n/a"}


def _present(value: Any) -> bool:
    if isinstance(value, list):
        return any(_present(v) for v in value)
    return isinstance(value, str) and " ".join(re.sub(r"[^a-z0-9']+", " ", value.lower()).split()) not in UNKNOWN


def _has(record: dict) -> list[str]:
    """Which key fields a record really answers (presence only, never values)."""
    return [f for f in HAS_FIELDS if _present(record.get(f))]
_SPLIT = re.compile(r"\s*(?:,|;|&|\band\b)\s*", re.IGNORECASE)


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def _names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [n for v in values if isinstance(v, str) for n in _SPLIT.split(v) if n.strip()]


def _people(cat: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Kept people (current versions): full name -> path, and first name -> paths."""
    full: dict[str, str] = {}
    first: dict[str, list[str]] = {}
    for c in cat.get("concepts", {}).values():
        if c["collection"] == "people" and c["graph"]["status"] == "stable":
            name, path = _key(c["record"].get("name", "")), f"/{c['path']}"
            if name:
                full.setdefault(name, path)
                first.setdefault(name.split(" ")[0], []).append(path)
    return full, first


def _resolve(name: str, people: tuple[dict[str, str], dict[str, list[str]]]) -> str | None:
    full, first = people
    key = _key(name)
    if key in full:
        return full[key]
    matches = first.get(key, []) if " " not in key else []
    return matches[0] if len(matches) == 1 else None


def links_for(collection: str, record: dict, cat: dict) -> dict[str, list[str]]:
    """The typed links of a kept item, as bundle paths of kept people."""
    people = _people(cat)
    fields = {"inventory": {"nominee": "nominee_or_beneficiary", "holder": "holder_name", "intended_for": "intended_for"},
              "moments": {"people": "people", "intended_for": "intended_for"}}.get(collection, {})
    links: dict[str, list[str]] = {}
    for link, field_name in fields.items():
        paths = sorted({p for n in _names(record.get(field_name)) if (p := _resolve(n, people))})
        if paths:
            links[link] = paths
    if collection == "moments":
        for s in record.get("statements") or []:
            if link := STATEMENT_LINKS.get(s.get("predicate")):
                found = {p for n in _names([s.get("recipient"), s.get("object")]) if (p := _resolve(n, people))}
                if found:
                    links[link] = sorted(set(links.get(link, [])) | found)
    return links


WISH_STATES = ("kept", "understood", "planned", "done", "changed", "dropped")


def _wish_parts(record: dict) -> dict:
    """What the graph part adds for statements: the predicates used and a wish's state. Closed
    values only, so a fixed-code reader (fraud cues, PR-13.5) never sees what was said."""
    parts: dict = {}
    if predicates := _predicates(record):
        parts["predicates"] = predicates
    if record.get("wish_state") in WISH_STATES:
        parts["wish_state"] = record["wish_state"]
    return parts


def _predicates(record: dict) -> list[str]:
    """Which predicates a kept item's statements use: closed-list values only, never the words."""
    return sorted({p for s in record.get("statements") or [] if (p := s.get("predicate")) in PREDICATES})


def _link_lines(links: dict[str, list[str]], cat: dict | None) -> list[str]:
    titles = {f"/{c['path']}": c["title"] for c in (cat or {}).get("concepts", {}).values()}
    return [f"- {LINK_LABELS.get(link, link)}: [{titles.get(path, path)}]({path})"
            for link, paths in links.items() for path in paths]


def _body(collection: str, item: dict, title: str, description: str,
          links: dict[str, list[str]] | None = None, cat: dict | None = None) -> str:
    lines = [f"# {title}", "", description]
    if collection == "moments" and item.get("quote"):
        lines += ["", "In their own words:", "", *[f"> {q}" for q in item["quote"].splitlines()]]
    if collection == "inventory":
        rows = [("Institution", item.get("institution")), ("Product", item.get("product")),
                ("Account", item.get("account_hint")), ("Holder", item.get("holder_name")),
                ("Nominee or beneficiary", item.get("nominee_or_beneficiary")),
                ("Approximate value", item.get("approximate_value")), ("Papers kept at", item.get("papers_at")),
                ("Meant for", item.get("intended_for"))]
        lines += ["", *[f"- **{k}:** {v}" for k, v in rows if v]]
    if collection == "moments" and item.get("intended_for"):
        lines += ["", f"Meant for: {', '.join(item['intended_for'])}"]
    if links:
        lines += ["", "Linked to:", *_link_lines(links, cat)]
    return "\n".join(lines) + "\n"


def concept(collection: str, item: dict, owner_id: str, *, at: float,
            generated_by: str | None = None, verified: bool = True, cat: dict | None = None) -> tuple[dict, str]:
    """The concept for a kept item: (frontmatter, Markdown body). Content is masked."""
    if collection not in COLLECTIONS:
        raise NotKnowledge(collection)
    if not verified and not (generated_by or "").startswith("process:"):
        # Only the owner's Keep confirms knowledge; the demo's own seeding is the one exception,
        # and it's labelled as unconfirmed in the bundle and the index.
        raise NotKnowledge("only knowledge the owner confirmed can be kept")
    # Content is masked; ids and file paths (which can contain long digit runs) are not.
    record = {k: v if k in UNMASKED else mask(v) for k, v in item.items() if k not in WORKFLOW_FIELDS}
    if collection == "moments" and record.get("kind") == "wish":
        record.setdefault("wish_state", "kept")  # a kept wish starts here (PR-13.1); PR-13.2 moves it on
    else:
        record.pop("wish_state", None)  # only a wish has a wish state
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
        "generated": {"by": generated_by or f"saathi/{governance.primary_model('capture.extractor')}", "at": _iso(at)},
        "status": "stable",
        "id": record["id"],
        "collection": collection,
        "links": links_for(collection, record, cat or {}),  # typed links (PR-7.9)
        "record": record,  # the card's fields, so the catalog can always be rebuilt from the files
    }
    if verified:
        front["verified"] = {"by": f"human:owner-{owner_id}", "at": _iso(at)}
    if okf_type in ("Bank Account", "Insurance Policy", "Investment", "Retirement Account", "Loan"):
        front["stale_after"] = _iso(at + STALE_AFTER_DAYS * 86400)
    return front, _body(collection, record, title, description, front["links"], cat)


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
        # The graph part: what a fixed-code reader may use (gap check, fraud cues). Types, status,
        # links between kept items, and which key fields are present; never names, text or sources.
        "graph": {"type": front["type"], "status": front.get("status", "stable"), "links": front.get("links", {}),
                  "has": _has(front.get("record") or {}), **_wish_parts(front.get("record") or {})},
        "collection": front["collection"],
        "title": front.get("title", ""),
        "description": front.get("description", ""),
        "record": front.get("record", {}),
        "verified": bool(front.get("verified")),
        "kept_at": (front.get("verified") or {}).get("at") or front.get("generated", {}).get("at"),
        "superseded_by": front.get("superseded_by"),
    }


def _empty() -> dict:
    return {"okf_version": OKF_VERSION, "concepts": {}, "events": []}


# One read of each owner's catalog per request: the API's guard and the live call start a fresh
# cache; anything else (tests, scripts) reads storage every time.
_cache: ContextVar[dict | None] = ContextVar("knowledge_cache", default=None)


def start_request() -> None:
    _cache.set({})


def _load(owner_id: str) -> tuple[dict, int]:
    data, version = media.load_versioned(owner_id, CATALOG)
    return (json.loads(data) if data else _empty()), version


def catalog(owner_id: str) -> dict:
    cache = _cache.get()
    if cache is not None and owner_id in cache:
        return cache[owner_id]
    cat, _ = _load(owner_id)
    if cache is not None:
        cache[owner_id] = cat
    return cat


def _retry(attempt) -> Any:
    """Run a conditional write, retrying with a short random pause when someone else wrote first."""
    for _ in range(RETRIES):
        try:
            return attempt()
        except media.Changed:
            time.sleep(random.uniform(0.01, 0.08))
    raise RuntimeError("the kept knowledge is busy; try again")


def _change_catalog(owner_id: str, change) -> dict:
    def attempt() -> dict:
        cat, version = _load(owner_id)
        change(cat)
        media.save_if(owner_id, CATALOG, json.dumps(cat, ensure_ascii=False).encode(), "application/json", version)
        return cat

    cat = _retry(attempt)
    cache = _cache.get()
    if cache is not None:
        cache[owner_id] = cat
    return cat


# ---- Writing: Keep is the commit --------------------------------------------------------------


def _event(owner_id: str, at: float, action: str, front: dict, path: str) -> dict:
    """A change, also saved as its own file: the durable history a rebuild reads."""
    event = {"at": _iso(at), "action": action, "id": front["id"], "title": front.get("title", ""), "path": path}
    name = f"{ROOT}/.events/{_iso(at).replace(':', '')}-{front['id']}-{action.lower()}.json"
    media.save(owner_id, name, json.dumps(event, ensure_ascii=False).encode(), "application/json")
    return event


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
    if replaces and replaces not in _load(owner_id)[0]["concepts"]:
        raise Removed(replaces)
    front, body = concept(collection, item, owner_id, at=at, generated_by=generated_by, verified=verified,
                          cat=catalog(owner_id))
    path = _write(owner_id, front, body)
    events = [_event(owner_id, at, "Update" if replaces else "Creation", front, path)]
    old_front: dict | None = None
    if replaces:
        old = catalog(owner_id)["concepts"].get(replaces)
        text = media.load(owner_id, f"{ROOT}/{old['path']}") if old else None
        if text:
            old_front, old_body = parse(text.decode())
            old_front |= {"status": "deprecated", "superseded_by": front["id"]}
            media.save(owner_id, f"{ROOT}/{old['path']}", render(old_front, old_body).encode(), "text/markdown")
            events.append(_event(owner_id, at, "Deprecation", old_front, old["path"]))

    def change(cat: dict) -> None:
        if replaces and replaces not in cat["concepts"]:
            raise Removed(replaces)  # removed while this Keep was being written
        cat["concepts"][front["id"]] = _entry(front, path)
        if old_front and replaces in cat["concepts"]:
            cat["concepts"][replaces] = _entry(old_front, cat["concepts"][replaces]["path"])
        cat["events"] = (cat.get("events", []) + events)[-MAX_EVENTS:]

    try:
        _change_catalog(owner_id, change)
    except Removed:
        # Undo what this Keep wrote: the new version, its history, and the old file it rewrote.
        media.delete(owner_id, f"{ROOT}/{path}")
        if old_front:
            media.delete(owner_id, f"{ROOT}/{catalog_path(old_front)}")
        for name in media.names(owner_id, f"{ROOT}/.events/"):
            if f"-{front['id']}-" in name or (replaces and f"-{replaces}-" in name):
                media.delete(owner_id, name)
        raise
    if collection == "people":
        relink(owner_id)  # items that name this person (or an earlier version of them) link to it now
    write_indexes(owner_id)
    return as_item(front["id"], _entry(front, path), owner_id)


def catalog_path(front: dict) -> str:
    """Where a concept's file lives in the bundle."""
    _, folder = kind_of(front["collection"], front["record"])
    return f"{folder}/{front['id']}.md"


def relink(owner_id: str) -> int:
    """Work out every current item's links again (after a person is kept or replaced), and rewrite
    the items whose links changed. Not a new version: the item says the same; only its links do.
    Safe alongside other Keeps: a file is rewritten only if it hasn't changed since it was read,
    and the catalog update touches only the links of entries that are still current."""
    cat, _ = _load(owner_id)
    updates: dict[str, dict] = {}
    for cid, entry in cat["concepts"].items():
        if entry["graph"]["status"] != "stable" or entry["collection"] == "people":
            continue
        links = links_for(entry["collection"], entry["record"], cat)
        if links == entry["graph"]["links"]:
            continue
        data, version = media.load_versioned(owner_id, f"{ROOT}/{entry['path']}")
        if not data:
            continue
        front, _ = parse(data.decode())
        if front.get("status", "stable") != "stable":
            continue  # replaced meanwhile
        front["links"] = links
        body = _body(front["collection"], front.get("record", {}), front["title"], front["description"], links, cat)
        try:
            media.save_if(owner_id, f"{ROOT}/{entry['path']}", render(front, body).encode(), "text/markdown", version)
        except media.Changed:
            continue  # someone else changed it; their write wins
        updates[cid] = links

    def change(c: dict) -> None:
        for cid, links in updates.items():
            entry = c["concepts"].get(cid)
            if entry and entry["graph"]["status"] == "stable":
                entry["graph"]["links"] = links

    if updates:
        _change_catalog(owner_id, change)
    return len(updates)


REMOVED_TITLE = "An item was removed by the owner"


class Removed(LookupError):
    """The item an update would replace has been removed by the owner."""


def _versions_in(cat: dict, cid: str) -> set[str]:
    """Every version of an item in this catalog: earlier ones it replaced and later ones that
    replaced it."""
    concepts = cat["concepts"]
    found = {cid} if cid in concepts else set()
    while True:
        more = {k for k, e in concepts.items() if k not in found
                and (e.get("superseded_by") in found or any(concepts[f].get("superseded_by") == k for f in found))}
        if not more:
            return found
        found |= more


def versions(owner_id: str, cid: str) -> list[str]:
    """Every version of a kept item: the current one and each earlier one it replaced."""
    return sorted(_versions_in(catalog(owner_id), cid))


def remove(owner_id: str, cid: str) -> int:
    """The owner removed something wrongly recorded: take it and every version out of the catalog
    first (what the family's assistant reads), then delete their files and history, so it never
    reaches the family. Waiting update cards for it are deleted too, so it can't come back. The
    log keeps one line, with no title or content. Returns how many versions were removed."""
    event = {"at": _iso(time.time()), "action": "Removal", "id": new_removal_id(), "title": REMOVED_TITLE, "path": ""}
    gone: dict[str, str] = {}

    def change(cat: dict) -> None:
        gone.clear()
        for v in _versions_in(cat, cid):
            gone[v] = cat["concepts"].pop(v)["path"]
        cat["events"] = [e for e in cat.get("events", []) if e["id"] not in gone][-MAX_EVENTS + 1:] + [event]

    _change_catalog(owner_id, change)
    for path in gone.values():
        media.delete(owner_id, f"{ROOT}/{path}")
    for name in media.names(owner_id, f"{ROOT}/.events/"):
        if any(f"-{v}-" in name.rsplit("/", 1)[-1] for v in gone):
            media.delete(owner_id, name)
    media.save(owner_id, f"{ROOT}/.events/{event['at'].replace(':', '')}-{event['id']}-removal.json",
               json.dumps(event, ensure_ascii=False).encode(), "application/json")
    for collection in COLLECTIONS:  # cards still waiting for Keep
        for draft in store.list(collection, owner_id=owner_id):
            if draft.get("updates") in gone:
                store.delete(collection, draft["id"])
            elif (draft.get("looks_like") or {}).get("id") in gone:
                store.update(collection, draft["id"], {"looks_like": None})  # nothing left to replace
    relink(owner_id)
    write_indexes(owner_id)
    return len(gone)


def new_removal_id() -> str:
    """An id for the removal's log line that says nothing about what was removed."""
    return f"rm_{random.getrandbits(48):012x}"


# ---- index.md and log.md ------------------------------------------------------------------------


def _index(cat: dict) -> str:
    lines = ["---", f'okf_version: "{OKF_VERSION}"', "---", "", "# Kept knowledge", "",
             "What the owner chose to keep for their family, each confirmed with Keep. "
             "Anything not confirmed by the owner says so.", ""]
    stable = [c for c in cat["concepts"].values() if c["graph"]["status"] == "stable"]
    for folder, heading in SECTIONS:
        entries = sorted((c for c in stable if c["path"].startswith(f"{folder}/")), key=lambda c: c["title"].lower())
        if entries:
            lines += [f"# {heading}", *[f"* [{c['title']}](/{c['path']}) - {c['description']}"
                                        + ("" if c.get("verified") else " (not confirmed by the owner)")
                                        for c in entries], ""]
    return "\n".join(lines).rstrip() + "\n"


def _log(cat: dict) -> str:
    lines = ["# Log", ""]
    by_day: dict[str, list[dict]] = {}
    for e in sorted(cat.get("events", []), key=lambda e: (e["at"], e["id"], e["action"]), reverse=True):
        by_day.setdefault(e["at"][:10], []).append(e)
    for day, events in by_day.items():
        lines += [f"## {day}", *[f"* **{e['action']}** [{e['title']}](/{e['path']})" if e.get("path")
                                 else f"* **{e['action']}** {e['title']}" for e in events], ""]
    return "\n".join(lines).rstrip() + "\n"


def write_indexes(owner_id: str) -> None:
    """Rewrite index.md and log.md from the latest catalog. Each write is conditional on the file
    not having changed since it was read, so an older Keep can never overwrite a newer index."""
    for name, render_from in ((f"{ROOT}/index.md", _index), (f"{ROOT}/log.md", _log)):
        def attempt(name=name, render_from=render_from) -> None:
            _, version = media.load_versioned(owner_id, name)
            cat, _ = _load(owner_id)
            media.save_if(owner_id, name, render_from(cat).encode(), "text/markdown", version)
        _retry(attempt)


def rebuild(owner_id: str) -> dict:
    """Rebuild the catalog, index.md and log.md from the concept and event files (PR-7.4)."""
    cat = _empty()
    for name in media.names(owner_id, f"{ROOT}/"):
        rel = name[len(ROOT) + 1:]
        data = media.load(owner_id, name) or b""
        if rel.startswith(".events/") and rel.endswith(".json"):
            cat["events"].append(json.loads(data))
        elif rel.endswith(".md") and rel not in ("index.md", "log.md") and not rel.startswith("."):
            front, _ = parse(data.decode())
            cat["concepts"][front["id"]] = _entry(front, rel)
    cat["events"] = sorted(cat["events"], key=lambda e: (e["at"], e["id"], e["action"]))[-MAX_EVENTS:]
    _, version = media.load_versioned(owner_id, CATALOG)
    media.save_if(owner_id, CATALOG, json.dumps(cat, ensure_ascii=False).encode(), "application/json", version)
    write_indexes(owner_id)
    return cat


# ---- Reading ----------------------------------------------------------------------------------------


# List fields a card always has; a kept item has them too (empty when there's nothing), so the
# screens never meet a card without one. missing_fields itself stays out of the bundle: it's
# workflow, not knowledge.
CARD_LISTS = {"inventory": ("missing_fields",), "moments": ("people", "places", "intended_for", "statements")}


def as_item(cid: str, entry: dict, owner_id: str) -> dict:
    """A kept concept in the shape the rest of the app uses for cards."""
    lists = {f: [] for f in CARD_LISTS.get(entry["collection"], ()) if not entry["record"].get(f)}
    # kept_at: when the owner kept it (UTC, ISO 8601), shown wherever kept items are listed (PR-2.13.1).
    return {**entry["record"], **lists, "id": cid, "owner_id": owner_id, "status": "approved",
            "concept": entry["path"], "okf_status": entry["graph"]["status"], "kept_at": entry.get("kept_at")}


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


def current(owner_id: str, cid: str) -> dict | None:
    """The current version of a kept item, following "superseded by" from an older version."""
    concepts = catalog(owner_id)["concepts"]
    for _ in range(50):
        entry = concepts.get(cid)
        if not entry:
            return None
        if entry["graph"]["status"] == "stable" or not entry.get("superseded_by"):
            return as_item(cid, entry, owner_id)
        cid = entry["superseded_by"]
    return None


def for_reading(owner_id: str) -> list[dict]:
    """The current concepts as the family's assistant reads them (PR-7.5): id, bundle path, type,
    title, whether the owner confirmed it, and its text. Rendered from the catalog in one read;
    only kept knowledge, never the conversation or the vault."""
    out = []
    cat = catalog(owner_id)
    for cid, entry in sorted(cat["concepts"].items(), key=lambda kv: kv[1]["path"]):
        if entry["graph"]["status"] != "stable":
            continue
        out.append({"id": cid, "path": f"/{entry['path']}", "type": entry["graph"]["type"], "title": entry["title"],
                    "confirmed": entry.get("verified", False),
                    "text": _body(entry["collection"], entry["record"], entry["title"], entry["description"],
                                  entry["graph"]["links"], cat)})
    return out


def graph_parts(owner_id: str) -> list[dict]:
    """The graph part of every current kept item: type, status, typed links (bundle paths) and which
    key fields are present. Nothing else: no names, text, sources or record. This is all a
    fixed-code reader such as the fraud cues (fraud_cues.py) may see of an owner's knowledge.
    Links and presence are worked out now from the current record, so items kept before either
    existed are counted correctly."""
    cat = catalog(owner_id)
    out = []
    for entry in cat["concepts"].values():
        if entry["graph"]["status"] != "stable":
            continue
        out.append({"path": f"/{entry['path']}", "type": entry["graph"]["type"], "status": "stable",
                    "links": links_for(entry["collection"], entry["record"], cat), "has": _has(entry["record"]),
                    **_wish_parts(entry["record"])})
    return out


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
