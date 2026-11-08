"""Repo-level deadness: unused declarations and unused runtime dependencies.

scanaislop round-4 adoption. The main site's headline signal list names
"Unused helpers" outright, and the catalog carries `code-quality/
unused-declaration` (AST-based dead-declaration detection) plus the knip
family `knip/unused-export` / `knip/unused-dependency`. Rounds 1-3 took
the per-file shapes; deadness is a repo-level property, so it lives in
its own module and runs once per scan, not once per file.

The usage oracle is deliberately dumb and deliberately conservative: a
candidate survives if its bare name appears anywhere else in the
repository - another source file, a workflow, a doc, even a string.
Only a name whose occurrences are exactly its own definition site is
reported. That yields near-zero false positives at the cost of missing
dynamically-referenced dead code, the correct trade for an advisory
signal.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

from slop_files import is_ignored, is_test_path
from slop_model import Finding, _finding
from slop_patterns import PY_IMPORT_ALIASES

# Entry-point-style runtime deps that legitimate projects never import
# from application code (servers and service layers import them for us).
_DYNAMIC_USE = {
    "gunicorn",
    "uvloop",
    "httptools",
    "uvicorn-worker",
    "python-multipart",
    "asgiref",
}

_JS_EXTS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
_JS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_JS_IMPORT = re.compile(
    r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*|import\s*\(\s*)[\"']([^\"']+)[\"']"
)
_PY_DEP_ITEM = re.compile(r"[\"']([A-Za-z0-9_.\-]+)")
# Dist-name -> import-name: the usable direction of the alias table.
_ALIASES_BY_DIST = {v.lower(): k for k, v in PY_IMPORT_ALIASES.items()}

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
        if path.suffix.lower() == ".py":
            for name, lineno in _py_toplevel_names(path, texts[path]):
                candidates.setdefault(name, (path, lineno, None))
        elif path.suffix.lower() in _JS_EXTS:
            for match in _JS_EXPORT.finditer(texts[path]):
                candidates.setdefault(match.group(1), (path, None, match.start()))
    return candidates


def _count_occurrences(
    candidates: dict[str, Candidate],
    texts: dict[Path, str],
) -> dict[str, Counter]:
    """One repo-wide pass: every occurrence of every candidate name."""
    alternation = re.compile(r"\b(?:" + "|".join(sorted(re.escape(n) for n in candidates)) + r")\b")
    occurrences: dict[str, Counter] = {name: Counter() for name in candidates}
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
# unused-dependency
# ---------------------------------------------------------------------------


def _py_dep_item(stripped: str, deps: dict[str, int], lineno: int) -> bool:
    """Collect one deps-table line; False when the table closes."""
    if stripped.startswith("]"):
        return False
    match = _PY_DEP_ITEM.match(stripped)
    if match:
        deps.setdefault(match.group(1), lineno)
    return True


def _py_inline_deps(stripped: str, deps: dict[str, int], lineno: int) -> None:
    """Single-line table: dependencies = ["a", "b"]."""
    inner = re.match(r"dependencies\s*=\s*\[(.*)\]", stripped)
    if not inner:
        return
    for item in _PY_DEP_ITEM.finditer(inner.group(1)):
        deps.setdefault(item.group(1), lineno)


def _py_deps_from_lines(lines: list[str]) -> dict[str, int]:
    deps: dict[str, int] = {}
    in_project = False
    in_deps = False
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            in_deps = False
        elif in_project and not in_deps:
            if re.match(r"dependencies\s*=\s*\[", stripped):
                in_deps = "]" not in stripped
                _py_inline_deps(stripped, deps, lineno)
        elif in_deps:
            in_deps = _py_dep_item(stripped, deps, lineno)
    return deps


def _py_runtime_deps(root: Path) -> dict[str, int]:
    """pyproject [project] dependencies -> {dist name: declaration line}."""
    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return {}
    try:
        lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    return _py_deps_from_lines(lines)


def _js_runtime_deps(root: Path) -> dict[str, int]:
    """package.json dependencies+peerDependencies -> {pkg: declaration line}."""
    manifest = root / "package.json"
    if not manifest.is_file():
        return {}
    try:
        raw = manifest.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    runtime: set[str] = set()
    for key in ("dependencies", "peerDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            runtime.update(value)
    deps: dict[str, int] = {}
    for pkg in runtime:
        match = re.search(rf"[\"']{re.escape(pkg)}[\"']\s*:", raw)
        if match:
            deps[pkg] = raw[: match.start()].count("\n") + 1
    return deps


def _py_imported_tops(files: list[Path]) -> set[str]:
    tops: set[str] = set()
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        tops.update(
            m.group(1).split(".")[0]
            for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE)
        )
    return tops


def _py_unused_dep_findings(py_deps: dict[str, int], files: list[Path]) -> list[Finding]:
    tops = _py_imported_tops(files)
    tops_folded = {t.replace("_", "") for t in tops}
    findings: list[Finding] = []
    for dist, lineno in py_deps.items():
        if dist.lower() in _DYNAMIC_USE:
            continue
        stem = re.split(r"[<>=!~\[;]", dist)[0]
        module = _ALIASES_BY_DIST.get(stem.lower(), stem.lower())
        if module in tops or module.replace("_", "") in tops_folded:
            continue
        findings.append(
            Finding(
                path="pyproject.toml",
                line=lineno,
                check="unused-dependency",
                message=(
                    f"runtime dependency {stem!r} is never imported - "
                    "declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


def _js_unused_dep_findings(js_deps: dict[str, int], files: list[Path]) -> list[Finding]:
    specs: set[str] = set()
    for path in files:
        if path.suffix.lower() not in _JS_EXTS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        specs.update(_JS_IMPORT.findall(text))
    findings: list[Finding] = []
    for pkg, lineno in js_deps.items():
        base = pkg.split("/")[0] if not pkg.startswith("@") else pkg
        if any(spec == pkg or spec.startswith(base + "/") for spec in specs):
            continue
        findings.append(
            Finding(
                path="package.json",
                line=lineno,
                check="unused-dependency",
                message=(
                    f"runtime package {pkg!r} is never imported - declared "
                    "weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


def check_unused_dependencies(root: Path, files: list[Path]) -> list[Finding]:
    """Runtime deps no source file ever imports (advisory)."""
    if not root.is_dir():
        return []
    findings: list[Finding] = []
    py_deps = _py_runtime_deps(root)
    if py_deps:
        findings.extend(_py_unused_dep_findings(py_deps, files))
    js_deps = _js_runtime_deps(root)
    if js_deps:
        findings.extend(_js_unused_dep_findings(js_deps, files))
    return _cap(findings)
