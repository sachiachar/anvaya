# Claude Code in this repository

Follow [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

- **A hook enforces the git rules:** `.claude/hooks/guard-git.sh`, registered in `.claude/settings.json`.
  - It blocks commits on `main`, pushes to `main`, force pushes, `--no-verify` and deploy commands.
  - It reads the whole command, so put `git push` and `gh pr create --base main` in **separate** commands.
- **Start each piece of work from the latest `main`:** `git fetch origin && git switch -c feat/name origin/main`.
- **Pitch documents:** keep each one's voice (see CONTRIBUTING.md, "Writing").
