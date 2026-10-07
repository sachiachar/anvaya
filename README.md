<p align="center">
  <img src="docs/assets/anvaya-logo.png" alt="Anvaya" width="200">
</p>

<p align="center"><strong>Your stories, your wishes, and everything your family will need.</strong></p>

**When someone dies suddenly, their family can't find the money.** Anvaya is an app with a friendly AI companion, **Saathi**. While people are alive, they talk to Saathi about their stories, money matters, family and wishes. When they pass away, Anvaya hands everything over safely to the right person.

Built for the **AI Builder Cup 2026** · Track: **BFSI** · Google Gemini + Google Cloud.

> All demo data is synthetic.

## Documents

| Document | What it covers |
| --- | --- |
| [Concept](docs/concept.md) | The idea in plain language (start here) |
| [Pitch: individuals](docs/pitch-individuals.md) | Why a person and their family would use Anvaya |
| [Pitch: banks and insurers](docs/pitch-institutions.md) | Why an institution would offer Anvaya to its customers |
| [Business model](docs/business-model.md) | Revenue, costs, margins and the assumptions behind them |
| [Architecture](docs/architecture.md) | How it's built, security, design decisions |
| [Use cases](docs/use-cases.md) | Who uses it, what they do, what the AI does |
| [Release notes](docs/release-notes.md) | What's in each version and known limitations |
| [Demo script](docs/demo-script.md) | A timed 3-minute demo with the lines to say to Saathi |
| [Libraries and services](docs/dependencies.md) | Every library, version and licence, and the Google services used, with the purpose of each |
| [Brand](docs/brand.md) | The name, taglines by country and language, and the companion's name in each language |
| [Proposal: agent governance](docs/proposals/agent-governance.md) | Agent identity, permissions, budgets and the admin portal (agreed, mostly built) |
| [Proposal: fraud foresight](docs/proposals/fraud-foresight.md) | Imagining and checking fraud before it happens, and learning from kept knowledge without reading it (agreed, partly built) |

## The problem

- **India:** ₹72,454 crore in unclaimed bank deposits (January 2026), plus ₹8,974 crore in unclaimed insurance (February 2026).
- **Japan:** a record ¥129.2 billion in estates went to the national treasury in financial year 2024 because no heir came forward.
- **South Korea:** more than ₩10 trillion in unclaimed insurance money (2026).
- **Malaysia:** RM13.3 billion in unclaimed money (April 2025).
- **Australia:** A$21.2 billion in lost and unclaimed superannuation (June 2026).
- **Singapore:** 2 in 5 CPF members lost before 65 had no nominee (2023).
- Most people never write down their accounts, policies, passwords or wishes.

Sources and dates are in [docs/concept.md](docs/concept.md#sources).

## How it works

1. **Talk to Saathi.** Tap the microphone and talk as in a phone call. You can also type, or share a photo of a document. Saathi suggests cards to keep: stories, advice, wishes, accounts, family.
2. **Next steps.** Saathi shows what the family would still be missing. Each step has a button.
3. **Secure vault.** The phone encrypts the passwords. The owner chooses who receives them.
4. **Careful handover.** After a death, the AI pre-checks the certificate. Then two different reviewers confirm. A waiting period lets the owner cancel.
5. **For the family.** The chosen person unlocks the vault on their phone. They ask Saathi questions, and Saathi answers only from what was recorded, with sources. They also get a claims checklist with draft letters.
6. **Guardian at every step** *(in progress)*. An independent layer watches every agent step for fraud. It corrects the course before anything takes effect (see below).

**AI-first:** every part is a loop that the AI runs, with clear limits.
- Routine work runs on its own.
- Anything saved or shared is only a proposal until a person says yes.
- The AI only assists with death and legal checks. People decide.

## Guardian layer (in progress)

Fraud can enter at any step that an agent runs, not only at the handover. The **guardian** watches every loop. It corrects the course before an action takes effect. Design: [architecture.md §4.1](docs/architecture.md#41-guardian-layer-planned-for-the-prototype-release).

```mermaid
flowchart LR
    observe["Observe"] --> detect["Detect"] --> intervene["Intervene"] --> govern["Govern"]
```

- **Observe:** every loop records an event before it acts. The event has who, action, data without secrets, autonomy level and device.
- **Detect:**
  - fixed rules for hard facts;
  - Gemini detectors for pressure in conversations, probing questions, synthetic audio and prompt injection;
  - fraud reference guides for each document type and country:
    - a research agent keeps them current (Gemini reads web pages that Exa search finds);
    - a dreaming agent red-teams them and also makes a safeguard coverage map.
- **Intervene:** continue, add a check, reduce autonomy one level, pause for a reviewer, or block. The guardian enforces this where actions happen. It is independent of the agents and has no vault access.
- **Govern:** people review every intervention and can reverse it. We measure interventions, false alarms and reviewer patterns.

The guardian never approves or rejects a case. People decide.

## Governance layer and platform admin portal

One governance layer governs every agent. It has these parts:
- the **registry** of what each agent is ([agents/registry.yaml](agents/registry.yaml));
- the **policies** for what each agent may do ([agents/policies.yaml](agents/policies.yaml));
- the **gateway** checks and usage **ledger**;
- the **guardian**'s interventions;
- the **kill switch**;
- platform **roles**;
- the **audit log**.

Code: [agents/app/governance/](agents/app/governance/).

The **platform admin portal** is a separate site for the Anvaya governance team, at https://anvaya-admin.web.app. It covers:
- spend against budget and forecast;
- the agent registry with use per agent, and blocked calls;
- budget approvals;
- guardian interventions (confirm or reverse);
- the fraud coverage map and reference guides;
- judges' demo copies;
- the audit log.

The server enforces the roles: platform admin, budget approver, risk reviewer and viewer. The `PLATFORM_ROLES` secret sets them. Anyone else who can sign in is a read-only viewer.

The portal never shows conversation text or names. Spend (from Google Cloud billing), use and refused calls are always real. Everything else is real too. Budget requests, guardian interventions, fraud scenarios and demo copies appear as those features record them.

Live today:
- every Gemini call names its agent: the conversation, extraction, documents, readiness, legal, certificate and settlement steps, the live call, spoken replies and the ADK handover agent;
- the registry, the kill switch and each person's daily allowance are checked before each call;
- the ledger records every call;
- spend comes only from Google Cloud billing.

## Live now, and coming

What's built is recorded as numbered requirements (P0-1 to P0-11) in the completed [Prototype 0.1 milestone](https://github.com/sachiachar/anvaya/milestone/3?closed=1).

| Live now on https://anvaya-saathi.web.app | Coming in the prototype release (18 October) |
| --- | --- |
| Live voice conversation with Saathi (Gemini Live), typing, photos and documents | Opening the demo to every judge, clean-up of unused copies and a guided tour (PR-1.1, 1.7, 1.9) |
| Suggestion cards with Keep; merging and updates; next steps. Each card is checked against what was said: a detail nobody said stays empty | Guardian layer: two real reviewers, owner alert with cancel, fraud reference guides, fixed checks and a reviewer trace (PR-2) |
| Vault encrypted on the phone; recipient invitation with key code | Claim file for each institution and a Bank view (PR-3) |
| Certificate pre-check by Gemini; two reviewers; waiting period; owner cancel | Accounts discovered and readiness checked at the source: simulated Account Aggregator flow and real statement upload (PR-11) |
| Family's assistant (ADK) reading what the owner kept, in any wording or language, with cited sources; claims checklist and draft letters | Only the owner can reach their conversation; sensitive details masked; retention and deletion (PR-6) |
| Sign-in with Google; access requests | Reminders when accounts go stale, export of the kept knowledge, readiness as link coverage (F-7) |
| Kept knowledge as an open-format (OKF) graph: Keep is the commit, items link to the people they name, Saathi asks gently about what's missing, and a live knowledge map (PR-7) | Fraud research that starts from common gaps in kept knowledge, counted but never read (fraud foresight, step 4) |
| Certificate of passing checked sign by sign against a fraud reference guide; "Dream now" and "Research now" in the admin portal, checked against web sources (PR-2.3, 2.5, 2.6) | The guardian core: every step recorded, set responses, enforced where actions happen (PR-2.7) |
| A private demo for each person who signs in, with switching between Vikram, Priya, two reviewers and the platform admin, checked on the server (PR-1) | AI quality measured on a synthetic test set (PR-8); live "inside the agents" panel (PR-9) |
| Agent gateway, limits per request, daily allowances, "ask for more" with approval, costs from Google Cloud billing, kill switch (PR-10) | Backstops and Model Armor (PR-10.5, 10.8) |
| Platform admin portal with real data only, on desktop and phone; each judge is the platform admin of their own demo copy (PR-12) | The guardian core (PR-2.7) |

## Prototype release plan

The [Prototype release milestone](https://github.com/sachiachar/anvaya/milestone/1) tracks this plan. It is due on 18 October 2026. Each issue has a tier, set against the judging criteria:
- **must-have:** the submission fails without it;
- **high-value:** the highest score per day;
- **trimmed:** kept at reduced scope.

| ID | Requirement | Tier | Issue |
| --- | --- | --- | --- |
| PR-1 | **Mostly built:** each judge gets a private demo and can switch between its people. Still to come: open sign-in, 30-day clean-up, guided tour | Must-have | [#1](https://github.com/sachiachar/anvaya/issues/1) |
| PR-5 | Submission package for the judges | Must-have | [#3](https://github.com/sachiachar/anvaya/issues/3) |
| PR-6 | Secure each owner's conversation (prototype: owner-only access, masking, retention) | Must-have | [#17](https://github.com/sachiachar/anvaya/issues/17) |
| PR-2 | Guardian layer: catch fraud at every agent step (PR-2.1 to PR-2.8, PR-2.10). Built so far: two different reviewers inside the demo (PR-2.1), the India certificate guide (PR-2.3), and dreaming and research checked against web sources (PR-2.5, 2.6) | High-value / trimmed | [#2](https://github.com/sachiachar/anvaya/issues/2) |
| PR-3 | Claim file for banks and insurers, with a Bank view | High-value | [#10](https://github.com/sachiachar/anvaya/issues/10) |
| PR-7 | **Built:** kept knowledge as an OKF graph; Keep is the commit; typed links; the family's assistant reads it; gaps drive the conversation; a live knowledge map | High-value | [#18](https://github.com/sachiachar/anvaya/issues/18) |
| PR-8 | Measure AI quality on a synthetic test set and publish the numbers | High-value | [#22](https://github.com/sachiachar/anvaya/issues/22) |
| PR-9 | Inside the agents: a live panel for judges | High-value | [#23](https://github.com/sachiachar/anvaya/issues/23) |
| PR-12 | **Built:** platform admin portal: governance dashboard, approvals, guardian and fraud operations, demo management; judges approve their own copy's requests | High-value | [#42](https://github.com/sachiachar/anvaya/issues/42) |
| PR-11 | Connect to financial institutions (Account Aggregator, statements) and check each account's readiness for handover | High-value | [#41](https://github.com/sachiachar/anvaya/issues/41) |
| PR-10 | **Mostly built:** agent governance: gateway, registry, limits, allowances, costs from billing ([proposal](docs/proposals/agent-governance.md)). Still to come: backstops, Model Armor | High-value | [#24](https://github.com/sachiachar/anvaya/issues/24) |

We moved these to the [Grand finale milestone](https://github.com/sachiachar/anvaya/milestone/2) (4 December):
- real recordings (#11);
- signs of pressure (#14, #20);
- insider collusion (#16, #19);
- voice letters (#21);
- cloned-voice signals (#25);
- cooling-off delay (#26);
- per-tenant buckets and keys (#27);
- OKF export and coverage map (#28);
- governance for a bank pilot (#29).

## Architecture

```mermaid
flowchart LR
    subgraph phone ["Phone"]
        app["Web app on Firebase Hosting"]
        crypto["Vault encryption on the device"]
    end
    app -->|"HTTPS and live call"| api["Cloud Run: FastAPI + ADK agents"]
    app --- crypto
    api --> gemini["Gemini: chat, Live voice, TTS"]
    api --> firestore[("Firestore")]
    api --> storage[("Cloud Storage")]
    api --> kms["Cloud KMS: vault escrow key"]
```

Details: [docs/architecture.md](docs/architecture.md).

## Repository layout

```
agents/                 Python service (Cloud Run)
  app/main.py           HTTP API and live-call WebSocket (all under /api)
  app/loops.py          Gemini steps for each loop, Saathi persona, reply checks
  app/live.py           Live voice calls (Gemini Live)
  app/proposals.py      Suggestion cards: merging, updates, family
  app/tasks.py          Next steps
  app/release.py        Vault release rules (two reviewers, waiting period)
  app/auth.py           Google sign-in and the email allowlist
  tests/                Automated tests (Gemini stubbed)
web/                    React installable web app (Firebase Hosting)
  src/v2/               The app's screens: Talk, Kept, Family, Vault, Priya's side, our team
  src/vaultCrypto.ts    On-device encryption and recovery codes
  src/admin/            The platform admin portal
infra/deploy.sh         One-command Google Cloud deploy
docs/                   Concept, architecture, use cases, release notes
```

## Run locally

You need Python 3.14, [uv](https://docs.astral.sh/uv/), Node 24 and a Gemini API key from [Google AI Studio](https://aistudio.google.com).

```bash
# 1. Agents API on :8080
cd agents
cp .env.example .env        # add GOOGLE_API_KEY, ALLOWED_EMAILS, FIREBASE_PROJECT_ID
uv sync
uv run --env-file .env uvicorn app.main:app --reload --port 8080

# 2. Web app on :5173
cd ../web
cp .env.example .env        # Firebase web config: firebase apps:sdkconfig web
npm install
npm run dev
```

1. Open http://localhost:5173.
2. Sign in with an allowed Google account.
3. Use the **Demo** button at the top right to switch people: **Vikram** (owner), **Priya** (family), **Anjali** and **Rahul** (our team).

To run locally without sign-in, set `AUTH_REQUIRED=false` and leave `web/.env` out.

Tests (no cloud credentials needed):

```bash
cd agents && uv run --group dev pytest -q
```

## Deploy to Google Cloud

You need:
- `gcloud` and the Firebase CLI;
- Firebase added to the project;
- Google sign-in enabled in Firebase Authentication.

```bash
PROJECT_ID=your-project ALLOWED_EMAILS="you@example.com,@yourteam.com" ./infra/deploy.sh
```

The app is live at **https://anvaya-saathi.web.app**. The old address, life-after-509907.web.app, redirects there. `firebase.json` names both Hosting sites, so a new project needs its own site IDs there.

`infra/deploy.sh` does three things:
1. It sets up the service account, Firestore, a private bucket, a KMS key and the Gemini key in Secret Manager.
2. It deploys the API to Cloud Run in `asia-southeast1` (Singapore).
3. It deploys the web app to Firebase Hosting.

### Automatic deploys (GitHub Actions)

Every push to `main` deploys **what changed since each part last deployed** ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)). So if GitHub cancels a release because a newer merge arrived, no changes are lost.

| Changed since that part's last successful deploy | Deployed |
| --- | --- |
| `agents/**` | Tests, then the API to Cloud Run |
| `web/**`, `firebase.json`, `firestore.rules` | The web app to Firebase Hosting, and the Firestore rules |
| Docs only | Nothing |

**Who can use the demo** is kept in GitHub: *Settings → Environments → production → secret `ALLOWED_EMAILS`* (emails and/or `@domains`, comma-separated). Every API deploy applies it. To apply a change at once, run **Actions → Deploy → Run workflow → allowlist**.

**Actions → Deploy → Run workflow → all** deploys everything.

GitHub signs in to Google Cloud with Workload Identity Federation, so GitHub stores no keys. Google gives those credentials only to the Deploy workflow that runs on `main` in the `production` environment. The deployer can only do what the deploy needs.

Run this setup once, and again after it changes: `PROJECT_ID=… REPO=owner/repo ./infra/setup-github-actions.sh`. It needs a project owner and a repository admin.

## Demo in three minutes

Full script with lines to say: [docs/demo-script.md](docs/demo-script.md).


1. **Vikram → Talk:** tap **Talk to Saathi**. Talk about family, a first job, and an LIC policy ("make Ajay the nominee"). Tap **End call**. Saathi sums it up and marks everything to save. Tap **Save**. Open **Kept** to see the book and the pages still to write.
2. **Vikram → Family:** choose Priya and invite her. **Priya** (Demo menu): accept and keep the recovery code. **Vikram:** check that the codes match, then seal. **Vault:** lock a secret.
3. **Priya:** start the handover with a certificate. **Anjali**, then **Rahul**: two different reviewers confirm. To go against a "not valid" check, a reviewer must write a reason.
4. After the waiting period, **Priya** opens the vault. She asks "What insurance did Papa have?" and prepares the claim letters. Optional: open **Demo → Open the knowledge map** in a second window. It shows each item appear as it is kept.
