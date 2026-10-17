"""Fold the release-note files in docs/release-notes/next/ into docs/release-notes.md.

Each pull request adds its own file there, so two PRs never edit the same lines. This moves every
file's bullets under its bold group in "## Not yet released" -> "### What's included" (a new group
goes at the end of that part), then deletes the files. Run it in a branch, and open a PR:

    python3 .github/scripts/fold_release_notes.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOTES = "docs/release-notes.md"
NEXT = "docs/release-notes/next"


def parse(text: str) -> tuple[str, list[str]]:
    """A note file: a bold group line ("**Secure vault**"), then bullets ("- ...")."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or not (lines[0].startswith("**") and lines[0].endswith("**")):
        raise ValueError("the first line must be a bold group, for example **Governance and the admin portal**")
    bullets = lines[1:]
    if not bullets or not bullets[0].startswith("- "):
        raise ValueError("after the group line, write one or more bullets starting with '- '")
    return lines[0], bullets


def fold(notes: str, files: list[tuple[str, str]]) -> str:
    """The release notes with each file's bullets added under its group."""
    lines = notes.splitlines()
    start = lines.index("## Not yet released")
    included = lines.index("### What's included", start)
    end = next(i for i in range(included + 1, len(lines)) if lines[i].startswith("##"))
    for _name, text in files:
        group, bullets = parse(text)
        if group in lines[included:end]:
            at = lines.index(group, included, end) + 1
            while at < end and lines[at].strip() and not lines[at].startswith("**"):
                at += 1  # after the group's last line, before the blank line
        else:
            while lines[end - 1] == "":
                end -= 1
            lines[end:end] = ["", group]
            at = end + 2
        lines[at:at] = bullets
        end = next(i for i in range(included + 1, len(lines)) if lines[i].startswith("##"))
    return "\n".join(lines) + "\n"


def main(root: pathlib.Path = ROOT) -> int:
    files = sorted(p for p in (root / NEXT).glob("*.md") if p.name != "README.md")
    if not files:
        print("No release-note files to fold.")
        return 0
    path = root / NOTES
    path.write_text(fold(path.read_text(), [(p.name, p.read_text()) for p in files]))
    for p in files:
        p.unlink()
    print(f"Folded {len(files)} file(s) into {NOTES}: " + ", ".join(p.name for p in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
