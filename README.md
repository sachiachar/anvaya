<p align="center">
  <img src="docs/assets/anvaya-logo.png" alt="Anvaya" width="200">
</p>

<p align="center"><strong>Your stories, your wishes, and everything your family will need.</strong></p>

**When someone leaves us suddenly, their family often can't find the money they left behind.** In India alone, ₹72,454 crore sits unclaimed in bank deposits. Anvaya gives everyone a warm AI companion, **Saathi**. People talk to Saathi about their accounts, nominees, wishes and stories. When the time comes, Anvaya hands everything over, safely, to the person they chose.

**AI Builder Cup 2026** · BFSI track · built on **Gemini** and **Google Cloud** · all demo data is synthetic.

## Try it

| | |
| --- | --- |
| **The app** | **https://anvaya-saathi.web.app**: sign in with any Google account. You get your own private demo copy. |
| **The admin portal** | **https://anvaya-admin.web.app**: same account. See every agent live, the guardian and the fraud knowledge graph. |
| **Start here** | A five-step tour opens on your first visit. Switch people with the row of names: **Vikram** (owner), **Priya** (daughter), **Anjali** and **Rahul** (reviewers), **Admin**. |

**A five-minute walkthrough**

1. **Vikram** talks to Saathi about his family, his first job and an LIC policy. He taps **Keep**, then opens **Kept** to see his book and its readiness score.
2. **Vikram** chooses Priya to receive his vault. Priya accepts, the codes match, and he seals it.
3. **Priya** uploads the specimen certificate. **Anjali**, then **Rahul**, confirm it.
4. After the waiting period, **Priya** opens the vault and asks *"What insurance did Papa have?"*
5. In the **admin portal**, the Agents page shows the loops that just ran.

## One smart agent, four pillars

| | What it does | See it |
| --- | --- | --- |
| **Smart agent** | Saathi talks by voice or text in your language, plans the conversation, finds the gap that matters most, and asks once. Nothing is kept until you tap **Keep**. | The app, as Vikram |
| **Fraud prevention** | A guardian checks every step. Every certificate is checked for known signs of forgery. Every hour, the service researches new frauds and imagines new attacks on its own safeguards. | Portal: Guardian and Fraud pages |
| **Quick closure** | Two different reviewers, a waiting period with one-tap cancel, then the vault, the family's answers with sources, a checklist and a draft letter for each institution. | The app, as Priya, Anjali and Rahul |
| **Customer retention** | Each heir starts their own book. Inside a bank's app, one customer becomes generations. The bank side is a design for our first partners. | [Pitch: banks and insurers](docs/pitch-institutions.md) |

## What's new here

- **A companion, not a form.** One conversation plan decides what to talk about, and in what tone. Saathi waits on wishes until you're ready.
- **A guardian over every loop.** Every AI step is a loop with a set autonomy level. The guardian can add a check, steer to a safer route, pause or block, and people decide.
- **Fraud foresight.** A "dreaming" agent imagines new attacks on our safeguards every hour. A new tactic is added only when two independent sources confirm it.
- **A vault even we can't open.** Secrets are encrypted on the phone, for one chosen person.
- **Open memory.** What people keep is stored in an open, portable format, with a history of every change.

## How it uses Gemini and Google Cloud

| | |
| --- | --- |
| **Gemini 3.1 Flash-Lite** | Saathi's replies, cards, certificate checks, next steps and letters |
| **Gemini Live** | Hands-free voice calls in the owner's language |
| **Gemini 3.8 Flash** | Hourly fraud research, and "dreaming" up new attacks on our safeguards |
| **Gemini TTS, Google ADK** | Spoken replies; the family's assistant, which answers only from what was kept, with sources |
| **Google Cloud** | Cloud Run, Firebase, Firestore, Cloud Storage, Cloud KMS, Secret Manager, Cloud Scheduler |

**People decide.** Nothing is kept without the owner's **Keep**, and nothing is released without two reviewers and a waiting period. 15 registered agents work within budgets, and every Gemini call passes one gateway.

![The admin portal's Agents page: every loop around the guardian](docs/assets/admin-agents-map.png)

## In numbers

| | |
| --- | --- |
| Forged certificates flagged | 7 of 7 |
| Family answers citing the right source | 14 of 14, with 0 made-up sources |
| True details kept on cards | 23 of 23 |
| Automated tests | 803, with no cloud needed |

Measured on a small synthetic test set, 9 October 2026. Details and limitations: [agents/evals/results/](agents/evals/results/).

## Find your way around

| Read | What it covers |
| --- | --- |
| [Concept](docs/concept.md) | The idea, in a few minutes (start here) |
| [Pitch: individuals](docs/pitch-individuals.md) | Why a person and their family would use Anvaya |
| [Pitch: banks and insurers](docs/pitch-institutions.md) | Why an institution would offer it |
| [Architecture](docs/architecture.md) | How it is built, and how it stays safe |
| [Release notes](docs/release-notes.md) | What runs today, and the known limitations |

| Code | What is there |
| --- | --- |
| [`agents/app/`](agents/app/) | The API and every agent (Python, FastAPI, Gemini) |
| [`agents/app/governance/`](agents/app/governance/) | The guardian, the gateway and the admin API |
| [`agents/registry.yaml`](agents/registry.yaml), [`policies.yaml`](agents/policies.yaml) | Each agent, its models, and what it may do |
| [`web/src/`](web/src/) | The app (React, TypeScript), with the vault's encryption on the phone |
| [`web/src/admin/`](web/src/admin/) | The admin portal |
| [`agents/tests/`](agents/tests/), [`agents/evals/`](agents/evals/) | Automated tests, and the quality measures |

More: [limits](docs/limits.md), [API specification](docs/openapi.yaml), [libraries and services](docs/dependencies.md).

## Run it yourself

You need Python 3.14, [uv](https://docs.astral.sh/uv/), Node 24 and a Gemini API key from [Google AI Studio](https://aistudio.google.com).

```bash
cd agents && cp .env.example .env    # add GOOGLE_API_KEY
uv sync && AUTH_REQUIRED=false uv run --env-file .env uvicorn app.main:app --port 8080
cd ../web && npm install && npm run dev    # http://localhost:5173 (admin: /admin.html)
```

Tests: `cd agents && uv run --group dev pytest -q`. Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).
