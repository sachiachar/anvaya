"""Sign-in for the demo: Firebase Authentication plus an email allowlist.

The web app signs in with Google (Firebase Auth) and sends the Firebase ID token
with every request: as `Authorization: Bearer <token>` for HTTP, and as
`?token=<token>` for the live-call WebSocket (browsers can't set headers there).

The token is verified here, on the server, so the allowlist can't be bypassed by
calling the API directly.

ALLOWED_EMAILS is a comma-separated list of addresses and/or domains:
    ALLOWED_EMAILS=sachi@example.com, judge@contest.org, @hack2skill.com

OPEN_SIGN_IN=true lets in any Google account with a verified email, for judging (PR-15.2).
Each account still gets only its own demo copy (demo.py).
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import demo
from .config import settings
from .governance import core as governance



class _CachedRequest:
    """Google's sign-in certificates, kept until they expire. A plain Request fetches them on every
    check, which put a round trip to Google in front of every API call."""

    def __init__(self) -> None:
        self._http = google_requests.Request()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if method != "GET" or body is not None:
            return self._http(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        with self._lock:
            expires, response = self._cache.get(url, (0.0, None))
            if response is not None and time.time() < expires:
                return response
            response = self._http(url, method=method, headers=headers, timeout=timeout, **kwargs)
            if response.status == 200:
                match = re.search(r"max-age=(\d+)", response.headers.get("cache-control", ""))
                self._cache[url] = (time.time() + (int(match.group(1)) if match else 300), response)
            return response


_request = _CachedRequest()


@dataclass
class User:
    uid: str
    email: str
    name: str | None


def allowed(email: str) -> bool:
    if settings.open_sign_in:
        return True
    email = email.strip().lower()
    for entry in settings.allowed_emails:
        if entry.startswith("@") and email.endswith(entry):
            return True
        if email == entry:
            return True
    return False


def verify(token: str | None) -> User:
    """Check a Firebase ID token and the allowlist. Raises 401 (not signed in) or 403 (not on the list)."""
    if not token:
        raise HTTPException(401, "Please sign in.")
    try:
        claims = id_token.verify_firebase_token(token, _request, audience=settings.firebase_project_id)
    except Exception:
        raise HTTPException(401, "Your sign-in has expired. Please sign in again.") from None
    email = claims.get("email") or ""
    if not email or not claims.get("email_verified"):
        raise HTTPException(403, "This account has no verified email address.")
    if not allowed(email):
        raise HTTPException(403, f"{email} isn't on the list for this demo. Ask the Anvaya team for access.")
    return User(uid=claims["sub"], email=email, name=claims.get("name"))


async def require_user(request: Request) -> User | None:
    """FastAPI dependency for every /api route (HTTP). It also tells the governance gateway
    who is asking, so each person's daily allowance is counted (PR-10.4), and finds or creates
    the person's own demo copy (PR-1)."""
    if not settings.auth_required:
        governance.set_current_user(governance.user_key(demo.LOCAL))
        demo.begin(demo.LOCAL)
        return None
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    user = await asyncio.to_thread(verify, token)  # off the event loop, so other requests keep moving
    request.state.user = user
    governance.set_current_user(governance.user_key(user.uid))
    demo.begin(user.uid)
    return user
