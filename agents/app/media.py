"""Keep original recordings and documents: Cloud Storage in the cloud, a folder locally."""

from __future__ import annotations

import pathlib
import shutil
import threading

from .config import settings


def save(owner_id: str, name: str, data: bytes, mime_type: str) -> str:
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        from google.cloud import storage

        blob = storage.Client().bucket(settings.media_bucket).blob(path)
        blob.upload_from_string(data, content_type=mime_type)
        return f"gs://{settings.media_bucket}/{path}"
    target = pathlib.Path(settings.local_media_dir) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return str(target)


def load(owner_id: str, name: str) -> bytes | None:
    """Read back a file saved with save(), or None if it isn't there."""
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        from google.cloud import storage

        blob = storage.Client().bucket(settings.media_bucket).blob(path)
        return blob.download_as_bytes() if blob.exists() else None
    target = pathlib.Path(settings.local_media_dir) / path
    return target.read_bytes() if target.exists() else None


_local_lock = threading.Lock()  # makes save_if atomic locally, as Cloud Storage's precondition is


class Changed(Exception):
    """Someone else wrote the file since it was read (save_if)."""


def _bucket():
    from google.cloud import storage

    return storage.Client().bucket(settings.media_bucket)


def load_versioned(owner_id: str, name: str) -> tuple[bytes | None, int]:
    """A file and its version (0 if it isn't there), for a later save_if()."""
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        blob = _bucket().get_blob(path)
        return (blob.download_as_bytes(), blob.generation) if blob else (None, 0)
    target = pathlib.Path(settings.local_media_dir) / path
    return (target.read_bytes(), target.stat().st_mtime_ns) if target.exists() else (None, 0)


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
    root = pathlib.Path(settings.local_media_dir) / owner_id
    return sorted(str(f.relative_to(root)) for f in (root / prefix).rglob("*") if f.is_file()) \
        if (root / prefix).exists() else []


def delete(owner_id: str, name: str) -> None:
    path = f"{owner_id}/{name}"
    if settings.media_bucket:
        blob = _bucket().get_blob(path)
        if blob:
            blob.delete()
        return
    (pathlib.Path(settings.local_media_dir) / path).unlink(missing_ok=True)


def delete_all(owner_id: str) -> None:
    """Remove every file saved for one owner (resetting a demo copy)."""
    if not owner_id or "/" in owner_id or owner_id.startswith("."):
        raise ValueError("not an owner id")
    if settings.media_bucket:
        from google.cloud import storage

        bucket = storage.Client().bucket(settings.media_bucket)
        for blob in bucket.list_blobs(prefix=f"{owner_id}/"):
            blob.delete()
        return
    shutil.rmtree(pathlib.Path(settings.local_media_dir) / owner_id, ignore_errors=True)
