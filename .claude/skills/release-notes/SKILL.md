---
name: release-notes
description: Draft the docs/release-notes.md entry for the current branch or a pull request, in Anvaya's release-notes style. Use before opening a PR that changes anything a user, reviewer or judge would notice, or when asked to update the release notes.
argument-hint: "[PR number or base branch, default main]"
---

# Draft the release notes entry

Write the entry for the change on this branch (or the PR in `$ARGUMENTS`) into `docs/release-notes.md`.

## 1. Find what changed

- For a branch: `git log --format='%h %s%n%b' main..HEAD` and `git diff --stat main...HEAD`.
- For a PR number: `gh pr view <n> --json title,body,commits,files`.
- Read the commit bodies first. They already say what changed and why. Open the code only when a commit body is unclear.

Keep only what a person would notice: owners, families, reviewers, the governance team or judges. Leave out refactors, tests, CI and internal renames. If nothing is noticeable, say so and stop without editing.

## 2. Where it goes

`docs/release-notes.md` has one `##` section per release, newest first. Each section has `### What's included`, then groups headed by a bold line (`**Secure vault**`), then `### Known limitations` and `### Next`.

- Add to a `## Not yet released` section at the top, directly under `# Anvaya: Release Notes`. Create it if it's missing. Never edit a released section, except to fix a mistake.
- Put each bullet under the existing bold group it belongs to (Talking to Saathi, Keeping what matters, Next steps, Legal help, Secure vault, Handover, Family access, Access). Add a new group only when none fits, for example **Governance and the admin portal**.
- If the change removes a known limitation, delete it from the limitations under `## Not yet released`. Copy that section from the last release first if it isn't there yet. If the change adds a limitation, add it.
- Leave the date out. The person releasing adds the version and date.

## 3. How to write it

Copy the voice of the existing entries:

- One bullet per feature. Start with what the person can now do or see, not with the code.
- Use plain words and short sentences. Don't use marketing words like "seamless", "powerful" or "robust".
- Give concrete numbers and limits where they exist (for example "Live calls end at 20 minutes").
- Mention a safeguard where it matters ("checked on the server", "numbers masked").
- Use no file names, class names or roadmap numbers. Those belong in commits.

## 4. Finish

Show the person the diff of `docs/release-notes.md`, and say which commits you counted as noticeable and which you left out. Don't commit unless they ask.
