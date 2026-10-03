#!/usr/bin/env bash
# PreToolUse hook for Bash: keeps Claude off main, away from force pushes and
# skipped hooks, and away from deploys. Exit code 2 blocks the command and shows
# the message to Claude.
#
# The branch is read from the folder the command runs in: the session's cwd, or
# the last `git -C <dir>` or `cd <dir>` before the git command, so it's right in
# worktrees too. It matches the command text, so a command that only mentions a
# blocked pattern (in a heredoc, say) is blocked as well.
parsed=$(python3 -c '
import json, os, re, sys
d = json.load(sys.stdin)
cmd = d.get("tool_input", {}).get("command", "")
base = d.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
path = r"(\"[^\"]+\"|\x27[^\x27]+\x27|[^\s;&|)]+)"
target = (re.findall(r"\bgit\s+-C\s+" + path, cmd) or [None])[-1]
if target is None:
    before_git = re.split(r"\bgit\b", cmd, maxsplit=1)[0]
    target = (re.findall(r"(?:^|[;&|(]\s*)cd\s+" + path, before_git) or [None])[-1]
if target:
    d2 = os.path.normpath(os.path.join(base, os.path.expanduser(target.strip("\"\x27"))))
    if os.path.isdir(d2):
        base = d2
print(base)
print(cmd)
' 2>/dev/null)
dir=$(printf '%s\n' "$parsed" | head -n 1)
cmd=$(printf '%s\n' "$parsed" | tail -n +2)
branch=$(git -C "${dir:-.}" symbolic-ref --short HEAD 2>/dev/null)

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
