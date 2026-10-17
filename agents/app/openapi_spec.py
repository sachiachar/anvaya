"""The API's OpenAPI description, kept in docs/openapi.yaml.

FastAPI builds the paths and schemas from the code. This adds what it can't see: the groups
(tags), how each route signs in, the servers, and the live-call WebSocket, which OpenAPI can't
describe as a path. A test checks that docs/openapi.yaml matches the code.

Write it again after changing a route:

    cd agents && uv run python -m app.openapi_spec
"""

from __future__ import annotations

import pathlib
import re

import yaml
from fastapi import FastAPI

SPEC_FILE = pathlib.Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"

DESCRIPTION = """\
The Anvaya API: the app, the live call, the handover and the platform admin portal.
All demo data is synthetic.

## Signing in
- Every `/api` route needs a **Firebase ID token** from Google sign-in, as `Authorization: Bearer <token>`.
  Running locally with `AUTH_REQUIRED=false`, no token is needed.
- Each signed-in account works on **its own demo copy**. A route with an `owner_id` refuses one that isn't
  the caller's copy.
- `/api/platform` routes check the caller's **portal role** on the server: viewer, demo admin, budget
  approver, risk reviewer or platform admin. A demo admin sees and decides only their own copy's records.
- `/api/healthz` is open. `/internal/fraud/cycle` takes only the Cloud Scheduler service account's
  Google-signed ID token.

## What the API never returns
- Vault secrets in readable form: they are encrypted on the device, and the server holds only what it can't open.
- Conversation text or names in `/api/platform` routes: people appear as hashed keys.

## Paged lists
The `/api/platform` list routes (`…/events`, `…/entries`, `…/copies` and others) take `page`, `size`
(20 by default, at most 100), `q` (every word must match, in any case), and `from` and `to` dates
(`YYYY-MM-DD`, whole days in UTC). They return `items`, `page` (`page`, `size`, `total`, `pages`) and
`choices`: each filter's values with how many rows have them.

## Errors
Errors come as `{"detail": "..."}`.

| Status | Meaning |
| --- | --- |
| 400 | The request can't be done as asked |
| 401 | No sign-in, or the token isn't valid |
| 403 | The role or the governance gateway refuses it, or the allowance is used up |
| 404 | Not found, or not in the caller's demo copy |
| 409 | It clashes with the current state, for example a second request while one waits |
| 413 | The upload is too large |
| 422 | A field is missing or not valid |
| 502 | Gemini failed, or its answer didn't match the expected format |
| 503 | A cloud service isn't set up or can't be reached |

## The live call (WebSocket)
`GET /api/owners/{owner_id}/live?token=<Firebase ID token>`, upgraded to a WebSocket. Browsers can't
send headers on a WebSocket, so the token comes in the query.

| Direction | Frame | Content |
| --- | --- | --- |
| app → server | binary | 16 kHz mono 16-bit PCM from the microphone |
| app → server | text | `{"type": "end"}` to hang up |
| server → app | binary | 24 kHz mono 16-bit PCM, Saathi's voice |
| server → app | text | `{"type": "ready"}`: the call is connected |
| server → app | text | `{"type": "heard" or "said", "text"}`: live captions |
| server → app | text | `{"type": "interrupted"}`: the owner started talking, so stop playback |
| server → app | text | `{"type": "turn", "owner_turn", "assistant_turn"}`: after each exchange, with things to keep |
| server → app | text | `{"type": "error", "detail"}` |
"""

SERVERS = [
    {"url": "https://anvaya-saathi.web.app", "description": "Production, through Firebase Hosting to Cloud Run"},
    {"url": "http://localhost:8080", "description": "Local development"},
]

SECURITY_SCHEMES = {
    "firebase": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                 "description": "Firebase ID token from Google sign-in."},
    "scheduler": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                  "description": "Google-signed ID token of the Cloud Scheduler service account."},
}

# Each route's group, by its path: the first pattern that matches wins.
TAGS: list[tuple[str, str, str]] = [
    (r"^/api/healthz$", "Service", "Whether the service is up, and how it is set up."),
    (r"^/internal/", "Scheduler", "Work that Cloud Scheduler starts, such as the hourly fraud cycle."),
    (r"^/api/(demo|me|allowance|agents/steps|model-access)", "Your demo copy",
     "The caller's own demo copy: who they are, their allowance, their own model key, and starting again."),
    (r"^/api/owners/[^/]+/voiceprint", "Voice check",
     "The owner's optional voice check. Only numbers that describe the voice are kept, never the recording."),
    (r"^/api/owners/[^/]+/vault", "Vault and family",
     "Choosing who receives the vault, and the vault itself. Secrets arrive already encrypted on the device."),
    (r"^/api/owners/[^/]+/(death-report|release|alive|guardian/held|ask|settlement|access-requests)"
     r"|^/api/admin/|^/api/access-requests/", "Handover",
     "When the time comes: the death report, two reviewers, the waiting period, the release, and the family's questions."),
    (r"^/api/owners/[^/]+/legal-", "Wishes and legal", "Which wishes need a legal document, and the legal partner."),
    (r"^/api/(owners|proposals|tasks|speech)", "Owner",
     "Talking to Saathi, keeping what matters, the plan, gaps and tasks."),
    (r"^/api/platform/(guardian)", "Admin portal: guardian", "Each time the guardian stepped in, and whether it was right."),
    (r"^/api/platform/fraud", "Admin portal: fraud",
     "The fraud reference guides, what the pipeline added, reviews and runs."),
    (r"^/api/platform/(agents|overview)", "Admin portal: agents and use", "The agents, their use, spend and refused calls."),
    (r"^/api/platform/(approvals|demos|model-keys)", "Admin portal: people",
     "Allowance requests, demo copies and people's own model keys."),
    (r"^/api/platform/", "Admin portal: settings and audit",
     "The caller's role, the kill switch, privacy settings and the audit log."),
]

OPEN_PATHS = {"/api/healthz"}


def tag_for(path: str) -> str:
    for pattern, name, _ in TAGS:
        if re.search(pattern, path):
            return name
    raise ValueError(f"no OpenAPI tag matches {path}: add one to TAGS in app/openapi_spec.py")


def build(app: FastAPI) -> dict:
    """The full description: FastAPI's spec with groups, sign-in, servers and the live call."""
    app.openapi_schema = None
    spec = app.openapi()
    spec["info"]["description"] = DESCRIPTION
    spec["servers"] = SERVERS
    spec.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    spec["security"] = [{"firebase": []}]
    used = set()
    for path, operations in spec["paths"].items():
        for op in operations.values():
            op["tags"] = [tag_for(path)]
            used.add(op["tags"][0])
            if path in OPEN_PATHS:
                op["security"] = []
            elif path.startswith("/internal/"):
                op["security"] = [{"scheduler": []}]
    spec["tags"] = [{"name": name, "description": text} for _, name, text in TAGS if name in used]
    # Key order: the header first, then the paths and schemas, as readers expect.
    order = ("openapi", "info", "servers", "security", "tags", "paths", "components")
    return {k: spec[k] for k in order if k in spec} | {k: v for k, v in spec.items() if k not in order}


class _Dumper(yaml.SafeDumper):
    """Writes text with line breaks as a readable block, not one escaped line."""


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _text)


def dump(spec: dict) -> str:
    return yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def main() -> None:
    from app.main import app

    SPEC_FILE.write_text(dump(build(app)))
    print(f"Wrote {SPEC_FILE}")


if __name__ == "__main__":
    main()
