"""Bring your own model key (PR-10.10): a demo user's own Gemini key, with ours as failover.

Anyone who signs in may add their own key, optionally: a Google AI Studio key (free or paid)
or a Vertex AI express-mode key. Every model call caused by that person, whatever the agent,
uses their key first; the platform key (GOOGLE_API_KEY) is used only when theirs fails, or when
they haven't added one. A platform admin can turn this off for a person, or limit the kinds.

How a key stays theirs alone:
- It's found only from the verified sign-in (governance.current_user), never from an ID the
  browser sends, and it's used only for calls that person causes.
- It's encrypted with its own Cloud KMS key, with the person's user key bound in as additional
  authenticated data, so a stored key copied to another person's record won't decrypt.
- It's never sent back to anyone, its owner included: the app and the portal see only the kind,
  the last 4 characters and the status. Key-shaped text is removed from logs and error messages.

The gateway's rules apply whatever key answers: the registry, the limits and the kill switch.
Only the daily allowance isn't counted for calls on a person's own key.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import re
import threading
import time

from google.genai import errors

from ..config import settings
from ..store import store
from . import core

KINDS = ("studio", "vertex_express")
SOURCES = {"studio": "judge_studio", "vertex_express": "judge_vertex"}
PLATFORM = "platform"
ACCESS = "model_access"          # one document per person, by user key
POLICY = "model_access_policy"   # a platform admin's setting for one person, by user key
DEFAULT_SETTING = "model_access_default"
PLATFORM_STATUS = ("model_access_status", "platform")
DEFAULT_POLICY = {"enabled": True, "kinds": list(KINDS)}
DEV_PREFIX = "dev:"
CACHE_SECONDS = 60
KEY_MIN, KEY_MAX = 20, 200
TESTS_PER_MINUTE = 10
# A key that's invalid, revoked, out of quota or can't reach the model: try the next key.
# AI Studio reports an invalid key as 400 "API key not valid"; the live API closes the connection
# (1007, or 1011 for quota) with the same words, so the message counts, whatever the code.
FAILOVER_CODES = (401, 403, 404, 429)
# Google API keys (AI Studio and Vertex AI express mode) start with "AIza".
KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


class NotAllowed(Exception):
    """Adding this key isn't allowed (turned off, a kind that isn't allowed, or not set up)."""


# ---- Keeping keys out of logs and messages ---------------------------------------------

_live_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def redact(text: str | None) -> str:
    """Text with every key we know of, and anything key-shaped, replaced."""
    out = KEY_SHAPE.sub("[key removed]", text or "")
    with _secrets_lock:
        known = list(_live_secrets)
    for secret in known:
        out = out.replace(secret, "[key removed]")
    return out


def _remember(secret: str) -> None:
    with _secrets_lock:
        _live_secrets.add(secret)


def _forget(secret: str) -> None:
    with _cache_lock:
        in_use = any(entry and entry[1] == secret for _, entry in _cache.values())
    if not in_use:
        with _secrets_lock:
            _live_secrets.discard(secret)


@contextlib.contextmanager
def held(secret: str):
    """While a key is being tested, it's removed from anything logged or shown, too."""
    _remember(secret)
    try:
        yield
    finally:
        _forget(secret)


def install_log_redaction() -> None:
    """Every log record, from any library, has keys removed before it's written."""
    old = logging.getLogRecordFactory()
    if getattr(old, "_redacts_keys", False):
        return

    def factory(*args, **kwargs):
        record = old(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            return record
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            # Handlers format tracebacks later, from exc_info; fill in the cleaned text now instead.
            text = logging.Formatter().formatException(record.exc_info)
            clean_text = redact(text)
            if clean_text != text:
                record.exc_text, record.exc_info = clean_text, None
        return record

    factory._redacts_keys = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


# ---- Checking what was typed ----------------------------------------------------------


def clean_key(kind: str, key: str | None) -> str:
    """The key as typed, checked without ever repeating it in an error."""
    if kind not in KINDS:
        raise NotAllowed("Choose AI Studio or Vertex AI express mode.")
    key = (key or "").strip()
    if not KEY_MIN <= len(key) <= KEY_MAX or any(c.isspace() or not c.isprintable() for c in key):
        raise NotAllowed("That doesn't look like an API key. Copy it again from Google AI Studio or the Google Cloud console.")
    return key


def masked(key: str) -> str:
    return "••••" + key[-4:]


# ---- Policy ------------------------------------------------------------------------------


def default_policy() -> dict:
    saved = core.platform_settings().get(DEFAULT_SETTING) or {}
    return {"enabled": bool(saved.get("enabled", DEFAULT_POLICY["enabled"])),
            "kinds": [k for k in saved.get("kinds", DEFAULT_POLICY["kinds"]) if k in KINDS]}


def policy(user: str) -> dict:
    """What this person may do: the default policy, with the admin's setting for them on top."""
    mine = store.get(POLICY, user) or {}
    base = default_policy()
    return {"enabled": bool(mine.get("enabled", base["enabled"])),
            "kinds": [k for k in mine.get("kinds", base["kinds"]) if k in KINDS],
            "set_by_admin": bool(mine)}


def set_policy(user: str, enabled: bool, kinds: list[str]) -> dict:
    store.put(POLICY, {"id": user, "user_key": user, "enabled": enabled,
                       "kinds": [k for k in kinds if k in KINDS], "at": time.time()})
    forget_cached(user)
    return policy(user)


def set_default_policy(enabled: bool, kinds: list[str]) -> dict:
    current = store.get("platform_settings", "settings") or {"id": "settings"}
    store.put("platform_settings", {**current, DEFAULT_SETTING: {
        "enabled": enabled, "kinds": [k for k in kinds if k in KINDS]}})
    with _cache_lock:
        _cache.clear()
    return default_policy()


def check_allowed(user: str, kind: str) -> None:
    p = policy(user)
    if not p["enabled"]:
        raise NotAllowed("Adding your own model key is turned off for your account.")
    if kind not in p["kinds"]:
        raise NotAllowed("This kind of key isn't allowed for your account.")


# ---- Storing keys, encrypted -------------------------------------------------------------


def ready() -> bool:
    """Keys can be stored: a KMS key is set, or this isn't Firestore (local development, tests)."""
    return bool(settings.model_keys_kms_key_name) or settings.store != "firestore"


def _encrypt(user: str, key: str) -> str:
    if not settings.model_keys_kms_key_name:
        if not ready():
            raise NotAllowed("Adding your own key isn't set up yet on this server.")
        return DEV_PREFIX + base64.b64encode(f"{user}\n{key}".encode()).decode()
    from google.cloud import kms

    response = kms.KeyManagementServiceClient().encrypt(request={
        "name": settings.model_keys_kms_key_name, "plaintext": key.encode(),
        "additional_authenticated_data": user.encode()})
    return base64.b64encode(response.ciphertext).decode()


def _decrypt(user: str, blob: str) -> str:
    if blob.startswith(DEV_PREFIX):
        if settings.store == "firestore":  # never trust an unencrypted record in production
            raise ValueError("unencrypted model key in Firestore")
        owner, _, key = base64.b64decode(blob[len(DEV_PREFIX):]).decode().partition("\n")
        if owner != user:  # a key copied to someone else's record is never used
            raise ValueError("model key belongs to someone else")
        return key
    from google.cloud import kms

    response = kms.KeyManagementServiceClient().decrypt(request={
        "name": settings.model_keys_kms_key_name, "ciphertext": base64.b64decode(blob),
        "additional_authenticated_data": user.encode()})
    return response.plaintext.decode()


def public(doc: dict | None) -> dict | None:
    """What anyone may see about a stored key: never the key or its ciphertext."""
    if not doc:
        return None
    return {k: doc.get(k) for k in ("kind", "last4", "status", "features", "tested_at", "saved_at",
                                    "failed_at", "failure")}


def access(user: str) -> dict | None:
    return public(store.get(ACCESS, user))


def save(user: str, kind: str, key: str, features: list[dict]) -> dict:
    check_allowed(user, kind)
    now = time.time()
    store.put(ACCESS, {"id": user, "user_key": user, "kind": kind, "secret": _encrypt(user, key),
                       "last4": masked(key), "status": "working", "features": features,
                       "tested_at": now, "saved_at": now, "failed_at": None, "failure": None})
    forget_cached(user)
    return access(user)


def record_test(user: str, features: list[dict], usable: bool) -> dict | None:
    """A platform admin tested a person's saved key again: keep the result, never the key."""
    if not store.get(ACCESS, user):
        return None
    now = time.time()
    fields = {"features": features, "tested_at": now, "status": "working" if usable else "failing"}
    if not usable:
        fields |= {"failed_at": now, "failure": "test"}
    store.update(ACCESS, user, fields)
    with _cache_lock:
        _status.pop(user, None)
    return access(user)


def remove(user: str) -> None:
    store.delete(ACCESS, user)
    forget_cached(user)


def saved_key(user: str) -> tuple[str, str] | None:
    """(kind, key) of the person's stored key, decrypted, for testing it again. Server only."""
    doc = store.get(ACCESS, user)
    if not doc:
        return None
    key = _decrypt(user, doc["secret"])
    _remember(key)
    return doc["kind"], key


# ---- The key chain the gateway uses -------------------------------------------------------

_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}
_cache_lock = threading.Lock()
_status: dict[str, str] = {}  # last status written for each person, so a status write happens only on change


def forget_cached(user: str) -> None:
    with _cache_lock:
        old = _cache.pop(user, None)
        _status.pop(user, None)
    if old and old[1]:
        _forget(old[1][1])


def own_key(user: str | None) -> tuple[str, str] | None:
    """(kind, key) for this person's own key, if they added one and may use it. Kept in memory
    for a minute, so KMS isn't asked on every call; a change on this server applies at once."""
    if not user:
        return None
    now = time.time()
    with _cache_lock:
        hit = _cache.get(user)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    entry: tuple[str, str] | None = None
    doc = store.get(ACCESS, user)
    if doc:
        p = policy(user)
        if p["enabled"] and doc["kind"] in p["kinds"]:
            try:
                entry = (doc["kind"], _decrypt(user, doc["secret"]))
                _remember(entry[1])
            except Exception:
                logging.getLogger(__name__).warning("A stored model key couldn't be decrypted; using the platform key")
    with _cache_lock:
        _cache[user] = (now, entry)
    return entry


def chain() -> list[tuple[str, str | None, str | None]]:
    """The keys to try, in order: (source, kind, key). The person's own first, then ours. Ours
    is never tried first while they have a key, and is the only one when they don't."""
    own = own_key(core.current_user())
    links: list[tuple[str, str | None, str | None]] = [(SOURCES[own[0]], own[0], own[1])] if own else []
    return links + [(PLATFORM, None, None)]


def fails_over(e: Exception) -> bool:
    """Whether to try the next key: this key is refused, out of quota, or can't reach the model."""
    if not isinstance(e, errors.APIError):
        return False
    return e.code in FAILOVER_CODES or key_refused(e) or out_of_quota(e)


def _words(e: errors.APIError) -> str:
    # The live API's errors carry their text only in str(e); their message is empty.
    return f"{e.message or ''} {e}".lower()


def key_refused(e: errors.APIError) -> bool:
    return "api key" in _words(e)


def out_of_quota(e: errors.APIError) -> bool:
    return e.code == 429 or "quota" in _words(e)


def failed(agent: str, source: str, code: int | None) -> None:
    """One key failed and the call moves on: recorded, without the key."""
    user = core.current_user()
    core.record_blocked(agent, "key_fallback", source, f"Key failed ({code}); trying the next one", user=user)
    now = time.time()
    if user:
        day = core._today()
        store.increment("key_usage", f"{day}:{user}", {source: {"failovers": 1}}, {"day": day, "user_key": user})
    if source == PLATFORM:
        store.put(PLATFORM_STATUS[0], {"id": PLATFORM_STATUS[1], "failed_at": now, "code": code})
        return
    if user and store.get(ACCESS, user):
        store.update(ACCESS, user, {"status": "failing", "failed_at": now, "failure": code})
        with _cache_lock:
            _status[user] = "failing"


def worked(source: str) -> None:
    """A key answered. A person's key that was failing is marked working again."""
    user = core.current_user()
    if source == PLATFORM or not user:
        return
    with _cache_lock:
        known = _status.get(user)
    if known == "working":
        return
    doc = store.get(ACCESS, user)
    if doc and doc.get("status") != "working":
        store.update(ACCESS, user, {"status": "working"})
    with _cache_lock:
        _status[user] = "working"


def platform_status() -> dict:
    return store.get(*PLATFORM_STATUS) or {}


def counts_against_allowance() -> bool:
    """False when the person's own key will answer first: their testing costs us nothing."""
    return own_key(core.current_user()) is None


def client_cache_key(kind: str, key: str) -> str:
    return kind + ":" + hashlib.sha256(key.encode()).hexdigest()


# ---- Testing a key before it's saved ------------------------------------------------------


def allow_test(user: str) -> None:
    """At most 10 tests a minute for each person, so the server can't check keys in bulk."""
    minute = int(time.time() // 60)
    doc_id = f"{minute}:{user}"
    store.increment("model_access_tests", doc_id, {"count": 1}, {"user_key": user, "minute": minute})
    if (store.get("model_access_tests", doc_id) or {}).get("count", 0) > TESTS_PER_MINUTE:
        raise NotAllowed("Too many tests in a minute. Wait a moment and try again.")
