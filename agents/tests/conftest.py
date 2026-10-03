"""Shared test setup: files (recordings, documents and the kept-knowledge bundles) go to a fresh
folder for each test run, never to agents/data/media."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True, scope="session")
def _media_dir(tmp_path_factory):
    original = settings.local_media_dir
    settings.local_media_dir = str(tmp_path_factory.mktemp("media"))
    yield
    settings.local_media_dir = original
