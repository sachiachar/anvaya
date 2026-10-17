"""Both sites send security headers (SEC-6): no other site may frame them, and browsers don't guess
file types or leak full addresses."""

import json
import pathlib

HOSTING = json.loads((pathlib.Path(__file__).resolve().parents[2] / "firebase.json").read_text())["hosting"]


def _headers(site: str) -> dict:
    entry = next(s for s in HOSTING if s["site"] == site)
    rule = next(h for h in entry.get("headers", []) if h["source"] == "**")
    return {h["key"]: h["value"] for h in rule["headers"]}


def test_the_app_and_the_portal_cannot_be_framed_by_another_site():
    for site in ("anvaya-saathi", "anvaya-admin"):
        h = _headers(site)
        assert "frame-ancestors 'none'" in h["Content-Security-Policy"], site
        assert "object-src 'none'" in h["Content-Security-Policy"], site
        assert h["X-Frame-Options"] == "DENY", site
        assert h["X-Content-Type-Options"] == "nosniff", site
        assert h["Referrer-Policy"] == "strict-origin-when-cross-origin", site


def test_only_the_app_may_use_the_microphone():
    assert "microphone=(self)" in _headers("anvaya-saathi")["Permissions-Policy"]
    assert "microphone=()" in _headers("anvaya-admin")["Permissions-Policy"]
    assert _headers("anvaya-admin")["X-Robots-Tag"] == "noindex"
