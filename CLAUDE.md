@AGENTS.md

## Claude Code

- **A hook enforces the git rules:** `.claude/hooks/guard-git.sh`, registered in `.claude/settings.json`.
  - It blocks commits on `main`, pushes to `main`, force pushes, `--no-verify` and deploy commands. It checks the branch of the folder the command runs in, so it works in worktrees.
  - It reads the whole command text. Put `git push` and `gh pr create --base main` in **separate** commands, and write files that mention blocked commands with the Write tool, not a heredoc.
- **Shared settings** are in `.claude/settings.json`: allowed, ask-first and blocked commands. Put personal settings in `.claude/settings.local.json` and personal notes in `CLAUDE.local.md`; git ignores both.
- **Plan before large changes** (plan mode, or `/paul:plan`). For anything that touches the vault, the handover or governance, write the plan out and wait for a yes.
- **Before opening a PR**, run `/code-review` on the branch. For changes to auth, the vault or the handover, also run `/security-review`. Run `/release-notes` when users will notice the change.
- **Use `gh` for GitHub work:** `gh pr create --fill`, then edit the description so it follows the template.
