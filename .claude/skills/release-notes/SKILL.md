---
name: release-notes
description: Write the release note for the current branch or a pull request as its own file in docs/release-notes/next/, in Anvaya's release-notes style. Use before opening a PR that changes anything a user, reviewer or judge would notice, or when asked to update the release notes. With "fold", folds those files into docs/release-notes.md.
argument-hint: "[PR number, base branch (default main), or fold]"
---

# Write the release note

Write the note for the change on this branch (or the PR in `$ARGUMENTS`) as **its own file** in `docs/release-notes/next/`. Never insert it into `docs/release-notes.md`: every PR inserting lines there conflicts with the next one.

If `$ARGUMENTS` is `fold`, skip to "Fold" at the end.

## 1. Find what changed

- For a branch: `git log --format='%h %s%n%b' main..HEAD` and `git diff --stat main...HEAD`.
- For a PR number: `gh pr view <n> --json title,body,commits,files`.
- Read the commit bodies first. They already say what changed and why. Open the code only when a commit body is unclear.

Keep only what a person would notice: owners, families, reviewers, the governance team or judges. Leave out refactors, tests, CI and internal renames. If nothing is noticeable, say so and stop without editing.

## 2. Where it goes

- Name the file after the branch, with `/` as `-`: `docs/release-notes/next/feat-paged-lists.md`. If the branch already has a file there, update it.
- The first line is a bold group from `## Not yet released` in `docs/release-notes.md`, such as `**Secure vault**` or `**Governance and the admin portal**`. Use an existing group when one fits. Then write one or more bullets starting with `- `. The "Release notes" check refuses any other shape.
- A known limitation the change removes or adds: say so in a bullet, such as "No longer a limitation: …". It's sorted out when the files are folded.
- Leave the date out.

## 3. How to write it

Copy the voice of the existing entries:

- One bullet per feature. Start with what the person can now do or see, not with the code.
- Follow the writing rule in `AGENTS.md` ("Docs and writing"): plain words, about 80% of the way to ASD-STE100. Keep sentences short, with one idea each, in the active voice. Don't use marketing words like "seamless", "powerful" or "robust".
- Give concrete numbers and limits where they exist (for example "Live calls end at 20 minutes").
- Mention a safeguard where it matters ("checked on the server", "numbers masked").
- Use no file names, class names or roadmap numbers. Those belong in commits.

## 4. Finish

Show the person the new file, and say which commits you counted as noticeable and which you left out. Don't commit unless they ask.

## Fold (only when asked)

On a new branch from the latest `main`, run `python3 .github/scripts/fold_release_notes.py`. It moves every file's bullets under its group in "Not yet released", adds a new group at the end of "What's included", and deletes the files. Then move any "No longer a limitation" bullets into "Known limitations" by hand, show the diff, and open a PR.
