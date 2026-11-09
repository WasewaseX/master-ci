"""Repo-level deadness: unused declarations and unreachable modules.

scanaislop-style repo-level structural verification, following the same
"deeper analysis, not another heuristic" guidance that produced the
deadness module. The main site's headline signal list names "Unused
helpers" outright, and the catalog carries `code-quality/
unused-declaration`. Rounds 1-3 took the per-file shapes; deadness is a
repo-level property, so it lives in its own module and runs once per
scan, not once per file. The dependency half of the tranche
(`knip/unused-dependency` across every stack with a sound oracle)
outgrew this module and now lives in slop_deps.py.

The usage oracle is deliberately dumb and deliberately conservative: a
candidate survives if its bare name appears anywhere else in the
repository - another source file, a test, a doc, even a string. Only a
name whose occurrences are exactly its own definition site is reported.
That yields near-zero false positives at the cost of missing
dynamically-referenced dead code, the correct trade for an advisory
signal.

`orphan-module` is the file-scale version of the same oracle: a module
no import, config, doc or script ever mentions. Python, JS/TS and Ruby
are judged by file stem (their import specifiers name files); Go is
judged by package directory, because files in one Go package reference
each other namelessly. Stems under four characters, test files,
entry-point stems (`main`, `index`, `app`, `cli`, ...) and namespace
glue (`__init__`, `mod`) are exempt, and the oracle corpus reaches
past source into manifests, workflows and config files so a module
wired up by name never gets flagged.
"""

from __future__ import annotations

import ast
import os
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from slop_files import is_ignored, is_test_path
from slop_model import Finding, _finding
from slop_patterns import MAX_FILE_BYTES, SKIP_DIRS, SKIP_PATTERN

MAX_ORPHANS = 8
# The oracle corpus is the whole repository's text; this bounds memory on
# very large trees (roughly 64 MB of source is far more than a scan needs).
MAX_CORPUS_BYTES = 64 * 1024 * 1024
MAX_ORACLE_EXTRAS = 2000

_JS_EXTS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
_JS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)

# Candidate record: (path, python definition line or None, JS export
# offset or None). Exactly one of the two location fields is set.
Candidate = tuple[Path, "int | None", "int | None"]


def _cap(findings: list[Finding], limit: int = 8) -> list[Finding]:
    findings.sort(key=lambda f: (f.path, f.line))
    return findings[:limit]


# ---------------------------------------------------------------------------
# unused-declaration
# ---------------------------------------------------------------------------


def _py_toplevel_names(path: Path, text: str) -> list[tuple[str, int]]:
    """Module-level def/class names with their definition line."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    names: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            if not (name.startswith("__") and name.endswith("__")) and name != "main":
                names.append((name, node.lineno))
    return names


def _deadness_texts(files: list[Path]) -> dict[Path, str]:
    """Read every Python and JS-family file once - the usage oracle.

    Every scanned file counts as a usage oracle, including tests, docs
    and workflows. Only the candidate harvest excludes tests: a helper
    exercised by a test suite is not dead.
    """
    texts: dict[Path, str] = {}
    for path in files:
        suffix = path.suffix.lower()
        if suffix != ".py" and suffix not in _JS_EXTS:
            continue
        try:
            texts[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return texts


def _harvest_one(
    candidates: dict[str, Candidate],
    path: Path,
    text: str,
) -> None:
    """Add one file's top-level definitions to the candidate map."""
    if path.suffix.lower() == ".py":
        for name, lineno in _py_toplevel_names(path, text):
            candidates.setdefault(name, (path, lineno, None))
    elif path.suffix.lower() in _JS_EXTS:
        for match in _JS_EXPORT.finditer(text):
            candidates.setdefault(match.group(1), (path, None, match.start()))


def _harvest_candidates(
    root: Path,
    files: list[Path],
    texts: dict[Path, str],
    globs: list[str],
) -> dict[str, Candidate]:
    """Name -> definition site, from non-test source files."""
    candidates: dict[str, Candidate] = {}
    for path in files:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_ignored(rel, globs) or is_test_path(rel) or path not in texts:
            continue
        _harvest_one(candidates, path, texts[path])
    return candidates


def _count_occurrences(
    names: Iterable[str],
    texts: dict[Path, str],
) -> dict[str, Counter]:
    """One repo-wide pass: every occurrence of every candidate name."""
    ordered = sorted(names)
    alternation = re.compile(r"\b(?:" + "|".join(map(re.escape, ordered)) + r")\b")
    occurrences: dict[str, Counter] = {name: Counter() for name in ordered}
    for path, text in texts.items():
        for name in alternation.findall(text):
            occurrences[name][path] += 1
    return occurrences


def _flag_dead(
    candidates: dict[str, Candidate],
    occurrences: dict[str, Counter],
    texts: dict[Path, str],
) -> list[Finding]:
    findings: list[Finding] = []
    for name, (path, py_line, js_offset) in sorted(candidates.items()):
        counts = occurrences[name]
        if len(counts) != 1 or sum(counts.values()) != 1:
            continue  # referenced elsewhere, or more than a lone definition
        if py_line is not None:
            findings.append(
                Finding(
                    path=str(path),
                    line=py_line,
                    check="unused-declaration",
                    message=(
                        f"{name!r} is defined but never referenced anywhere in the "
                        "repository - dead helper"
                    ),
                    weight=1,
                    advisory=True,
                )
            )
        elif js_offset is not None:
            findings.append(
                _finding(
                    path,
                    texts[path],
                    js_offset,
                    "unused-declaration",
                    (
                        f"{name!r} is exported but never referenced anywhere in the "
                        "repository - dead export"
                    ),
                    1,
                )
            )
    return findings


def check_unused_declarations(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    """Top-level defs and JS exports that nothing in the repo references."""
    if not root.is_dir():
        return []
    texts = _deadness_texts(files)
    candidates = _harvest_candidates(root, files, texts, globs)
    if not candidates:
        return []
    occurrences = _count_occurrences(candidates, texts)
    return _cap(_flag_dead(candidates, occurrences, texts))


# ---------------------------------------------------------------------------
# orphan-module
# ---------------------------------------------------------------------------

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
        return path.read_text(encoding="utf-8", errors="replace")
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
            text = path.read_text(encoding="utf-8", errors="replace")
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
    return _cap(_flag_orphans(candidates, occurrences))
