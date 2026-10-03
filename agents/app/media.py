"""Keep original recordings and documents: Cloud Storage in the cloud, a folder locally."""

from __future__ import annotations

import pathlib

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
