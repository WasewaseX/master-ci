#!/usr/bin/env python3
"""Workspace member enumeration for the project-discovery module.

A workspace marker at a repo root (npm/pnpm ``workspaces``,
``go.work``, Cargo ``[workspace]``) promises that nested projects are
declared somewhere explicit. This module honors that promise: it reads
each manifest dialect, expands member globs, and refuses to guess -
``WorkspaceError`` on any declared member that cannot be backed by a
real manifest file, so a monorepo is never silently analyzed as one
root-only blob.

Fail-closed contract (same semantics as the architecture config): the
CLI maps ``WorkspaceError`` to exit 2.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

GLOB_CHARS = set("*?[")
# Gradle is deliberately absent: a Gradle build is analyzed from the root
# invocation (settings.gradle includes are build configuration, not
# separate analysis targets), so the root project is the honest unit.


class WorkspaceError(Exception):
    """A workspace marker exists but cannot be honored - maps to exit 2."""


def is_glob(pattern: str) -> bool:
    return any(ch in GLOB_CHARS for ch in pattern)


def _pnpm_globs(root: Path) -> list[str]:
    """Member patterns from pnpm-workspace.yaml; empty when absent."""
    pnpm = root / "pnpm-workspace.yaml"
    if not pnpm.is_file():
        return []
    if not (root / "package.json").is_file():
        raise WorkspaceError(f"{pnpm} declares a workspace but the root package.json is missing")
    parsed = pnpm_packages(pnpm)
    if parsed is None:
        raise WorkspaceError(f"unparseable workspace manifest {pnpm}")
    return parsed


def _package_json_globs(root: Path) -> list[str]:
    """Member patterns from package.json workspaces; empty when absent."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A broken package.json is the npm stack's failure to report,
        # not discovery's; without parseable JSON we cannot claim
        # workspace semantics, so treat it as a plain project.
        return []
    declared = data.get("workspaces")
    if isinstance(declared, list):
        return [str(p) for p in declared]
    if isinstance(declared, dict):
        return [str(p) for p in declared.get("packages") or []]
    if declared is not None:
        raise WorkspaceError(
            f"package.json workspaces has unsupported type {type(declared).__name__}"
        )
    return []


def node_workspace_globs(root: Path) -> list[str] | None:
    """Workspace member patterns for a node root, or None for a plain project."""
    patterns = _pnpm_globs(root) + _package_json_globs(root)
    return patterns or None


def pnpm_packages(path: Path) -> list[str] | None:
    """``packages:`` entries from pnpm-workspace.yaml.

    PyYAML when installed, a purpose-built fallback parser otherwise -
    the file is a flat string list by spec, and helper scripts must not
    hard-require an optional dependency (same policy as path_filters).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"unreadable workspace manifest {path}: {exc}") from exc
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text)
    except ImportError:
        data = _pnpm_fallback(text)
    except Exception as exc:  # noqa: BLE001 - any yaml failure is unparseable
        raise WorkspaceError(f"unparseable workspace manifest {path}: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
        return None
    return [str(p) for p in data["packages"]]


def _pnpm_fallback(text: str) -> dict | None:
    """Flat ``packages:`` list parser for environments without PyYAML."""
    packages: list[str] = []
    in_packages = False
    for row in text.splitlines():
        if re.fullmatch(r"\S.*:", row):
            in_packages = row.startswith("packages:")
            continue
        if in_packages:
            hit = re.match(r"\s*-\s*(.+?)\s*(?:#.*)?$", row)
            if hit:
                packages.append(hit.group(1).strip("'\""))
    return {"packages": packages}


def expand_globs(root: Path, patterns: list[str], manifest: str) -> tuple[list[Path], list[str]]:
    """Member dirs holding ``manifest``, plus patterns that matched nothing."""
    members: list[Path] = []
    misses: list[str] = []
    for pattern in patterns:
        clean = pattern.removeprefix("./").rstrip("/")
        hits = [
            d
            for d in sorted(root.glob(clean))
            if d.is_dir() and (d / manifest).is_file() and d != root
        ]
        if not hits and not is_glob(clean):
            raise WorkspaceError(
                f"workspace member {pattern!r} has no {manifest} - the declared "
                "project would otherwise be silently skipped"
            )
        if not hits:
            misses.append(pattern)
        members.extend(hits)
    return sorted(set(members)), misses


def go_work_members(root: Path) -> list[Path] | None:
    """Member dirs from go.work ``use`` entries, or None without go.work."""
    work = root / "go.work"
    if not work.is_file():
        return None
    try:
        text = work.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"unreadable workspace manifest {work}: {exc}") from exc
    block = re.search(r"\buse\s*\(([^)]*)\)", text, re.S)
    if block:
        entries = re.findall(r'"?([^"\s]+)"?', block.group(1))
    else:
        entries = re.findall(r'\buse\s+"?([^"\s]+)"?', text)
    members: list[Path] = []
    for entry in entries:
        rel = entry.removeprefix("./")
        if rel in (".", ""):
            continue  # the root module is handled by root inclusion
        directory = root / rel
        if not (directory / "go.mod").is_file():
            raise WorkspaceError(
                f"go.work use {entry!r} has no go.mod - the declared module "
                "would otherwise be silently skipped"
            )
        members.append(directory)
    return sorted(set(members))


def cargo_members(root: Path) -> tuple[list[Path], list[str], bool] | None:
    """(members, glob misses, root has [package]) or None without [workspace]."""
    cargo = root / "Cargo.toml"
    if not cargo.is_file():
        return None
    try:
        data = tomllib.loads(cargo.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise WorkspaceError(f"unparseable workspace manifest {cargo}: {exc}") from exc
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        return None
    members = [str(m) for m in workspace.get("members") or []]
    exclude = {str(e).removeprefix("./").rstrip("/") for e in workspace.get("exclude") or []}
    dirs, misses = expand_globs(root, members, "Cargo.toml")
    dirs = [d for d in dirs if d.relative_to(root).as_posix() not in exclude]
    return dirs, misses, isinstance(data.get("package"), dict)


def root_project(root: Path, family: str) -> bool:
    """Does the workspace root itself carry an analyzable manifest?"""
    if family == "node":
        return (root / "package.json").is_file()
    if family == "go":
        return (root / "go.mod").is_file()
    if family == "rust":
        cargo = root / "Cargo.toml"
        if not cargo.is_file():
            return False
        try:
            return isinstance(tomllib.loads(cargo.read_text(encoding="utf-8")).get("package"), dict)
        except (tomllib.TOMLDecodeError, OSError):
            return False
    return False


def workspace_inventory(root: Path, family: str) -> tuple[list[Path], list[str]]:
    """Member dirs plus glob-miss warnings for one workspace family."""
    if family == "node":
        patterns = node_workspace_globs(root)
        if patterns is None:
            return [root], []
        members, misses = expand_globs(root, patterns, "package.json")
        members.append(root)  # the workspace root is itself a package
        return sorted(set(members)), misses
    if family == "go":
        members = go_work_members(root) or []
        if root_project(root, family):
            members.append(root)
        return sorted(set(members)), []
    if family == "rust":
        found = cargo_members(root)
        if found is None:
            return [root], []
        members, misses, root_is_package = found
        if root_is_package:
            members.append(root)
        return sorted(set(members)), misses
    return [root], []
