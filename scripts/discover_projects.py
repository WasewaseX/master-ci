#!/usr/bin/env python3
"""Workspace discovery: enumerate independent projects per language family.

The workflow's path filters trigger on nested manifests
(``**/package.json``), but language jobs historically analyzed root
manifests only - a nested project triggered a job that then reported
success after skipping every analysis step. This module makes project
discovery explicit:

- workspace markers (npm/pnpm ``workspaces``, ``go.work``, Cargo
    ``[workspace]``) enumerate their actual member projects via
    ``discover_workspaces``, so a monorepo is analyzed package by
    package instead of as one blob,
- every other manifest directory becomes its own project,
- jobs loop over the inventory so no project can be silently skipped.

Fail-closed contract (same semantics as the architecture config): a
workspace marker that exists but cannot be honored - malformed
pnpm-workspace.yaml, a named member whose manifest is missing - maps
to exit 2. A workspace that declares members must never silently
degrade to "analyze the root only".

Usage:
    python3 discover_projects.py --family node [--root .] [--format json|summary]
        [--fail-empty]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from discover_workspaces import WorkspaceError, workspace_inventory

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


def _node_workspace(root: Path) -> bool:
    if (root / "pnpm-workspace.yaml").is_file() or (root / "lerna.json").is_file():
        return True
    pkg = root / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False  # malformed or unreadable manifest: plain project
    return "workspaces" in data


def _rust_workspace(root: Path) -> bool:
    cargo = root / "Cargo.toml"
    if not cargo.is_file():
        return False
    try:
        text = cargo.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\s*\[workspace\]", text, re.M) is not None


def _workspace_rooted(root: Path, family: str) -> bool:
    """True when the root manifest itself covers nested projects."""
    if family == "node":
        return _node_workspace(root)
    if family == "go":
        return (root / "go.work").is_file()
    if family == "rust":
        return _rust_workspace(root)
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


def _project_name(directory: Path, family: str, manifest: str) -> str:
    """Best-effort project name from its manifest, for honest summaries."""
    try:
        if family == "node" and manifest == "package.json":
            data = json.loads((directory / manifest).read_text(encoding="utf-8"))
            return str(data.get("name") or "")
        if family == "rust" and manifest == "Cargo.toml":
            data = tomllib.loads((directory / manifest).read_text(encoding="utf-8"))
            return str((data.get("package") or {}).get("name") or "")
        if family == "go" and manifest == "go.mod":
            for row in (directory / manifest).read_text(encoding="utf-8").splitlines():
                hit = re.match(r"\s*module\s+(\S+)", row)
                if hit:
                    return hit.group(1)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
        return ""
    return ""


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
    warnings: list[str] = []
    # A workspace marker is evaluated before any emptiness check: a marker
    # that promises members but cannot honor them fails closed even when
    # no nested manifest survived the walk.
    workspace = _workspace_rooted(root, family)
    if workspace:
        dirs, misses = workspace_inventory(root, family)
        warnings = [f"workspace glob {pattern!r} matched no member" for pattern in misses]
    if not dirs:
        projects: list[dict] = []
    else:
        # A workspace marker at the root enumerates its actual members, so
        # a monorepo is analyzed package by package, never as one blob.
        projects = [
            {
                "path": d.relative_to(root).as_posix() or ".",
                "manifest": _manifest_name(d, family, manifests),
                "name": _project_name(d, family, _manifest_name(d, family, manifests)),
            }
            for d in dirs
        ]
    return {
        "family": family,
        "root": str(root),
        "workspace": workspace,
        "projects": projects,
        "warnings": warnings,
    }


def discover_all(root: Path) -> dict:
    """Inventory across every family - the full monorepo map."""
    families = sorted(FAMILY_MANIFESTS) + ["dotnet"]
    return {
        "root": str(root.resolve()),
        "families": {family: discover(root, family) for family in families},
    }


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
    if "families" in inventory:
        blocks = [summary(child) for child in inventory["families"].values()]
        return "\n".join(block for block in blocks if block)
    projects = inventory["projects"]
    if not projects:
        return f"{inventory['family']}: no projects discovered"
    label = "workspace root" if inventory["workspace"] else "projects"
    lines = [f"{inventory['family']}: {len(projects)} {label}"]
    for project in projects:
        name = f": {project['name']}" if project.get("name") else ""
        lines.append(f"  - {project['path']} ({project['manifest']}{name})")
    lines.extend(f"  ! {warning}" for warning in inventory.get("warnings", []))
    return "\n".join(lines)


def _all_projects(inventory: dict) -> list[dict]:
    return [p for child in inventory["families"].values() for p in child["projects"]]


def _emit(inventory: dict, args: argparse.Namespace) -> None:
    if args.list:
        for project in _all_projects(inventory) if args.family == "all" else inventory["projects"]:
            print(project["path"])
    elif args.format == "summary":
        print(summary(inventory))
    else:
        print(json.dumps(inventory, indent=2))


def _no_projects(inventory: dict, family: str) -> bool:
    if family == "all":
        return not any(child["projects"] for child in inventory["families"].values())
    return not inventory["projects"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family", required=True, choices=sorted(FAMILY_MANIFESTS) + ["dotnet", "all"]
    )
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
    try:
        inventory = (
            discover_all(Path(args.root))
            if args.family == "all"
            else discover(Path(args.root), args.family)
        )
    except WorkspaceError as exc:
        print(
            f"::error::project discovery rejected: {exc} - a declared workspace "
            "must never silently degrade to root-only analysis",
            file=sys.stderr,
        )
        return 2
    _emit(inventory, args)
    if args.fail_empty and _no_projects(inventory, args.family):
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
