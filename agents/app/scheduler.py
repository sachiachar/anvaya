"""The hourly fraud cycle's link to Cloud Scheduler (fraud_pipeline.cycle).

Cloud Scheduler calls POST /internal/fraud/cycle once an hour with a Google-signed ID token for its
own service account. The run happens inside that request, so Cloud Run keeps the CPU on until it
finishes. "Run now" in the admin portal asks Cloud Scheduler to run the same job at once, since the
portal's own requests pass through Firebase Hosting, which cuts them off after 60 seconds.
"""

from __future__ import annotations

import google.auth
from fastapi import HTTPException
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import id_token

from .config import settings

AUDIENCE = "anvaya-fraud-cycle"  # the ID token's audience: set on the job by the release workflow
API = "https://cloudscheduler.googleapis.com/v1"


def verify(authorization: str, request) -> None:
    """Only the scheduler's own service account may start a cycle. Raises 404 when the cycle isn't
    set up here (so it doesn't reveal itself), 401 for anyone else."""
    if not settings.fraud_scheduler_sa:
        raise HTTPException(404, "Not found")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    try:
        claims = id_token.verify_oauth2_token(token, request, audience=AUDIENCE)
    except Exception:
        raise HTTPException(401, "Not allowed") from None
    if claims.get("email") != settings.fraud_scheduler_sa or not claims.get("email_verified"):
        raise HTTPException(401, "Not allowed")


def run_now() -> None:
    """Ask Cloud Scheduler to run the hourly job at once (the API's own service account may)."""
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    response = AuthorizedSession(credentials).post(f"{API}/{settings.fraud_scheduler_job}:run", timeout=15)
    response.raise_for_status()
