"""A tiny document store with two backends: in-memory (local demo, tests) and Firestore.

Every record is a dict with an "id" and, where relevant, an "owner_id".
"""

from __future__ import annotations

import copy
import json
import pathlib
import threading
import uuid
from typing import Any, Protocol

from .config import settings


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store(Protocol):
    def put(self, collection: str, doc: dict[str, Any]) -> dict[str, Any]: ...
    def create(self, collection: str, doc: dict[str, Any]) -> bool: ...
    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...
    def update(self, collection: str, doc_id: str, fields: dict[str, Any]) -> dict[str, Any]: ...
    def list(self, collection: str, **where: Any) -> list[dict[str, Any]]: ...
    def delete(self, collection: str, doc_id: str) -> None: ...
    def increment(self, collection: str, doc_id: str, amounts: dict[str, Any], fields: dict[str, Any]) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def put(self, collection, doc):
        with self._lock:
            self._data.setdefault(collection, {})[doc["id"]] = copy.deepcopy(doc)
        return doc

    def create(self, collection, doc):
        """Write only if no document has this id yet. False if one already exists."""
        with self._lock:
            docs = self._data.setdefault(collection, {})
            if doc["id"] in docs:
                return False
            docs[doc["id"]] = copy.deepcopy(doc)
            return True

    def get(self, collection, doc_id):
        doc = self._data.get(collection, {}).get(doc_id)
        return copy.deepcopy(doc) if doc else None

    def update(self, collection, doc_id, fields):
        with self._lock:
            doc = self._data[collection][doc_id]
            doc.update(copy.deepcopy(fields))
            return copy.deepcopy(doc)

    def list(self, collection, **where):
        with self._lock:  # another thread may be writing (requests run store calls in worker threads)
            docs = list(self._data.get(collection, {}).values())
        return [copy.deepcopy(d) for d in docs if all(d.get(k) == v for k, v in where.items())]

    def delete(self, collection, doc_id):
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)


    def increment(self, collection, doc_id, amounts, fields):
        """Add to counters atomically, creating the document with `fields` if needed. `amounts` is
        nested: {"agents": {"saathi.chat": {"calls": 1}}} adds 1 to doc["agents"]["saathi.chat"]["calls"]."""
        def add(target: dict, more: dict) -> None:
            for key, value in more.items():
                if isinstance(value, dict):
                    add(target.setdefault(key, {}), value)
                else:
                    target[key] = target.get(key, 0) + value

        with self._lock:
            add(self._data.setdefault(collection, {}).setdefault(doc_id, {"id": doc_id, **fields}), amounts)


class FileStore(MemoryStore):
    """Local development: in memory, but saved to a JSON file after every change, so a
    server restart (e.g. auto-reload after a code edit) doesn't lose the demo data."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = pathlib.Path(path)
        if self._path.exists():
            self._data = json.loads(self._path.read_text())

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        # default=str: a date (e.g. agent_steps.expire_at, for Firestore's TTL) is kept as text locally.
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, default=str))
        tmp.replace(self._path)

    def put(self, collection, doc):
        result = super().put(collection, doc)
        with self._lock:
            self._save()
        return result

    def create(self, collection, doc):
        created = super().create(collection, doc)
        if created:
            with self._lock:
                self._save()
        return created

    def update(self, collection, doc_id, fields):
        result = super().update(collection, doc_id, fields)
        with self._lock:
            self._save()
        return result

    def delete(self, collection, doc_id):
        super().delete(collection, doc_id)
        with self._lock:
            self._save()


    def increment(self, collection, doc_id, amounts, fields):
        super().increment(collection, doc_id, amounts, fields)
        with self._lock:
            self._save()


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self._db = firestore.Client()

    def put(self, collection, doc):
        self._db.collection(collection).document(doc["id"]).set(doc)
        return doc

    def create(self, collection, doc):
        """In a transaction, so two writers can't both create the same id."""
        from google.cloud import firestore

        ref = self._db.collection(collection).document(doc["id"])

        @firestore.transactional
        def write(tx) -> bool:
            if ref.get(transaction=tx).exists:
                return False
            tx.set(ref, doc)
            return True

        return write(self._db.transaction())

    def get(self, collection, doc_id):
        snap = self._db.collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def update(self, collection, doc_id, fields):
        ref = self._db.collection(collection).document(doc_id)
        ref.update(fields)
        return ref.get().to_dict()

    def list(self, collection, **where):
        query = self._db.collection(collection)
        for key, value in where.items():
            query = query.where(key, "==", value)
        return [snap.to_dict() for snap in query.stream()]

    def delete(self, collection, doc_id):
        self._db.collection(collection).document(doc_id).delete()


    def increment(self, collection, doc_id, amounts, fields):
        """Atomic on the server (Firestore Increment), so overlapping calls never lose a count.
        Nested maps merge, so only the counters named in `amounts` change."""
        from google.cloud import firestore

        def increments(more: dict) -> dict:
            return {k: increments(v) if isinstance(v, dict) else firestore.Increment(v) for k, v in more.items()}

        self._db.collection(collection).document(doc_id).set(
            {"id": doc_id, **fields, **increments(amounts)}, merge=True)


def _make_store() -> Store:
    if settings.store == "firestore":
        return FirestoreStore()
    if settings.store == "file":
        return FileStore(settings.local_store_path)
    return MemoryStore()


store: Store = _make_store()
