"""File iteration, ignore-file handling and path classification."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path, PurePosixPath

from slop_model import Finding
from slop_patterns import (
    CODE_EXTS,
    DOC_EXTS,
    IGNORE_FILE,
    MAX_FILE_BYTES,
    PHRASES_PATH,
    SKIP_DIRS,
    SKIP_PATTERN,
    TEST_DIRS,
    UI_EXTS,
)


def load_phrases() -> dict:
    with open(PHRASES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _scannable_name(path: Path) -> bool:
    """Name-level filters: skip dirs, dotfiles and generated trees."""
    if path.name in SKIP_DIRS or path.name.startswith("."):
        return False
    return not SKIP_PATTERN.search(path.name)


def _readable_source(path: Path) -> bool:
    """Size and encoding gates: text we can actually scan."""
    suffix = path.suffix.lower()
    if suffix not in CODE_EXTS and suffix not in DOC_EXTS and suffix not in UI_EXTS:
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if root.is_file():
        return [root]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if not _scannable_name(path) or not _readable_source(path):
            continue
        files.append(path)
    return files


def _ignore_lines(ignore_path: Path) -> list[str]:
    try:
        return ignore_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _rule_from_line(
    line: str, globs: list[str], disabled: set[str], scoped: dict[str, list[str]]
) -> None:
    if line.lower().startswith("check:"):
        parts = line.split(":", 2)
        check_id = parts[1].strip().lower()
        if len(parts) == 3 and parts[2].strip():
            scoped.setdefault(check_id, []).append(parts[2].strip())
        else:
            disabled.add(check_id)
    else:
        globs.append(line.rstrip("/"))


def load_ignore_rules(root: Path) -> tuple[list[str], set[str], dict[str, list[str]]]:
    """Read .ai-integrity-ignore.

    Grammar:
    - `glob/line`            - skip the file entirely
    - `check:<id>`           - disable the check everywhere
    - `check:<id>:<glob>`    - disable the check only on matching paths
    """
    globs: list[str] = []
    disabled: set[str] = set()
    scoped: dict[str, list[str]] = {}
    ignore_path = root if root.is_dir() else root.parent
    for raw in _ignore_lines(ignore_path / IGNORE_FILE):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        _rule_from_line(line, globs, disabled, scoped)
    return globs, disabled, scoped


def _scoped_out(finding: Finding, scoped: dict[str, list[str]]) -> bool:
    """True when a scoped `check:<id>:<glob>` rule covers the finding."""
    rules = scoped.get(finding.check.lower())
    if not rules:
        return False
    name = PurePosixPath(finding.path.replace("\\", "/")).name
    return any(
        fnmatch.fnmatch(finding.path, g)
        or fnmatch.fnmatch(name, g)
        or fnmatch.fnmatch(finding.path, g.rstrip("/*") + "/*")
        for g in rules
    )


def is_ignored(rel_path: str, globs: list[str]) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(
        fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(norm, g.rstrip("/*") + "/*") for g in globs
    )


def is_test_path(rel_path: str) -> bool:
    norm = PurePosixPath(rel_path.replace("\\", "/"))
    parts = norm.parts
    if any(part.lower() in TEST_DIRS for part in parts[:-1]):
        return True
    name = norm.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.go")
        or name.endswith("test.js")
        or name.endswith("test.ts")
    )
