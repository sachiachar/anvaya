# Working on Anvaya (for coding agents)

These are the rules for any coding agent working in this repo: Claude Code, Codex, Cursor, Copilot, Gemini CLI and others. People follow the same rules; see [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

Anvaya is an app with an AI companion, Saathi, for capturing a person's stories, money matters and wishes, and handing them over safely after a death. All demo data is synthetic.

| Area | Path |
| --- | --- |
| API, loops, live call (Python 3.14, FastAPI, Gemini) | `agents/app/` |
| Governance layer: registry, policies, gateway checks, ledger, guardian events, roles, audit, admin API | `agents/app/governance/`, `agents/registry.yaml`, `agents/policies.yaml` |
| Customer web app (React 19, TypeScript, Vite) | `web/src/` (`index.html`) |
| Platform admin portal | `web/src/admin/` (`admin.html`, built to `web/dist-admin/`, served as `anvaya-admin`) |
| Tests | `agents/tests/` |
| Cloud setup scripts | `infra/` |
| Design | `docs/architecture.md` |
| Plan | GitHub issues: milestone "Prototype release" (PR-1 to PR-12); parent issue #43 for governance |

Read [docs/architecture.md](docs/architecture.md) before changing anything in security, the vault or the handover.

## Commands

```bash
cd agents && uv run --group dev ruff check . && uv run --group dev pytest -q   # lint, then all API tests (about 15 seconds, no cloud credentials)
cd web && npm run lint && npm run build                                       # lint, then type-check and build both sites
```

Run both before you say a change is done. If you only changed one side, run that side's check. CI runs the same commands on every pull request. Say honestly what you did and didn't check.

The linters look for bugs, not style, and there's no formatter, so don't reformat code you aren't changing. If a lint rule doesn't fit, turn it off in `agents/pyproject.toml` or `web/biome.jsonc` with a comment saying why, rather than adding suppressions all over the code.

## Git and GitHub rules

- **Never commit or push to `main`, force-push, or skip git hooks (`--no-verify`).** Every push to `main` deploys to production (see `.github/workflows/deploy.yml`).
- Start each piece of work from the latest `main`: `git fetch origin && git switch -c feat/short-name origin/main`. Branch names are `feat/…`, `fix/…`, `docs/…` or `chore/…`.
- Keep a pull request to one change. Split unrelated changes into separate PRs.
- Commit only the files your change needs. Leave other people's uncommitted work alone: don't move, delete, stash or commit it.
- Don't switch branches in a checkout another session is using. If two agents (or an agent and a person) work at the same time, give each its own worktree: `git worktree add ../Anvaya-<topic> -b feat/<topic> origin/main`.
- Open a pull request using the template, and link the roadmap item (`PR-x.y`) or issue. Record progress on the issue.
- Don't merge your own PR. A person reviews and merges.
- Commit messages follow the existing style (`git log` shows it):
  - the subject is a plain sentence in the imperative, with no type prefix and no full stop, and at most about 72 characters;
  - add the roadmap item when there is one, for example `(PR-10.2)`;
  - the body says what changed and why, in plain language, and mentions new tests;
  - don't write subjects like "Updates" or "fix".
- When an agent wrote a substantial part of the change, say so in a `Co-Authored-By:` trailer and in the PR description.

## Rules for the code

These protect users and the budget. Tests enforce several of them, so don't weaken a test to get past one.

1. **Every Gemini call goes through the governance gateway** (`check_call(…)` in `agents/app/governance/core.py`). It names its agent (`agent="…"`), which must be in `agents/registry.yaml` (what the agent is, and the models it tries in order) and have one policy in `agents/policies.yaml` (its autonomy, allowed models and tools, and limits). The model comes from the registry, never from an environment variable. A new agent, model or tool needs a registry entry and a policy in the same PR.
2. **The server never sees vault secrets.** Encryption happens on the device (`web/src/vaultCrypto.ts`). Don't add a server path, log line or agent tool that could read them.
3. **People decide.** The AI proposes. Nothing approves a release, saves to the archive without the owner's Keep, or shares with a partner without consent. Death and legal checks only assist. Keep the two-reviewer rule, override reasons and the waiting period.
   - The one exception, the owner's decision: the fraud pipeline may add tactics and scenarios to the fraud guide on its own when at least 2 independent websites confirm them. It only adds, a reviewer can remove anything it added, and 2 removals in a week stop it until someone resumes it. A new warning sign, which goes into every certificate check, still waits for a reviewer.
4. **No conversation text or names** in the platform admin portal or in logs. Users appear only as hashed keys. The one exception: a nickname a visitor chooses for their own demo copy, which is optional, shown only to platform admins, and never logged.
5. **No real personal data.** Use synthetic data in code, tests, fixtures, prompts and screenshots. Mask account and policy numbers (last 4 digits only).
6. **No secrets in the repo.** Configuration comes from environment variables (`agents/app/config.py`); document new ones in `agents/.env.example` or `web/.env.example`. Never read, print or commit `.env` files.
7. **The prototype release uses the Gemini API key path, not Vertex AI.** A test checks this. The one exception: a person's own Vertex AI express-mode key (PR-10.10), used only for the calls they cause.
8. **Rules belong in code, not only in prompts.** If a model must always or never do something, enforce it in code and add a test.
9. **Tests run without the cloud.** Use the memory store and stub Gemini, as the existing tests do.

## Things only a person does

Don't run these yourself; ask the person you're working with:

- deploying: `gcloud run deploy`, `firebase deploy`, `infra/*.sh`. Deploys happen when a person merges to `main`, or through Actions → Deploy;
- changing GitHub settings, secrets or environments (`ALLOWED_EMAILS`, `PLATFORM_ROLES`);
- changing cloud resources (IAM, KMS, buckets, Firestore data);
- adding a new dependency or Google service: ask first, then follow "Dependencies and versions" below.

## Dependencies and versions

Use the **latest stable release** of every library, runtime, tool, container image and GitHub Action. Check the registry, not your memory: what you learned in training is out of date.

```bash
cd agents && uv pip list --outdated            # Python; or: uv tree --outdated --depth 1
cd web && npm outdated && npm audit            # npm
gh api repos/<owner>/<action>/releases/latest --jq .tag_name   # a GitHub Action
```

- **Stable only.** Don't use pre-releases (alpha, beta, rc, preview, `next`, `canary`) or runtimes past their end of life.
- **Runtimes are pinned in one place each.** Python is in `agents/.python-version` (also `requires-python` and the Dockerfile). Node is in `web/.nvmrc`, the current Active LTS (also `engines`; workflows read the file). Move them only to a newer stable or LTS release, in every place at once. A test checks that they agree.
- **Pin what you ship.** Commit the lockfiles (`agents/uv.lock`, `web/package-lock.json`). Pin container images to a version, never `latest`. Use each GitHub Action's latest release: its major tag (`@v7`) where it publishes one, otherwise the full version (`astral-sh/setup-uv@v10.2.0`; it stopped publishing major tags in v8). Check the tag exists (`gh api repos/<owner>/<action>/git/ref/tags/<tag>`): `deploy.yml` only runs after merging, so CI won't catch a bad tag there.
- **Set version floors to what you tested.** In `pyproject.toml`, use `>=` the locked version. In `package.json`, use `^` the installed version.
- **Read the release notes before a major upgrade.** List the breaking changes that apply in the PR, or say that none do. Then run every check.
- **When you can't use the latest version,** because another package caps it, don't force it or add an override. Record the package and what holds it back in [docs/dependencies.md](docs/dependencies.md).
- **Use permissive licences only:** MIT, Apache-2.0, BSD, ISC, PSF or 0BSD. For an "A OR B" licence, every option must be on that list. Ask before adding anything else. Weak copyleft (MPL) needs a reviewed exception in `agents/tests/test_dependencies.py`; strong copyleft (GPL, AGPL, LGPL) and unknown licences are not allowed. Font files in `web/public/fonts/` may use the SIL Open Font License 1.1, with its `OFL.txt` beside them and a row in the Fonts table of `docs/dependencies.md`.
- **Import only what you declare.** If code imports a package directly, list it as a direct dependency, even if something else already installs it.
- **Keep [docs/dependencies.md](docs/dependencies.md) current in the same PR.** It records each library's version, licence and purpose, and each Google service and what it's for. A test fails if a direct dependency is missing from it, or if any installed package has a licence that isn't allowed.
- **Leave Dependabot's PRs to people.** They arrive weekly. Don't open your own PRs for the same upgrades.

## Files that need the owner's review

[.github/CODEOWNERS](.github/CODEOWNERS) lists them: deploys, infra, hosting, Firestore rules, governance and the registry, sign-in, the vault and the handover, the admin portal, and these agent rules. Point them out in the PR description when you change them.

## Docs and writing

Update docs in the same PR as the code:

- `docs/release-notes.md` for anything a user or judge would notice (Claude Code users can run `/release-notes` to draft the entry);
- `docs/architecture.md` when the design, security model or tests change;
- `README.md` when setup or commands change.

How to write:

- **Docs** use plain English, about 80% of the way to ASD-STE100 (Simplified Technical English):
  - short sentences: about 20 words at most for descriptions, 15 for instructions;
  - one idea per sentence, active voice, simple present or past tense;
  - one word for one meaning, used the same way every time;
  - instructions start with the verb ("Run the tests");
  - no idioms, jargon, filler or marketing words. Code terms and product names are fine.
- **Diagrams** come first when a doc or explanation is about structure, flow, sequence or states. Use Mermaid in Markdown files, so GitHub draws them.
- **Long explanations** for a reviewer or judge can be an HTML page instead of a long Markdown file, when steps, comparisons or interaction make it clearer. Keep the source in the repo only if the page must stay in sync with the code.
- **Pitches for people and institutions** (`docs/concept.md`, `docs/pitch-*.md`) are warm and talk about a service, not technology:
  - they never use the word "AI";
  - they never say someone "died": use "left us", "no longer here" or "when the time comes".
- **The judges' material** (README, deck) names Gemini and Google Cloud openly.
- **Issues** use numbered requirements (`PR-x.y.z`) with a plain-words description and "How we check it".
