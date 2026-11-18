"""Orphan modules: files and packages nothing in the repository reaches.

`orphan-module` is the file-scale version of the deadness oracle: a
module no import, config, doc or script ever mentions. Python, JS/TS and
Ruby are judged by file stem (their import specifiers name files); Go is
judged by package directory, because files in one Go package reference
each other namelessly. Stems under four characters, test files,
entry-point stems (`main`, `index`, `app`, `cli`, ...) and namespace
glue (`__init__`, `mod`) are exempt, and the oracle corpus reaches
past source into manifests, workflows and config files so a module
wired up by name never gets flagged.

The usage oracle is deliberately dumb and deliberately conservative: a
candidate survives if its bare name appears anywhere else in the
repository - another source file, a test, a doc, even a string. Only a
name whose occurrences are exactly its own definition site is reported.
That yields near-zero false positives at the cost of missing
dynamically-referenced dead code, the correct trade for an advisory
signal. (Split out of slop_dead.py when that file outgrew its own
400-line budget; the shared name-occurrence oracle stays in slop_dead.)
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from slop_dead import _cap, _count_occurrences
from slop_files import is_ignored, is_test_path, read_text_lax
from slop_model import Finding
from slop_patterns import MAX_FILE_BYTES, SKIP_DIRS, SKIP_PATTERN

MAX_ORPHANS = 8
# The oracle corpus is the whole repository's text; this bounds memory on
# very large trees (roughly 64 MB of source is far more than a scan needs).
MAX_CORPUS_BYTES = 64 * 1024 * 1024
MAX_ORACLE_EXTRAS = 2000

_ORPHAN_STEM_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".rb")
_GO_EXTS = (".go",)
# Entry points and namespace glue: files whose stem is a boot name are
# referenced by runners and frameworks, not by an import line.
_ORPHAN_EXEMPT_STEMS = {
    "__init__",
    "__main__",
    "main",
    "index",
    "app",
    "server",
    "cli",
    "wsgi",
    "asgi",
    "manage",
    "conftest",
    "mod",
    "run",
    "start",
    "bootstrap",
}
# The corpus reaches past source: a module wired up by a workflow, a
# manifest, a containerfile or a build script is not an orphan.
_ORACLE_EXTRA_EXTS = {
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".cfg",
    ".ini",
    ".cmake",
    ".gradle",
    ".gradle.kts",
    ".properties",
}
_ORACLE_EXTRA_NAMES = {
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "package.json",
    "pyproject.toml",
    "gemfile",
    "composer.json",
    "pom.xml",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
}

OrphanCandidate = tuple[str, Path, bool]  # (name, representative file, is Go package)


def _orphan_go_packages(
    root: Path,
    go_dirs: dict[Path, list[tuple[str, Path]]],
) -> dict[str, OrphanCandidate]:
    """One candidate per Go package directory - the importable unit.

    The root package and ``cmd/*`` mains are entry points; a package
    holding a ``main.go`` is a binary; short or exempt directory names
    are noise, like their file-stem siblings.
    """
    packages: dict[str, OrphanCandidate] = {}
    for package_dir, members in sorted(go_dirs.items(), key=lambda kv: kv[0].as_posix()):
        if package_dir == root or package_dir.parent.name.lower() == "cmd":
            continue
        if (
            len(package_dir.name) < 4
            or package_dir.name in _ORPHAN_EXEMPT_STEMS
            or any(p.name.lower() == "main.go" for _, p in members)
        ):
            continue
        first = min(members, key=lambda m: m[0])
        packages.setdefault(package_dir.name, (package_dir.name, first[1], True))
    return packages


def _orphan_candidates(
    root: Path, files: list[Path], globs: list[str]
) -> dict[str, OrphanCandidate]:
    """Stem candidates for file-named families, directory candidates for Go.

    A Go package is its directory: files inside one package reference
    each other namelessly, so the importable unit - and the thing an
    orphan verdict names - is the package path, not the file stem.
    """
    candidates: dict[str, OrphanCandidate] = {}
    go_dirs: dict[Path, list[tuple[str, Path]]] = {}
    for path in files:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_ignored(rel, globs) or is_test_path(rel):
            continue
        suffix = path.suffix.lower()
        if suffix in _ORPHAN_STEM_EXTS:
            stem = path.stem
            # Short stems are noise: they collide with prose (the article
            # 'a' matches a.js) and flag nothing a reader would act on.
            if len(stem) >= 4 and stem not in _ORPHAN_EXEMPT_STEMS:
                candidates.setdefault(stem, (stem, path, False))
        elif suffix in _GO_EXTS:
            go_dirs.setdefault(path.parent, []).append((rel, path))
    candidates.update(_orphan_go_packages(root, go_dirs))
    return candidates


def _extra_name_ok(name: str) -> bool:
    """True for config/build file names the orphan oracle cares about."""
    low = name.lower()
    return low in _ORACLE_EXTRA_NAMES or Path(low).suffix in _ORACLE_EXTRA_EXTS


def _read_extra(path: Path) -> str | None:
    """Best-effort read of one bounded extra oracle file."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return read_text_lax(path)
    except (OSError, UnicodeDecodeError):
        return None


def _collect_extras(
    dirpath: str,
    filenames: list[str],
    corpus: dict[Path, str],
    budget: int,
) -> int:
    """One directory's extra files into the corpus; budget comes back."""
    for name in filenames:
        path = Path(dirpath) / name
        if path in corpus or not _extra_name_ok(name):
            continue
        text = _read_extra(path)
        if text is None:
            continue
        budget -= len(text)
        corpus[path] = text
        if budget <= 0 or len(corpus) > MAX_ORACLE_EXTRAS:
            return budget
    return budget


def _oracle_extras(root: Path, corpus: dict[Path, str], budget: int) -> int:
    """Append the config/build files the scan itself does not carry.

    A module wired up by a workflow, a manifest, a containerfile or a
    build script is not an orphan, so the corpus reaches past source.
    Collection stops at the byte or file cap.
    """
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not SKIP_PATTERN.search(d)]
            budget = _collect_extras(dirpath, filenames, corpus, budget)
            if budget <= 0 or len(corpus) > MAX_ORACLE_EXTRAS:
                return budget
    except OSError:
        return budget
    return budget


def _orphan_corpus(root: Path, files: list[Path]) -> dict[Path, str]:
    """Every scanned text plus the config/build files the scan skips."""
    corpus: dict[Path, str] = {}
    budget = MAX_CORPUS_BYTES
    for path in files:
        try:
            text = read_text_lax(path)
        except OSError:
            continue
        budget -= len(text)
        corpus[path] = text
        if budget <= 0:
            return corpus
    _oracle_extras(root, corpus, budget)
    return corpus


def _flag_orphans(
    candidates: dict[str, OrphanCandidate],
    occurrences: dict[str, Counter],
) -> list[Finding]:
    findings: list[Finding] = []
    for name, (_stem, path, is_go) in sorted(candidates.items()):
        touched = occurrences.get(name)
        if touched and not set(touched) <= {path}:
            continue  # some other file mentions the name - referenced
        kind = "Go package" if is_go else "module"
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="orphan-module",
                message=(
                    f"nothing in the repository references {name!r} - {kind} "
                    "unreachable from any import, config or script"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


def check_orphan_modules(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    """Modules no import, config, doc or script ever mentions (advisory)."""
    if not root.is_dir():
        return []
    candidates = _orphan_candidates(root, files, globs)
    if not candidates:
        return []
    corpus = _orphan_corpus(root, files)
    occurrences = _count_occurrences(candidates, corpus)
    return _cap(_flag_orphans(candidates, occurrences), MAX_ORPHANS)
