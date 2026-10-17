"""Release notes come as one file per pull request (docs/release-notes/next/), so two PRs never
edit the same lines; the fold script moves them under their groups in docs/release-notes.md."""

import importlib.util
import pathlib
import shutil

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fold_release_notes", REPO / ".github/scripts/fold_release_notes.py")
fold_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fold_notes)

NOTES = """# Anvaya: Release Notes

## Not yet released

### What's included

**Secure vault**
- Vikram can lock a secret.

**Access**
- Sign in with Google.

### Known limitations
- One reviewer pool.

## 9 October 2026

### What's included

**Secure vault**
- Older entry.
"""


def test_a_note_goes_under_its_group_and_a_new_group_at_the_end():
    portal = "**Governance and the admin portal**\n- Lists come a page at a time.\n- With search.\n"
    out = fold_notes.fold(NOTES, [("a.md", "**Secure vault**\n- Vikram can show a secret.\n"), ("b.md", portal)])
    released = out.split("## 9 October 2026")[1]
    assert "Show" not in released and "page at a time" not in released  # a released section is never touched
    assert "- Vikram can lock a secret.\n- Vikram can show a secret.\n\n**Access**" in out
    assert ("- Sign in with Google.\n\n**Governance and the admin portal**\n- Lists come a page at a time.\n"
            "- With search.\n\n### Known limitations") in out


def test_a_note_must_be_a_group_then_bullets():
    for bad in ("- no group\n", "**Group**\n", "Secure vault\n- x\n", "**Group**\nnot a bullet\n"):
        with pytest.raises(ValueError):
            fold_notes.parse(bad)


def test_folding_the_real_release_notes_keeps_every_line_and_removes_the_files(tmp_path):
    (tmp_path / "docs/release-notes/next").mkdir(parents=True)
    shutil.copy(REPO / "docs/release-notes.md", tmp_path / "docs/release-notes.md")
    before = (tmp_path / "docs/release-notes.md").read_text()
    (tmp_path / "docs/release-notes/next/README.md").write_text("# not a note\n")
    (tmp_path / "docs/release-notes/next/fix-x.md").write_text("**Governance and the admin portal**\n- A folded test line.\n")
    assert fold_notes.main(tmp_path) == 0
    after = (tmp_path / "docs/release-notes.md").read_text()
    assert set(before.splitlines()) <= set(after.splitlines()) and "- A folded test line." in after
    assert after.index("- A folded test line.") < after.index("### Known limitations")
    assert [p.name for p in (tmp_path / "docs/release-notes/next").iterdir()] == ["README.md"]


def test_the_folder_explains_itself_and_holds_only_valid_notes():
    folder = REPO / "docs/release-notes/next"
    assert (folder / "README.md").exists()
    for note in folder.glob("*.md"):
        if note.name != "README.md":
            fold_notes.parse(note.read_text())
