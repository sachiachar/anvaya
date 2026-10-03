# Contributing to Anvaya

## Set up

- **API** (`agents/`): Python 3.12+ and [uv](https://docs.astral.sh/uv/). `cd agents && uv sync`.
- **Web app and admin portal** (`web/`): Node 20+. `cd web && npm install`.
- **Git hooks**, once per clone: `git config core.hooksPath .githooks`. This refuses pushes to `main` on your machine.

See the README for running locally.

## How a change gets in

1. **Branch from `main`:** `git switch -c feat/short-name` (or `fix/…`, `chore/…`, `docs/…`).
2. **Make the change, with tests.**
   - `cd agents && uv run --group dev pytest -q`
   - `cd web && npm run build`
3. **Open a pull request.** Fill in the template, link the roadmap item (`PR-x.y`) or issue, and say how you checked it.
4. **Review.** CI runs the API tests and the web build. Areas listed in [.github/CODEOWNERS](.github/CODEOWNERS) need an owner's approval: the vault, the handover, sign-in, governance, `firestore.rules`, hosting and deploys.
5. **Merge.** A person merges to `main`. **Every push to `main` deploys to production** ([deploy.yml](.github/workflows/deploy.yml)), and deploys only what changed. Never push to `main` directly, and never deploy by hand except through Actions → Deploy.

## Rules that keep the product safe

- **Every Gemini call goes through the governance gateway.**
  - Name the agent (`agent="…"`), and register it in [agents/registry.yaml](agents/registry.yaml) with its role, autonomy, allowed models and tools, and limits.
  - A test fails if any code calls Gemini outside the gateway.
  - Registry changes need an owner's review.
- **People decide.** Nothing automated approves a release, saves to the archive without the owner's Keep, or shares with a partner without consent.
- **No conversation text or names** in the platform admin portal or in logs. Users appear as hashed keys.
- **Secrets never go in the repository.** `.env` files stay local. Configuration lives in GitHub environments and Secret Manager.
- **Demo data is synthetic.** Never use real personal data.
- **The prototype release uses the Gemini API key path, not Vertex AI.**

## Writing

- **Docs** use plain English: short sentences, active voice, no jargon.
- **Pitches for people and institutions** (`docs/concept.md`, `docs/pitch-*.md`) are warm, and talk about a service, not technology:
  - they never use the word "AI";
  - they never say someone "died": use "left us", "no longer here" or "when the time comes".
- **The judges' material** (README, deck) names Gemini and Google Cloud openly.
- **Issues** use numbered requirements (`PR-x.y.z`) with a plain-words description and "How we check it".
