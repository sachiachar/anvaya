# Anvaya: Libraries, Licences and Google Services

Everything Anvaya is built on, with each item's version, licence and purpose. Versions are as of 3 October 2026. The lockfiles (`agents/uv.lock`, `web/package-lock.json`) are the source of truth for exact versions.

**Rule:** use the latest stable release of everything, with permissive licences only. See [AGENTS.md](../AGENTS.md#dependencies-and-versions). Update this page in the same pull request as any dependency change. A test fails if a direct dependency is missing from this page, or if any installed package has a licence outside the allowed list.

## Runtimes and tools

| Item | Version | Licence | Purpose | Pinned in |
| --- | --- | --- | --- | --- |
| Python | 3.14 | PSF-2.0 | Runs the API and the agents | `agents/.python-version`, `agents/pyproject.toml`, `agents/Dockerfile` |
| Node.js | 24 (Active LTS) | MIT | Builds the web app and the admin portal | `web/.nvmrc`, `web/package.json` (`engines`) |
| uv | 0.12.22 | MIT OR Apache-2.0 | Installs Python packages and locks versions | `agents/Dockerfile`; CI uses the latest release that `setup-uv` has verified |
| `python:3.14-slim` | 3.14 | PSF-2.0 (Python), Debian packages under their own free licences | Base image for the API container | `agents/Dockerfile` |

## API libraries (`agents/`)

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| fastapi | 0.141.1 | MIT | HTTP API and the WebSocket for live calls |
| uvicorn[standard] | 0.54.0 | BSD-3-Clause | Server that runs the API on Cloud Run |
| python-multipart | 0.0.32 | Apache-2.0 | Photo and document uploads |
| pydantic | 2.13.5 | MIT | Request and response schemas; structured output from Gemini |
| google-genai | 2.28.0 | Apache-2.0 | Gemini calls: conversation, extraction, documents, spoken replies, live calls |
| google-adk | 2.11.0 | Apache-2.0 | The handover assistant agent (`executor_agent.py`) |
| google-cloud-firestore | 2.34.0 | Apache-2.0 | Data store on Google Cloud (`STORE=firestore`) |
| google-cloud-kms | 3.18.0 | Apache-2.0 | Wraps the vault escrow blob with the KMS key |
| google-cloud-storage | 3.16.0 | Apache-2.0 | Recordings and documents in a private bucket |
| google-auth | 2.59.1 | Apache-2.0 | Verifies the Google sign-in token on every request (`auth.py`) |
| pyyaml | 6.0.3 | MIT | Reads the agent registry (`registry.yaml`) |

**Development only**

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| pytest | 9.1.1 | MIT | Runs the tests |
| httpx2 | 2.13.1 | BSD-3-Clause | HTTP client used by FastAPI's test client |
| httpx | 0.28.1 | BSD-3-Clause | Fallback HTTP client for the test client |
| ruff | 0.16.10 | MIT | Lint |

**Held back by an upstream limit.** These indirect packages aren't at their latest release, because `google-adk` 2.11.0 (its latest release) caps them. They move when ADK raises its limits, and Dependabot opens that pull request.

| Package | Locked | Latest | Held back by |
| --- | --- | --- | --- |
| opentelemetry-api, opentelemetry-sdk | 1.42.1 | 1.45.0 | `google-adk` requires 1.42.1 or lower |
| websockets | 15.0.1 | 17.1 | `google-adk` requires below 16 |
| fastapi | 0.141.1 | 0.142.2 | 0.142 needs OpenTelemetry 1.44 or newer (see above) |
| google-api-core | 2.34.0 | 2.40.0 | 2.40 needs OpenTelemetry 1.44 or newer (see above) |
| pydantic-core | 2.46.5 | 2.49.0 | `pydantic` 2.13.5 (latest) pins this exact version |

**Indirect packages** are MIT, Apache-2.0, BSD, PSF-2.0 or MIT-0, except `certifi` (MPL-2.0), which supplies the trusted certificate list. MPL-2.0 applies file by file, and we use `certifi` unmodified, so it puts no conditions on our code.

## Web libraries (`web/`)

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| react, react-dom | 19.3.0 | MIT | The user interface for the app and the admin portal |
| firebase | 12.19.0 | Apache-2.0 | Google sign-in in the browser (Firebase Authentication) |
| @grpc/grpc-js (override) | 1.14.5 | Apache-2.0 | Not used directly: raises the version `firebase` pulls in, to fix two security alerts |

**Development only**

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| vite | 8.3.2 | MIT | Dev server and production build |
| @vitejs/plugin-react | 6.1.1 | MIT | React support for Vite |
| typescript | 7.0.2 | Apache-2.0 | Type checking |
| @types/react, @types/react-dom | 19.3.0 | MIT | Type definitions for React |
| @biomejs/biome | 2.5.15 | MIT OR Apache-2.0 | Lint |

**Indirect packages** are MIT, Apache-2.0, BSD-3-Clause, ISC or 0BSD, except `lightningcss` (MPL-2.0). It's a CSS tool Vite uses during the build, and none of its code ships to users.

## GitHub Actions

| Action | Version | Licence | Purpose |
| --- | --- | --- | --- |
| actions/checkout | v7 | MIT | Fetches the code in CI and deploys |
| actions/setup-node | v7 | MIT | Installs Node from `web/.nvmrc` |
| astral-sh/setup-uv | v10.2.0 (no major tags since v8) | MIT | Installs uv, and Python from `agents/.python-version` |
| dorny/paths-filter | v4 | MIT | Deploys only the parts that changed |
| google-github-actions/auth | v3 | Apache-2.0 | Signs in to Google Cloud with Workload Identity Federation (no stored keys) |
| google-github-actions/setup-gcloud | v3 | Apache-2.0 | Installs the `gcloud` CLI for deploys |

## Google services

All of these are Google's proprietary managed services, used under the Google Cloud and Firebase terms of service. They are billed pay-as-you-go, with free usage tiers. Nothing is licensed or installed. Everything runs in the project's own Google Cloud account, in `asia-southeast1` (Singapore) where a region applies.

| Service | Purpose | Where in the code |
| --- | --- | --- |
| **Gemini API** (Google AI Studio key) | The models: `gemini-3.1-flash-lite` for conversation, extraction, documents, next steps, legal and handover checks; `gemini-3.5-flash-lite` as the fallback; `gemini-3.8-flash-lite-tts` and two other text-to-speech models for spoken replies; `gemini-3.8-live` for live calls | `agents/app/gemini.py`, `speech.py`, `live.py`, `executor_agent.py`; models in `config.py` and `registry.yaml` |
| **Cloud Run** | Runs the API (`life-after-agents`) | `infra/deploy.sh`, `.github/workflows/deploy.yml` |
| **Firestore** | Stores cases, cards, the ledger and the audit log; clients have no direct access (`firestore.rules`) | `agents/app/store.py` |
| **Cloud Storage** | Private bucket for recordings and documents | `agents/app/media.py` |
| **Cloud KMS** | Key that wraps the vault escrow blob | `agents/app/escrow.py` |
| **Secret Manager** | Holds the Gemini API key for Cloud Run | `infra/deploy.sh` |
| **Cloud Build, Artifact Registry** | Build and store the API container on each deploy | `gcloud run deploy --source` |
| **IAM, Workload Identity Federation** (IAM Credentials, STS) | Least-privilege service accounts; GitHub Actions deploys without stored keys | `infra/setup-github-actions.sh` |
| **Firebase Hosting** | Serves the app (`anvaya-saathi`), the admin portal (`anvaya-admin`) and the old address's redirect | `firebase.json` |
| **Firebase Authentication** | Google sign-in; the server checks the token and the allowlist | `web/src/auth.ts`, `agents/app/auth.py` |
| **Firebase Rules** | Publishes `firestore.rules` | `.github/workflows/deploy.yml` |
| Vertex AI API | Turned on by `infra/deploy.sh` for later, but not used: the prototype release calls the Gemini API with a key, and a test checks this | `infra/deploy.sh` |

**Data use.** On the Gemini API's free tier, Google may use prompts and responses to improve its products. On the paid tier it doesn't. Keep billing on for any environment that handles more than synthetic demo data.
