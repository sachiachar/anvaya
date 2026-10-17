# Anvaya: Libraries, Licences and Google Services

This page lists everything that Anvaya is built on, with the version, licence and purpose of each item. Versions are as of 3 October 2026. The lockfiles (`agents/uv.lock`, `web/package-lock.json`) are the source of truth for exact versions.

**Rule:** use the latest stable release of everything. Use permissive licences only. See [AGENTS.md](../AGENTS.md#dependencies-and-versions). Update this page in the same pull request as any dependency change. A test fails in two cases:

- a direct dependency is missing from this page;
- an installed package has a licence outside the allowed list.

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
| httpx | 0.28.1 | BSD-3-Clause | Calls Exa's search API for fraud research (`web_search.py`) and the speaker model for the voice check (`voiceprint.py`); also the test client's fallback |
| google-cloud-bigquery | 3.46.1 | Apache-2.0 | Reads actual costs from the Cloud Billing export (admin portal) |
| google-cloud-firestore | 2.34.0 | Apache-2.0 | Data store on Google Cloud (`STORE=firestore`) |
| google-cloud-kms | 3.18.0 | Apache-2.0 | Wraps the vault escrow blob with the KMS key, and encrypts the model keys people add themselves (PR-10.10) |
| google-cloud-storage | 3.16.0 | Apache-2.0 | Recordings and documents in a private bucket |
| google-auth | 2.59.1 | Apache-2.0 | Verifies the Google sign-in token on every request (`auth.py`) |
| pyyaml | 6.0.3 | MIT | Reads the agent registry (`registry.yaml`), and writes and reads the frontmatter of kept-knowledge concepts (OKF) |

**Development only**

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| pytest | 9.1.1 | MIT | Runs the tests |
| httpx2 | 2.13.1 | BSD-3-Clause | HTTP client used by FastAPI's test client |
| ruff | 0.16.10 | MIT | Lint |

**Held back by an upstream limit.** These indirect packages aren't at their latest release. The cause is that `google-adk` 2.11.0 (its latest release) caps them. They move when ADK raises its limits. Dependabot opens that pull request.

Dependabot skips FastAPI 0.142 and later (`ignore` in `.github/dependabot.yml`). Without this rule, its weekly update fails on this conflict. When an ADK update lifts the OpenTelemetry cap, a test (`test_dependabot_ignores_only_what_is_still_held_back`) fails. Then remove the ignore rule in that pull request, so that FastAPI updates again.

| Package | Locked | Latest | Held back by |
| --- | --- | --- | --- |
| opentelemetry-api, opentelemetry-sdk | 1.42.1 | 1.45.0 | `google-adk` requires 1.42.1 or lower |
| websockets | 15.0.1 | 17.1 | `google-adk` requires below 16 |
| fastapi | 0.141.1 | 0.142.2 | 0.142 needs OpenTelemetry 1.44 or newer (see above) |
| google-api-core | 2.34.0 | 2.40.0 | 2.40 needs OpenTelemetry 1.44 or newer (see above) |
| pydantic-core | 2.46.5 | 2.49.0 | `pydantic` 2.13.5 (latest) pins this exact version |

**Indirect packages** are MIT, Apache-2.0, BSD, PSF-2.0 or MIT-0. The one exception is `certifi` (MPL-2.0), which supplies the trusted certificate list. MPL-2.0 applies file by file, and we use `certifi` unmodified. So it puts no conditions on our code.

## Web libraries (`web/`)

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| react, react-dom | 19.3.0 | MIT | The user interface for the app and the admin portal |
| firebase | 12.19.0 | Apache-2.0 | Google sign-in in the browser (Firebase Authentication) |
| echarts | 6.1.0 | Apache-2.0 (zrender BSD-3-Clause, tslib 0BSD) | Interactive usage charts on the admin portal's dashboard; only bar and line charts are loaded, and only on that page |
| @grpc/grpc-js (override) | 1.14.5 | Apache-2.0 | Not used directly: raises the version `firebase` pulls in, to fix two security alerts |

**Development only**

| Library | Version | Licence | Purpose |
| --- | --- | --- | --- |
| vite | 8.3.2 | MIT | Dev server and production build |
| @vitejs/plugin-react | 6.1.1 | MIT | React support for Vite |
| typescript | 7.0.2 | Apache-2.0 | Type checking |
| @types/react, @types/react-dom | 19.3.0 | MIT | Type definitions for React |
| @biomejs/biome | 2.5.15 | MIT OR Apache-2.0 | Lint |

**Indirect packages** are MIT, Apache-2.0, BSD-3-Clause, ISC or 0BSD. The one exception is `lightningcss` (MPL-2.0). It's a CSS tool that Vite uses during the build. None of its code ships to users.

## Fonts (`web/public/fonts/`)

These are the brand typefaces. Our own site serves them, so no visitor's address goes to a font service. The app loads them. The admin portal doesn't. They come from the Fontsource builds of the Google Fonts releases, as woff2 files.

| Font | Version | Licence | Purpose |
| --- | --- | --- | --- |
| lora | 5.3.0 (Fontsource) | OFL-1.1 | Headings and Saathi's words: the brand's warm serif. Latin and Latin Extended, upright and italic |
| inter | 5.3.0 (Fontsource) | OFL-1.1 | The interface: the brand's sans-serif. Latin and Latin Extended |
| mukta | 5.3.0 (Fontsource) | OFL-1.1 | Hindi, Marathi and other Devanagari text, regular and semibold |

**Licence exception, for font files only:** the SIL Open Font License 1.1 lets anyone use, embed and share the fonts, also in commercial products. It has two conditions:

- the licence travels with the font files;
- nobody sells the fonts on their own.

Each font's folder has its `OFL.txt`. A test checks that every font folder has one, and a row in this table. OFL is not allowed for code.

## GitHub Actions

| Action | Version | Licence | Purpose |
| --- | --- | --- | --- |
| actions/checkout | v7 | MIT | Fetches the code in CI and deploys |
| actions/setup-node | v7 | MIT | Installs Node from `web/.nvmrc` |
| astral-sh/setup-uv | v10.2.0 (no major tags since v8) | MIT | Installs uv, and Python from `agents/.python-version` |
| google-github-actions/auth | v3 | Apache-2.0 | Signs in to Google Cloud with Workload Identity Federation (no stored keys) |
| google-github-actions/setup-gcloud | v3 | Apache-2.0 | Installs the `gcloud` CLI for deploys |

## Google services

All of these are Google's proprietary managed services. We use them under the Google Cloud and Firebase terms of service. Google bills them pay-as-you-go, with free usage tiers. Nothing is licensed or installed. Everything runs in the project's own Google Cloud account. Where a region applies, it is `asia-southeast1` (Singapore).

| Service | Purpose | Where in the code |
| --- | --- | --- |
| **Gemini API** (Google AI Studio key) | The models: `gemini-3.1-flash-lite` for conversation, extraction, documents, next steps, legal and handover checks; `gemini-3.5-flash-lite` as the fallback; `gemini-3.8-flash-lite-tts` and two other text-to-speech models for spoken replies; `gemini-3.8-live` for live calls | `agents/app/gemini.py`, `speech.py`, `live.py`, `executor_agent.py`; models in `config.py` and `registry.yaml` |
| **Cloud Run** | Runs the API (`life-after-agents`) | `infra/deploy.sh`, `.github/workflows/deploy.yml` |
| **Firestore** | Stores cases, cards, the ledger and the audit log; clients have no direct access (`firestore.rules`) | `agents/app/store.py` |
| **Cloud Storage** | Private bucket for recordings and documents | `agents/app/media.py` |
| **Cloud Billing export to BigQuery** | Actual spend for the admin portal, read from the detailed usage cost export (dataset `anvaya_platform`, set by `BILLING_EXPORT_TABLE`) | `agents/app/governance/billing.py` |
| **Cloud Scheduler** | Calls the hourly fraud cycle (research, then dreaming) as its own service account, `fraud-scheduler`, with an ID token the API checks. The run happens inside that call, so Cloud Run keeps the CPU on. The release workflow keeps the job (`fraud-foresight-hourly`); "Run now" in the admin portal asks it to run at once | `agents/app/scheduler.py`, the release workflow, `infra/setup-github-actions.sh` |
| **Cloud Billing Budget API** | A monthly budget alert by email at 50%, 90% and 100% of spend, and at a forecast of 100%: a backstop to the daily service ceiling (PR-15.1). A person sets it up once | `infra/budget-alert.sh` |
| **Cloud KMS** | Two keys: `vault-escrow` wraps the vault escrow blob; `model-keys` encrypts the Gemini keys people add themselves (PR-10.10), with the person's user key bound in | `agents/app/escrow.py`, `agents/app/governance/keys.py` |
| **Secret Manager** | Holds the Gemini API key for Cloud Run | `infra/deploy.sh` |
| **Cloud Build, Artifact Registry** | Build and store the API container on each deploy | `gcloud run deploy --source` |
| **IAM, Workload Identity Federation** (IAM Credentials, STS) | Least-privilege service accounts; GitHub Actions deploys without stored keys | `infra/setup-github-actions.sh` |
| **Firebase Hosting** | Serves the app (`anvaya-saathi`), the admin portal (`anvaya-admin`) and the old address's redirect | `firebase.json` |
| **Firebase Authentication** | Google sign-in; the server checks the token and the allowlist | `web/src/auth.ts`, `agents/app/auth.py` |
| **Firebase Rules** | Publishes `firestore.rules` | `.github/workflows/deploy.yml` |
| Vertex AI API | The platform doesn't use it: the prototype release calls the Gemini API with a key, and a test checks this. Only a person's own Vertex AI express-mode key (PR-10.10) reaches it, for the calls they cause, billed to them | `agents/app/gemini.py` (`make_client`), `infra/deploy.sh` |

## Outside services

These are services from other companies. Each one needs the product owner's agreement before we add it.

| Service | Purpose | What's sent | Key | Where in the code |
| --- | --- | --- | --- | --- |
| **Exa** (exa.ai), search API | Web search for the fraud research agent: finds pages that report fraud tactics, so a scenario or tactic can be checked against a quoted source. Replaced Google Search grounding on 3 October 2026, whose quota the free tier didn't cover. Paid per search, with free starting credit. | Fraud research queries only: a document type, a country, a claimed tactic. Never owner data (a test checks this). | `EXA_API_KEY`: the GitHub secret of that name (environment "production"), set on Cloud Run with each API deploy | `agents/app/web_search.py` |
| **Speaker model** (`spkrec-wasm` on Akamai Functions), a team member's demo deployment | The voice check (voice fingerprinting): turns a recording into a speaker embedding. It runs SpeechBrain's `spkrec-ecapa-voxceleb` (ECAPA-TDNN, Apache-2.0), quantised to int8, as WebAssembly. Agreed by the product owner for the demo. | Up to 5 seconds of the owner's speech as a WAV file, with no name or ID. The service may keep what it computes from it. | None: the demo deployment has no secret. `VOICE_MODEL_URL` sets its address (on Cloud Run, from the repository variable of the same name, by the deploy); empty turns the check off | `agents/app/voiceprint.py` |

**Data use.** On the Gemini API's free tier, Google may use prompts and responses to improve its products. On the paid tier, it doesn't. Keep billing on for any environment that handles more than synthetic demo data.
