# Release notes for the next release

Each pull request that changes what people use adds **its own file** here. Two pull requests never edit the same lines, so their release notes never conflict.

Name the file after your branch, with `/` as `-`, for example `feat-paged-lists.md`. Write a bold group from [the release notes](../../release-notes.md), then one or more bullets:

```markdown
**Governance and the admin portal**
- Long lists in the portal come one page at a time, with search and filters.
```

- Use an existing group when one fits. A new group is added at the end of "What's included".
- Write for the people who use Anvaya, as the release notes do. Leave out file names and roadmap numbers.
- Each scheduled release lists these notes in its [GitHub Release](https://github.com/sachiachar/anvaya/releases).
- When asked, someone folds the files into `docs/release-notes.md` in a pull request: `python3 .github/scripts/fold_release_notes.py`.
