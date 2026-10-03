# Contributing to Anvaya

This is how we work together on GitHub, with or without a coding agent. The rules for the code and for writing are in [AGENTS.md](AGENTS.md). They apply to people too.

## Set up

- **API** (`agents/`): Python 3.12+ and [uv](https://docs.astral.sh/uv/). Run `cd agents && uv sync --group dev`.
- **Web app and admin portal** (`web/`): Node 20.19+ (CI uses 22). Run `cd web && npm ci`.
- **Git hooks**, once per clone: `git config core.hooksPath .githooks`. They stop commits and pushes on `main`, for people and agents alike.

Then follow "Run it locally" in the [README](README.md).

## The flow

1. **Start from the latest `main`:** `git fetch origin && git switch -c feat/short-name origin/main` (or `fix/…`, `chore/…`, `docs/…`).
2. **Make one change per branch, with tests.** Small PRs get reviewed faster and are easier to undo.
3. **Run the checks locally.**
   ```bash
   cd agents && uv run --group dev ruff check . && uv run --group dev pytest -q
   cd web && npm run lint && npm run build
   ```
4. **Open a pull request** (`gh pr create`). Fill in the template, link the roadmap item (`PR-x.y`) or issue, and say how you checked it. Open it as a draft if it isn't ready.
5. **CI runs the same checks** on every PR. Fix anything red before asking for review.
6. **Another person reviews.** Changes to the paths in [.github/CODEOWNERS](.github/CODEOWNERS) need the owner's review: the vault, the handover, sign-in, governance, the admin portal, `firestore.rules`, hosting, deploys and the agent rules.
7. **Squash and merge.** The PR title becomes the commit on `main`, so write it in the commit style below. Delete the branch afterwards.
8. **Merging deploys.** Every merge to `main` deploys what changed to production ([deploy.yml](.github/workflows/deploy.yml)). Docs-only changes deploy nothing. Merge only when it's ready to go live, then check the Deploy run in Actions. Never push to `main` directly, and never deploy by hand except through Actions → Deploy.

To stay up to date while a branch is open, run `git pull --rebase origin main` on your own branch. Never rebase a branch someone else is also pushing to.

## Commit messages and PR titles

Copy the style in `git log`:

```
Limits per request from the registry (PR-10.2)

The gateway now enforces each agent's limits from agents/registry.yaml:
- max_input_tokens: estimated before the call; larger requests are refused …

Six new tests; real Gemini calls confirm the output caps leave room.
```

- The subject is a plain imperative sentence, under about 72 characters, with no full stop and the roadmap item if there is one.
- The body says what changed and why. Mention new tests.
- Avoid subjects like "Updates", "fix" or "wip" on `main`.

## Using coding agents

Any agent is fine (Claude Code, Codex, Cursor, Copilot, Gemini CLI). You're responsible for what you commit, whoever typed it.

- **Shared instructions are in `AGENTS.md`.** Claude Code loads them through `CLAUDE.md`, and Codex, Cursor and Copilot read `AGENTS.md` directly. For Gemini CLI, set `"context": {"fileName": ["AGENTS.md"]}` in `.gemini/settings.json`.
- **Change the shared instructions through a PR** when you find something every agent should know, such as a new rule, a gotcha or a command. Don't keep it in your own local notes.
- **Give each parallel session its own worktree.** Running two agents in one folder means one switches branches under the other, and files go missing or end up in the wrong commit. Use `git worktree add ../Anvaya-<topic> -b feat/<topic> origin/main`, and remove it with `git worktree remove` when the branch is merged.
- **Keep personal agent settings out of git.** Use `.claude/settings.local.json` and `CLAUDE.local.md`, which are ignored.
- **Read the whole diff before you commit.** Watch for code the agent deleted or "simplified", tests that were weakened to pass, new dependencies, and changes to files you didn't ask about.
- **Say what the agent did** in the PR description, and add a `Co-Authored-By:` trailer when it wrote a substantial part.
- **Don't paste secrets or real personal data** into an agent: no `.env` contents, no keys, no real family or account details. The demo data is synthetic, so keep it that way.
- **Agents never deploy** or change cloud resources and GitHub settings. A person does that.

## Reviewing

- Check the PR does what its description says, and nothing else.
- Read the tests first: do they test the behaviour, or only the code that was written?
- Look harder at anything touching the vault, the handover, sign-in, governance or the deploy workflow.
- Approve only what you'd be happy to see deployed, because merging deploys.

## Secrets and access

- Secrets live in GitHub (*Settings → Environments → production*) and in Google Secret Manager, never in the repo. `.env` files stay local.
- If a secret is committed by mistake, tell the owner straight away so they can rotate it. Deleting the commit isn't enough.
