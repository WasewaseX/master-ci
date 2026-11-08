#!/usr/bin/env python3
"""Workspace discovery: enumerate independent projects per language family.

The workflow's path filters trigger on nested manifests
(``**/package.json``), but language jobs historically analyzed root
manifests only - a nested project triggered a job that then reported
success after skipping every analysis step. This module makes project
discovery explicit:

- root manifests with workspace semantics (npm/pnpm/yarn workspaces,
    ``go.work``, Cargo ``[workspace]``) collapse to a single ``.``
    project,
- every other manifest directory becomes its own project,
- jobs loop over the inventory so no project can be silently skipped.

Usage:
    python3 discover_projects.py --family node [--root .] [--format json|summary]
        [--fail-empty]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    "venv",
    ".venv",
    "__pycache__",
    "_ci",
    "min",
    ".next",
    ".turbo",
    "coverage",
    "testdata",
    "fixtures",
}

# Manifests that mark an independent project root for a family. The dotnet
# family uses glob markers instead (handled separately below).
FAMILY_MANIFESTS: dict[str, tuple[str, ...]] = {
    "node": ("package.json",),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
    "jvm": (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ),
    "ruby": ("Gemfile",),
    "php": ("composer.json",),
    "cpp": ("CMakeLists.txt",),
}
DOTNET_GLOBS = ("*.csproj", "*.fsproj", "*.sln", "*.slnx")


def _workspace_rooted(root: Path, family: str) -> bool:
    """True when the root manifest itself covers nested projects."""
    if family == "node":
        pkg = root / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return False  # malformed or unreadable manifest: plain project
            return "workspaces" in data
        if (root / "pnpm-workspace.yaml").is_file() or (root / "lerna.json").is_file():
            return True
        return False
    if family == "go":
        return (root / "go.work").is_file()
    if family == "rust":
        cargo = root / "Cargo.toml"
        if cargo.is_file():
            try:
                return (
                    re.search(r"^\s*\[workspace\]", cargo.read_text(encoding="utf-8"), re.M)
                    is not None
                )
            except OSError:
                return False
        return False
    return False


def _manifest_dirs(root: Path, family: str) -> list[Path]:
    """Directories holding a project manifest, in stable sorted order."""
    names = FAMILY_MANIFESTS[family]
    found: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir()):
            if not child.is_dir():
                if child.name in names:
                    found.append(current)
                continue
            if child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            stack.append(child)
    return sorted(set(found), key=lambda p: p.relative_to(root).as_posix())


def _dotnet_dirs(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in DOTNET_GLOBS:
        for hit in root.glob(f"**/{pattern}"):
            if not any(part in SKIP_DIRS for part in hit.relative_to(root).parts[:-1]):
                found.add(hit.parent)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def discover(root: Path, family: str) -> dict:
    """Inventory of independent projects for one family under ``root``."""
    root = root.resolve()
    manifests: dict[str, tuple[str, ...]] | None = FAMILY_MANIFESTS
    if family == "dotnet":
        dirs = _dotnet_dirs(root)
        manifests = None
    elif family in FAMILY_MANIFESTS:
        dirs = _manifest_dirs(root, family)
    else:
        raise ValueError(f"unknown family: {family}")
    if not dirs:
        projects: list[dict] = []
        workspace = False
    else:
        # A workspace marker at the root covers every nested project (npm
        # workspaces, go.work `use` lists, Cargo `[workspace]`), so the
        # family collapses to a single root project.
        workspace = _workspace_rooted(root, family)
        if workspace:
            dirs = [root]
        projects = [
            {
                "path": d.relative_to(root).as_posix() or ".",
                "manifest": _manifest_name(d, family, manifests),
            }
            for d in dirs
        ]
    return {"family": family, "root": str(root), "workspace": workspace, "projects": projects}


def _manifest_name(
    directory: Path, family: str, manifests: dict[str, tuple[str, ...]] | None
) -> str:
    if family == "dotnet":
        for pattern in DOTNET_GLOBS:
            hit = sorted(directory.glob(pattern))
            if hit:
                return hit[0].name
        return ""
    assert manifests is not None
    for name in manifests[family]:
        if (directory / name).is_file():
            return name
    return ""


def summary(inventory: dict) -> str:
    projects = inventory["projects"]
    if not projects:
        return f"{inventory['family']}: no projects discovered"
    label = "workspace root" if inventory["workspace"] else "projects"
    lines = [f"{inventory['family']}: {len(projects)} {label}"]
    for project in projects:
        lines.append(f"  - {project['path']} ({project['manifest']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True, choices=sorted(FAMILY_MANIFESTS) + ["dotnet"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print one project path per line (shell-loop friendly)",
    )
    parser.add_argument(
        "--fail-empty",
        action="store_true",
        help="exit 1 when zero projects are discovered (turns silent skips into failures)",
    )
    args = parser.parse_args(argv)
    inventory = discover(Path(args.root), args.family)
    if args.list:
        for project in inventory["projects"]:
            print(project["path"])
    elif args.format == "summary":
        print(summary(inventory))
    else:
        print(json.dumps(inventory, indent=2))
    if args.fail_empty and not inventory["projects"]:
        print(
            f"::error::family '{args.family}' was selected but no project manifest "
            f"was discovered under {args.root} - the stack would otherwise skip "
            "silently; add a manifest or disable the stack explicitly",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
