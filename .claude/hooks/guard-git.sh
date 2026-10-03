#!/usr/bin/env bash
# PreToolUse hook for Bash: keeps Claude off main, away from force pushes and
# skipped hooks, and away from deploys. Exit code 2 blocks the command and shows
# the message to Claude.
input=$(cat)
cmd=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" symbolic-ref --short HEAD 2>/dev/null)

block() { echo "Blocked by .claude/hooks/guard-git.sh: $1" >&2; exit 2; }

if printf '%s' "$cmd" | grep -Eq '\bgit\b.*--no-verify'; then
  block "don't skip the git hooks."
fi
if printf '%s' "$cmd" | grep -Eq '\bgit\b.*\bcommit\b' && [ "$branch" = "main" ]; then
  block "you're on main, and every push to main deploys to production. Create a branch first (git switch -c feat/short-name)."
fi
if printf '%s' "$cmd" | grep -Eq '\bgit\b.*\bpush\b'; then
  if [ "$branch" = "main" ] || printf '%s' "$cmd" | grep -Eq '(\s|:)main(\s|$)'; then
    block "pushing to main deploys to production. Push a branch and open a pull request."
  fi
  if printf '%s' "$cmd" | grep -Eq '(\s)(-f|--force)(\s|$)'; then
    block "no force pushes. If you must rewrite your own branch, ask the person you're working with."
  fi
fi
if printf '%s' "$cmd" | grep -Eq '\b(gcloud run deploy|gcloud run services update|firebase deploy)\b|infra/(deploy|setup-github-actions)\.sh'; then
  block "deploys are done by a person (merge to main, or Actions → Deploy)."
fi
exit 0
