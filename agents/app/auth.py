"""Sign-in for the demo: Firebase Authentication plus an email allowlist.

The web app signs in with Google (Firebase Auth) and sends the Firebase ID token
with every request: as `Authorization: Bearer <token>` for HTTP, and as
`?token=<token>` for the live-call WebSocket (browsers can't set headers there).

The token is verified here, on the server, so the allowlist can't be bypassed by
calling the API directly.

ALLOWED_EMAILS is a comma-separated list of addresses and/or domains:
    ALLOWED_EMAILS=sachi@example.com, judge@contest.org, @hack2skill.com
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .config import settings

_request = google_requests.Request()


@dataclass
class User:
    uid: str
    email: str
    name: str | None


def allowed(email: str) -> bool:
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
        raise HTTPException(401, "Your sign-in has expired. Please sign in again.")
    email = claims.get("email") or ""
    if not email or not claims.get("email_verified"):
        raise HTTPException(403, "This account has no verified email address.")
    if not allowed(email):
        raise HTTPException(403, f"{email} isn't on the list for this demo. Ask the Anvaya team for access.")
    return User(uid=claims["sub"], email=email, name=claims.get("name"))


async def require_user(request: Request) -> User | None:
    """FastAPI dependency for every /api route (HTTP)."""
    if not settings.auth_required:
        return None
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    user = verify(token)
    request.state.user = user
    return user
