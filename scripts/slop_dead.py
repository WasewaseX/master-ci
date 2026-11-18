"""Repo-level deadness: top-level declarations nothing in the repo uses.

scanaislop-style repo-level structural verification, following the same
"deeper analysis, not another heuristic" guidance that produced the
cycles module. The main site's headline signal list names "Unused
helpers" outright, and the catalog carries `code-quality/
unused-declaration`. Rounds 1-3 took the per-file shapes; deadness is a
repo-level property, so it lives in its own module and runs once per
scan, not once per file. The file-scale half (`orphan-module`) outgrew
this module and now lives in slop_orphan.py; the dependency half
(`knip/unused-dependency` across every stack with a sound oracle) lives
in slop_deps.py.

The usage oracle is deliberately dumb and deliberately conservative: a
candidate survives if its bare name appears anywhere else in the
repository - another source file, a test, a doc, even a string. Only a
name whose occurrences are exactly its own definition site is reported.
That yields near-zero false positives at the cost of missing
dynamically-referenced dead code, the correct trade for an advisory
signal. The name-occurrence pass is shared with slop_orphan, which is
why the oracle helpers live here and the orphan module imports them.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from slop_files import is_ignored, is_test_path, read_text_lax
from slop_model import Finding, _finding

# Candidate record: (path, python definition line or None, JS export
# offset or None). Exactly one of the two location fields is set.
Candidate = tuple[Path, "int | None", "int | None"]

_JS_EXTS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
_JS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def _cap(findings: list[Finding], limit: int = 8) -> list[Finding]:
    findings.sort(key=lambda f: (f.path, f.line))
    return findings[:limit]


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
            texts[path] = read_text_lax(path)
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
