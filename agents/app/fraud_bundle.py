"""Fraud knowledge as one platform-level OKF bundle (PR-2.14, Open Knowledge Format v0.2).

The fraud guides (one per kind of document and country) live here as concepts, in the same format as
owners' kept knowledge (knowledge.py), at platform level in the private media bucket:

    platform/fraud/
        index.md                               okf_version "0.2"; every guide, with links
        log.md                                 what changed, newest first
        IN.death_certificate/index.md          the guide: document, country, title
        IN.death_certificate/signs/<id>.md     a warning sign the certificate check looks for
        IN.death_certificate/tactics/<id>.md   a known way of forging or misusing the document
        IN.death_certificate/scenarios/<id>.md a confirmed attack scenario in the guide
        IN.death_certificate/sources/<id>.md   a cited source
        .catalog.json                          machine copy of every concept (rebuildable from the files)
        .events/                               one small file per change

Each item keeps its stable id (a sign id is what the certificate check names), so a change rewrites
the item's file and records an event with the guide version it belongs to; a retired item stays,
marked retired (OKF status "deprecated"). The guide's version number goes up with each change, as
before. Callers use fraud_guides.py, whose interface doesn't change.

Concurrency: a write is conditional on the catalog not having changed since it was read; a write
built on an older guide version raises Stale, and the caller builds again from the latest one.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any

from . import media
from .knowledge import OKF_VERSION, _iso, render

OWNER = "platform"
ROOT = "fraud"
CATALOG = f"{ROOT}/.catalog.json"
KINDS = {"signs": "Warning Sign", "tactics": "Fraud Tactic", "scenarios": "Fraud Scenario", "sources": "Source"}
GUIDE_TYPE = "Fraud Guide"
GUIDE_FIELDS = ("key", "title", "country", "document")
CACHE_SECONDS = 10  # a read reuses the catalog for this long; a write in this process refreshes it
MAX_EVENTS = 2000
RETRIES = 20


class Stale(Exception):
    """The guide changed since the version this write was built on."""


_cache: dict[str, Any] = {"at": 0.0, "cat": None}
_lock = threading.Lock()


def _empty() -> dict:
    return {"okf_version": OKF_VERSION, "guides": {}, "concepts": {}, "events": []}


def _load() -> tuple[dict, int]:
    data, version = media.load_versioned(OWNER, CATALOG)
    return (json.loads(data) if data else _empty()), version


def catalog(fresh: bool = False) -> dict:
    """The bundle's catalog, reused for a few seconds (the certificate check reads the guide often)."""
    with _lock:
        if not fresh and _cache["cat"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["cat"]
    cat, _ = _load()
    with _lock:
        _cache.update(at=time.time(), cat=cat)
    return cat


def forget_cache() -> None:
    with _lock:
        _cache.update(at=0.0, cat=None)


def clear() -> None:
    """Remove the whole bundle (tests only, through fraud_guides.clear_saved)."""
    for name in media.names(OWNER, f"{ROOT}/"):
        media.delete(OWNER, name)
    for name in (CATALOG, RECORDS):
        media.delete(OWNER, name)
    forget_cache()
    records.forget_cache()


# ---- Reading ------------------------------------------------------------------------------------


def guide_keys() -> list[str]:
    return sorted(catalog()["guides"])


def load_guide(key: str, *, fresh: bool = False) -> dict | None:
    """A guide as a plain dict (fraud_guides.Guide reads it), or None if the bundle hasn't got it.
    Items keep the order they joined the guide in."""
    cat = catalog(fresh)
    guide = cat["guides"].get(key)
    if not guide:
        return None
    items: dict[str, list] = {kind: [] for kind in KINDS}
    for entry in sorted((e for e in cat["concepts"].values() if e["guide"] == key), key=lambda e: e["order"]):
        items[entry["kind"]].append(entry["record"])
    return {**{f: guide[f] for f in GUIDE_FIELDS}, "version": guide["version"], "updated_at": guide["updated_at"],
            **items}


def history(key: str, item_id: str | None = None) -> list[dict]:
    """The changes to a guide (or one of its items), oldest first."""
    return [e for e in catalog(fresh=True)["events"] if e["guide"] == key and (item_id is None or e["item"] == item_id)]


# ---- Writing ------------------------------------------------------------------------------------


def _path(key: str, kind: str, item_id: str) -> str:
    return f"{key}/{kind}/{item_id}.md"


def item_id(record: dict) -> str:
    """The item's id as a safe file name; an item saved without one (an old scenario) gets one from its content."""
    raw = str(record.get("id") or "") or "x" + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:12]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw).lstrip(".")[:120] or "x"


def _title(kind: str, record: dict) -> str:
    return {"signs": record.get("look_for", ""), "tactics": record.get("name", ""),
            "scenarios": record.get("title") or record.get("scenario", ""), "sources": record.get("title", "")}[kind]


def _links(key: str, kind: str, record: dict) -> dict[str, list[str]]:
    sources = [_path(key, "sources", s) for s in record.get("sources") or [] if isinstance(s, str)]
    links: dict[str, list[str]] = {"cites": sources} if sources else {}
    if kind == "tactics":
        caught = sorted({s.get("caught_by") for s in record.get("steps") or [] if s.get("caught_by")})
        links["caught_by"] = [_path(key, "signs", c) for c in caught]
    return links


def _body(kind: str, record: dict, title: str, links: dict[str, list[str]]) -> str:
    lines = [f"# {title}", ""]
    if kind == "signs":
        lines += [f"**A forgery shows:** {record.get('look_for', '')}", "",
                  f"**A genuine one shows:** {record.get('genuine', '')}"]
    elif kind == "tactics":
        lines += [f"{i}. {s.get('text', '')} (caught by: {s.get('caught_by', '')})"
                  for i, s in enumerate(record.get("steps") or [], 1)]
    elif kind == "scenarios":
        lines += [f"{i}. {s}" for i, s in enumerate(record.get("steps") or [], 1)] or [str(record.get("claim", ""))]
    elif kind == "sources":
        lines += [f"[{record.get('title', '')}]({record.get('url', '')})"]
    for label, paths in links.items():
        names = ", ".join(f"[{p.split('/')[-1][:-3]}](../../{p})" for p in paths)
        lines += ["", f"**{label.replace('_', ' ').capitalize()}:** {names}"]
    return "\n".join(lines) + "\n"


def _front(key: str, kind: str, record: dict, *, at: str, version: int) -> tuple[dict, str]:
    title = _title(kind, record)
    retired = record.get("status") == "retired"
    links = _links(key, kind, record)
    front = {
        "type": KINDS[kind], "title": title, "description": title[:200],
        "tags": ["fraud", key, kind],
        "sources": [{"resource": f"anvaya:fraud-guide/{key}", "title": "Anvaya fraud guide"}],
        "generated": {"by": "anvaya/fraud-pipeline" if record.get("auto") else "anvaya/fraud-guide", "at": at},
        "status": "deprecated" if retired else "stable",
        "id": f"{key}/{kind}/{item_id(record)}",
        "guide": key, "kind": kind, "guide_version": version,
        "links": links,
        "record": record,  # the item's fields, so the catalog can always be rebuilt from the files
    }
    return front, _body(kind, record, title, links)


def _guide_index(guide: dict, cat: dict) -> str:
    key = guide["key"]
    front = {"type": GUIDE_TYPE, "title": guide["title"], "description": f"{guide['document']}, {guide['country']}",
             "tags": ["fraud", key], "status": "stable", "id": key, "version": guide["version"],
             "record": {f: guide[f] for f in GUIDE_FIELDS}}
    lines = [f"# {guide['title']}", "", f"Version {guide['version']}.", ""]
    for kind, label in (("signs", "Warning signs"), ("tactics", "Tactics"), ("scenarios", "Scenarios"), ("sources", "Sources")):
        entries = sorted((e for e in cat["concepts"].values() if e["guide"] == key and e["kind"] == kind),
                         key=lambda e: e["order"])
        if entries:
            lines += [f"## {label}", ""] + [f"- [{e['title'] or e['record'].get('id')}]({e['path'][len(key) + 1:]})"
                                             + (" (retired)" if e["status"] == "deprecated" else "") for e in entries] + [""]
    return render(front, "\n".join(lines))


def _bundle_index(cat: dict) -> str:
    front = {"okf_version": OKF_VERSION, "title": "Anvaya fraud knowledge", "description": "Fraud reference guides"}
    lines = ["# Anvaya fraud knowledge", ""] + [f"- [{g['title']}]({k}/index.md), version {g['version']}"
                                                for k, g in sorted(cat["guides"].items())]
    return render(front, "\n".join(lines) + "\n")


def _log(cat: dict) -> str:
    lines = ["# What changed", ""] + [f"- {e['at']} · {e['guide']} v{e['version']} · {e['action']} · {e['item']}"
                                      for e in reversed(cat["events"])]
    return "\n".join(lines) + "\n"


def save_guide(guide: dict, *, built_on: int | None, at: float | None = None, action: str = "Update") -> None:
    """Write a guide version: every new or changed item as its concept file, an event for each, and
    the catalog. `built_on` is the version this one was built from (None for a guide not in the
    bundle yet); if the bundle's guide moved on since, Stale is raised and nothing is written."""
    at = at or time.time()
    stamp, key, version = _iso(at), guide["key"], guide["version"]

    def attempt() -> tuple[dict, list, list]:
        cat, file_version = _load()
        current = cat["guides"].get(key)
        if (current["version"] if current else None) != built_on:
            raise Stale(key)
        order = max((e["order"] for e in cat["concepts"].values()), default=0)
        events, writes, deletes = [], [], []

        def record_event(cid: str, kind: str, rid: str, path: str, change: str) -> None:
            event = {"at": stamp, "guide": key, "version": version, "item": cid, "action": change, "path": path,
                     "reason": action}
            writes.append((f"{ROOT}/.events/{stamp.replace(':', '')}-{key}-v{version}-{kind}-{rid}-{change.lower()}.json",
                           json.dumps(event, ensure_ascii=False).encode(), "application/json"))
            events.append(event)

        present = set()
        for kind in KINDS:
            for record in guide.get(kind) or []:
                rid = item_id(record)
                cid = f"{key}/{kind}/{rid}"
                present.add(cid)
                old = cat["concepts"].get(cid)
                if old and old["record"] == record:
                    continue
                front, body = _front(key, kind, record, at=stamp, version=version)
                path = _path(key, kind, rid)
                writes.append((f"{ROOT}/{path}", render(front, body).encode(), "text/markdown"))
                order += 0 if old else 1
                cat["concepts"][cid] = {"path": path, "guide": key, "kind": kind, "type": front["type"],
                                        "title": front["title"], "status": front["status"], "record": record,
                                        "order": old["order"] if old else order}
                record_event(cid, kind, rid, path, "Creation" if not old else
                             "Retirement" if front["status"] == "deprecated" and old["status"] != "deprecated" else "Update")
        for cid in [c for c, e in cat["concepts"].items() if e["guide"] == key and c not in present]:
            gone = cat["concepts"].pop(cid)  # left out of this version: its history stays in the events
            deletes.append(f"{ROOT}/{gone['path']}")
            record_event(cid, gone["kind"], cid.rsplit("/", 1)[1], gone["path"], "Deletion")
        cat["guides"][key] = {**{f: guide[f] for f in GUIDE_FIELDS}, "version": version,
                              "updated_at": guide.get("updated_at") or at, "path": f"{key}/index.md"}
        cat["events"] = (cat["events"] + events)[-MAX_EVENTS:]
        media.save_if(OWNER, CATALOG, json.dumps(cat, ensure_ascii=False).encode(), "application/json", file_version)
        return cat, writes, deletes

    # The catalog is written first (the conditional write decides who wins); the files follow it.
    cat, writes, deletes = _retry(attempt)
    for name, data, mime in writes:
        media.save(OWNER, name, data, mime)
    for name in deletes:
        media.delete(OWNER, name)
    with _lock:
        _cache.update(at=time.time(), cat=cat)
    media.save(OWNER, f"{ROOT}/{key}/index.md", _guide_index(cat["guides"][key], cat).encode(), "text/markdown")
    media.save(OWNER, f"{ROOT}/index.md", _bundle_index(cat).encode(), "text/markdown")
    media.save(OWNER, f"{ROOT}/log.md", _log(cat).encode(), "text/markdown")


def _retry(attempt):
    import random
    for _ in range(RETRIES):
        try:
            return attempt()
        except media.Changed:
            time.sleep(random.uniform(0.01, 0.08))
    raise RuntimeError("the fraud knowledge is busy; try again")


# ---- Research records (PR-2.14.2) ---------------------------------------------------------------
# Scenarios of every status, drafts waiting for review, items added on their own and reviewer
# decisions. They used to be Firestore collections; `records` keeps the same small interface (get,
# list, put, create, update, delete), so the pipeline and the portal changed only which store they
# call. Each record is a concept file with an event per change; `.records.json` is the machine copy,
# written with conditional writes. Run logs, locks, counters and the pause state stay in Firestore.

RECORDS = f"{ROOT}/.records.json"
COLLECTIONS = {"fraud_scenarios": ("research/scenarios", "Fraud Scenario Candidate"),
               "fraud_drafts": ("research/drafts", "Fraud Draft"),
               "fraud_auto": ("research/added", "Added Fraud Item"),
               "fraud_decisions": ("research/decisions", "Reviewer Decision")}
TITLE_FIELDS = ("scenario", "tactic", "title", "name", "look_for")


class Records:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {"at": 0.0, "cat": None}

    # Reading: the copy a write in this process left, or one at most CACHE_SECONDS old.
    def _catalog(self, fresh: bool = False) -> dict:
        with _lock:
            cat = self._cache["cat"]
            if not fresh and cat is not None and time.time() - self._cache["at"] < CACHE_SECONDS:
                return cat
        cat, _ = self._load()
        if not cat.get("migrated"):
            cat = self._migrate()
        with _lock:
            self._cache.update(at=time.time(), cat=cat)
        return cat

    def _load(self) -> tuple[dict, int]:
        data, version = media.load_versioned(OWNER, RECORDS)
        return (json.loads(data) if data else {"okf_version": OKF_VERSION, "records": {}, "events": []}), version

    def forget_cache(self) -> None:
        with _lock:
            self._cache.update(at=0.0, cat=None)

    def get(self, collection: str, doc_id: str) -> dict | None:
        doc = self._catalog()["records"].get(collection, {}).get(doc_id)
        return json.loads(json.dumps(doc)) if doc else None

    def list(self, collection: str, **where: Any) -> list[dict]:
        docs = self._catalog()["records"].get(collection, {}).values()
        return [json.loads(json.dumps(d)) for d in docs if all(d.get(k) == v for k, v in where.items())]

    def history(self, collection: str, doc_id: str) -> list[dict]:
        """The changes to one record, oldest first."""
        return [e for e in self._catalog(fresh=True)["events"] if e["collection"] == collection and e["item"] == doc_id]

    # Writing: each change is decided on the latest catalog, so overlapping writes never lose one.
    def put(self, collection: str, doc: dict) -> dict:
        doc = _plain(doc)
        self._change(collection, doc["id"], lambda _old: doc)
        return doc

    def create(self, collection: str, doc: dict) -> bool:
        doc = _plain(doc)
        made = [False]

        def build(old):  # runs once per attempt: only the attempt that was written counts
            made[0] = old is None
            return doc if old is None else old

        self._change(collection, doc["id"], build)
        return made[0]

    def update(self, collection: str, doc_id: str, fields: dict) -> dict:
        def build(old):
            if old is None:
                raise KeyError(f"{collection}/{doc_id}")
            return {**old, **_plain(fields)}

        return self._change(collection, doc_id, build)

    def delete(self, collection: str, doc_id: str) -> None:
        self._change(collection, doc_id, lambda _old: None)

    def _change(self, collection: str, doc_id: str, build) -> dict | None:
        folder, kind = COLLECTIONS[collection]

        def attempt():
            cat, version = self._load()
            if not cat.get("migrated"):  # old records are copied in before the first change
                self._migrate()
                cat, version = self._load()
            docs = cat["records"].setdefault(collection, {})
            old = docs.get(doc_id)
            new = build(json.loads(json.dumps(old)) if old else None)
            if new == old:
                return cat, new, None
            path = _record_path(folder, new or old, doc_id)
            stamp = _iso(time.time())
            action = "Deletion" if new is None else "Creation" if old is None else "Update"
            if new is None:
                docs.pop(doc_id, None)
            else:
                docs[doc_id] = new
            event = {"at": stamp, "collection": collection, "item": doc_id, "action": action, "path": path,
                     "guide": (new or old).get("guide_key")}
            cat["events"] = (cat["events"] + [event])[-MAX_EVENTS:]
            media.save_if(OWNER, RECORDS, json.dumps(cat, ensure_ascii=False).encode(), "application/json", version)
            return cat, new, (path, kind, event)

        cat, new, written = _retry(attempt)
        with _lock:
            self._cache.update(at=time.time(), cat=cat)
        if written:
            path, kind, event = written
            if new is None:
                media.delete(OWNER, f"{ROOT}/{path}")
            else:
                media.save(OWNER, f"{ROOT}/{path}", _record_file(kind, new, event["at"]).encode(), "text/markdown")
            name = f"{event['at'].replace(':', '')}-{collection}-{_safe(doc_id)}-{event['action'].lower()}.json"
            media.save(OWNER, f"{ROOT}/.events/{name}", json.dumps(event, ensure_ascii=False).encode(), "application/json")
        return json.loads(json.dumps(new)) if new else None

    def _migrate(self) -> dict:
        """Copy the records Firestore held before PR-2.14.2, once. A record already in the bundle wins.
        The Firestore copies are left for a person to remove after checking."""
        from .store import store

        def attempt():
            cat, version = self._load()
            if cat.get("migrated"):
                return cat
            for collection in COLLECTIONS:
                docs = cat["records"].setdefault(collection, {})
                for doc in store.list(collection):
                    if not doc.get("seeded"):  # old portal examples aren't knowledge
                        docs.setdefault(doc["id"], _plain(doc))
            cat["migrated"] = True
            media.save_if(OWNER, RECORDS, json.dumps(cat, ensure_ascii=False).encode(), "application/json", version)
            for collection, docs in cat["records"].items():
                folder, kind = COLLECTIONS[collection]
                for doc_id, doc in docs.items():
                    media.save(OWNER, f"{ROOT}/{_record_path(folder, doc, doc_id)}",
                               _record_file(kind, doc, _iso(time.time())).encode(), "text/markdown")
            return cat

        return _retry(attempt)


def _plain(doc: dict) -> dict:
    """The record as plain JSON values (a Firestore timestamp becomes text), so YAML and JSON both hold it."""
    return json.loads(json.dumps(doc, ensure_ascii=False, default=str))


def _safe(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(raw)).lstrip(".")[:120] or "x"


def _record_path(folder: str, doc: dict, doc_id: str) -> str:
    guide = doc.get("guide_key")
    return f"{_safe(guide)}/{folder}/{_safe(doc_id)}.md" if guide else f"{folder}/{_safe(doc_id)}.md"


def _record_file(kind: str, doc: dict, at: str) -> str:
    title = next((str(doc[f]) for f in TITLE_FIELDS if doc.get(f)), doc["id"])
    status = doc.get("review") or doc.get("status") or "recorded"
    front = {"type": kind, "title": title[:200], "description": str(doc.get("claim") or title)[:200],
             "tags": ["fraud", *([doc["guide_key"]] if doc.get("guide_key") else [])],
             "generated": {"by": "anvaya/fraud-pipeline", "at": at},
             "status": "deprecated" if status in ("removed", "faded", "rejected", "retired") else "draft"
             if status in ("pending", "unconfirmed") else "stable",
             "id": doc["id"], "record": doc}
    return render(front, f"# {title}\n\nStatus: {status}.\n")


records = Records()
