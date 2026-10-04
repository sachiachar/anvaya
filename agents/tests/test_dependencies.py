"""Dependency rules (AGENTS.md, "Dependencies and versions"): every direct dependency is in the
inventory (docs/dependencies.md), every installed package has an allowed licence, the runtime
versions agree everywhere they're named, and no container image floats on "latest"."""

import importlib.metadata as md
import json
import pathlib
import re
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[2]
AGENTS = REPO / "agents"
WEB = REPO / "web"
INVENTORY = (REPO / "docs" / "dependencies.md").read_text()

# Permissive licences, as SPDX ids or the names packages use in their metadata.
ALLOWED = re.compile(
    r"^(MIT|MIT-0|MIT License|Apache-2\.0|Apache Software License|Apache License 2\.0|Apache 2\.0|BSD|BSD License|"
    r"BSD-2-Clause|BSD-3-Clause|3-Clause BSD License|0BSD|ISC|ISC License \(ISCL\)|PSF-2\.0|"
    r"Python Software Foundation License|Unlicense|CC0-1\.0)$"
)
# Weak copyleft, reviewed and recorded in docs/dependencies.md: used unmodified or never shipped.
REVIEWED_EXCEPTIONS = {"certifi", "lightningcss"}


def _parts(expression: str) -> list[str]:
    return [p.strip(" ()") for p in re.split(r"\s+(?:OR|AND)\s+|;\s*", expression) if p.strip(" ()")]


def _python_licence(dist: md.Distribution) -> str:
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    lines = (meta.get("License") or "").strip().splitlines()
    return lines[0] if lines else ""


def _name(requirement: str) -> str:
    return re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0].lower()


def _listed(name: str) -> bool:
    return re.search(rf"^\|[^|\n]*(?<![\w@/-]){re.escape(name)}(?![\w-])", INVENTORY, re.M | re.I) is not None


def test_every_direct_dependency_is_in_the_inventory():
    project = tomllib.loads((AGENTS / "pyproject.toml").read_text())
    python = [_name(r) for r in project["project"]["dependencies"]]
    python += [_name(r) for group in project["dependency-groups"].values() for r in group]
    package = json.loads((WEB / "package.json").read_text())
    web = [*package["dependencies"], *package["devDependencies"], *package.get("overrides", {})]
    missing = [n for n in python + web if not _listed(n)]
    assert not missing, f"add these to docs/dependencies.md: {missing}"


def test_installed_python_packages_have_allowed_licences():
    bad = {}
    for dist in md.distributions():
        name = dist.metadata["Name"].lower()
        if name == "life-after-agents" or name in REVIEWED_EXCEPTIONS:
            continue
        licence = _python_licence(dist)
        if not licence or not all(ALLOWED.match(p) for p in _parts(licence)):
            bad[name] = licence
    assert not bad, f"licences need review (see AGENTS.md): {bad}"


def test_npm_packages_have_allowed_licences():
    packages = json.loads((WEB / "package-lock.json").read_text())["packages"]
    bad = {}
    for path, info in packages.items():
        name = path.rsplit("node_modules/", 1)[-1]
        if not path or name.split("-")[0] in REVIEWED_EXCEPTIONS:
            continue
        licence = info.get("license", "")
        if not licence or not all(ALLOWED.match(p) for p in _parts(licence)):
            bad[name] = licence
    assert not bad, f"licences need review (see AGENTS.md): {bad}"


def test_fonts_carry_their_licence_and_are_in_the_inventory():
    """Font files are the one place OFL-1.1 is allowed: the licence must travel with them."""
    fonts_table = INVENTORY.split("## Fonts", 1)[1].split("\n## ", 1)[0]
    folders = [f for f in (WEB / "public" / "fonts").iterdir() if f.is_dir()]
    assert folders, "web/public/fonts/ has no font folders"
    for folder in folders:
        licence = folder / "OFL.txt"
        assert licence.is_file(), f"{folder.name}: add its OFL.txt"
        assert "SIL Open Font License, Version 1.1" in licence.read_text(), f"{folder.name}: OFL.txt isn't OFL-1.1"
        assert re.search(rf"^\| {re.escape(folder.name)} \|", fonts_table, re.M), (
            f"{folder.name}: add it to the Fonts table in docs/dependencies.md")
        others = [f.name for f in folder.iterdir() if f.name != "OFL.txt" and f.suffix != ".woff2"]
        assert not others, f"{folder.name}: only .woff2 font files and OFL.txt belong here: {others}"


def test_runtime_versions_agree_everywhere():
    python = (AGENTS / ".python-version").read_text().strip()
    assert re.search(rf'requires-python = ">={re.escape(python)}"', (AGENTS / "pyproject.toml").read_text())
    assert f"FROM python:{python}-slim" in (AGENTS / "Dockerfile").read_text()
    node = (WEB / ".nvmrc").read_text().strip()
    assert json.loads((WEB / "package.json").read_text())["engines"]["node"] == f">={node}"
    for workflow in (REPO / ".github" / "workflows").glob("*.yml"):
        assert "node-version:" not in workflow.read_text(), f"{workflow.name}: use node-version-file: web/.nvmrc"


def test_container_images_are_pinned():
    for image in re.findall(r"(?:FROM|--from=)\s*(\S+)", (AGENTS / "Dockerfile").read_text()):
        assert ":" in image and not image.endswith(":latest"), f"pin {image} to a version"
