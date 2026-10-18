"""Keep original recordings and documents: Cloud Storage in the cloud, a folder locally."""

from __future__ import annotations

import functools
import hashlib
import pathlib
import shutil
import threading
import uuid

from .config import settings

_local_lock = threading.Lock()  # makes save_if atomic locally, as Cloud Storage's precondition is


class Changed(Exception):
    """Someone else wrote the file since it was read (save_if)."""


@functools.cache
def _client():
    from google.cloud import storage

    return storage.Client()  # one client per process: creating one per call is slow


def _bucket():
    return _client().bucket(settings.media_bucket)


def _local(path: str) -> pathlib.Path:
    return pathlib.Path(settings.local_media_dir) / path


def _missing(e: Exception) -> bool:
    return getattr(e, "code", None) == 404


def save(owner_id: str, name: str, data: bytes, mime_type: str) -> str:
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        _bucket().blob(path).upload_from_string(data, content_type=mime_type)
        return f"gs://{settings.media_bucket}/{path}"
    target = _local(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write a temporary file, then rename it into place: the rename is atomic, so a reader
    # never sees a half-written file (Cloud Storage uploads are atomic already).
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return str(target)


def load(owner_id: str, name: str) -> bytes | None:
    """Read back a file saved with save(), or None if it isn't there."""
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        try:
            return _bucket().blob(path).download_as_bytes()  # one request; 404 means it isn't there
        except Exception as e:
            if _missing(e):
                return None
            raise
    target = _local(path)
    return target.read_bytes() if target.exists() else None


def _local_version(data: bytes) -> int:
    # A hash of the content, not the file time: file times can be too coarse to tell two writes apart.
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big") or 1


def load_versioned(owner_id: str, name: str) -> tuple[bytes | None, int]:
    """A file and its version (0 if it isn't there), for a later save_if()."""
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        blob = _bucket().get_blob(path)  # metadata first, so the version is certain
        if blob is None:
            return None, 0
        return blob.download_as_bytes(if_generation_match=blob.generation), blob.generation
    target = _local(path)
    if not target.exists():
        return None, 0
    data = target.read_bytes()
    return data, _local_version(data)


def save_if(owner_id: str, name: str, data: bytes, mime_type: str, version: int) -> None:
    """Save only if the file is still at the version read (0: only if it doesn't exist yet).
    Raises Changed otherwise, so two writers never overwrite each other."""
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        try:
            _bucket().blob(path).upload_from_string(data, content_type=mime_type, if_generation_match=version)
        except Exception as e:
            if getattr(e, "code", None) == 412:  # Precondition Failed: written since it was read
                raise Changed(path) from e
            raise
        return
    with _local_lock:
        if load_versioned(owner_id, name)[1] != version:
            raise Changed(path)
        save(owner_id, name, data, mime_type)


def names(owner_id: str, prefix: str) -> list[str]:
    """The names of an owner's files under a prefix, e.g. "kept/"."""
    if settings.media_bucket:
        start = f"{owner_id}/"
        return sorted(b.name[len(start):] for b in _bucket().list_blobs(prefix=f"{owner_id}/{prefix}"))
    root = _local(owner_id)
    return sorted(str(f.relative_to(root)) for f in (root / prefix).rglob("*")
                  if f.is_file() and not f.name.startswith(".")) \
        if (root / prefix).exists() else []  # dot files are save()'s temporary files


def delete(owner_id: str, name: str) -> None:
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        try:
            _bucket().blob(path).delete()
        except Exception as e:
            if not _missing(e):
                raise
        return
    _local(path).unlink(missing_ok=True)


def _owned_name(owner_id: str, path: str) -> str:
    """The file name inside this owner's folder for a path save() returned, or "" if it isn't theirs."""
    marker = f"/{owner_id}/"
    name = path.split(marker, 1)[1] if marker in path else ""
    return "" if not name or ".." in name.split("/") else name


def load_path(owner_id: str, path: str) -> bytes | None:
    """Read a file by the path save() returned, only if it is one of this owner's files."""
    name = _owned_name(owner_id, path)
    return load(owner_id, name) if name else None


def delete_path(owner_id: str, path: str) -> None:
    """Delete a file by the path save() returned, only if it is one of this owner's files."""
    marker = f"/{owner_id}/"
    name = path.split(marker, 1)[1] if marker in path else ""
    if not name or ".." in name.split("/"):
        return
    delete(owner_id, name)


def delete_all(owner_id: str) -> None:
    """Remove every file saved for one owner (resetting a demo copy)."""
    if not owner_id or "/" in owner_id or owner_id.startswith("."):
        raise ValueError("not an owner id")
    if settings.media_bucket:
        for blob in _bucket().list_blobs(prefix=f"{owner_id}/"):
            blob.delete()
        return
    shutil.rmtree(_local(owner_id), ignore_errors=True)
