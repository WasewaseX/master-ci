"""Declared dependencies versus what the source tree actually consumes.

The dependency-health tranche, promoted to its own module when the
manifest coverage outgrew the Python and Node stacks that slop_dead
started with. One question per ecosystem: does any source file under
the manifest's directory consume what the manifest declares? Every
oracle is deliberately conservative - a mere mention (a string, a
config, a build script) counts as use, because a false "unused"
accusation costs more than a missed one.

This module carries the shared plumbing (manifest discovery, per-tree
scoping, the mention cap) plus the two founding ecosystems:

- Python: pyproject ``[project] dependencies`` against imported tops,
  with the dist-to-import alias table and entry-point servers exempt.
- JS/TS:  package.json ``dependencies``/``peerDependencies`` (dev is
  exempt by construction) against static and dynamic import specifiers.

The five stacks added in v2.1 - Go, Rust, PHP, C#, Java - live in
slop_deps_stacks.py, along with the ecosystems deliberately left out
(Ruby: Bundler.require auto-loads every default-group gem at boot, so
a missing require line is not evidence; C/C++: CMake target names do
not determine include paths).

Manifests are discovered anywhere under the scan root, each judged
against the sources under its own directory - a monorepo member is
checked against its own code, not the aggregate. Findings are
advisory, capped, and disabled by design when the scan root is a
single file.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

from slop_files import is_ignored
from slop_model import Finding
from slop_patterns import PY_IMPORT_ALIASES, SKIP_DIRS, SKIP_PATTERN

MAX_UNUSED_DEPS = 8

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
_PY_DEP_ITEM = re.compile(r"[\"']([A-Za-z0-9_.\-]+)")
_JS_IMPORT = re.compile(
    r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*|import\s*\(\s*)[\"']([^\"']+)[\"']"
)
# Dist-name -> import-name: the usable direction of the alias table.
_ALIASES_BY_DIST = {v.lower(): k for k, v in PY_IMPORT_ALIASES.items()}


def cap_findings(findings: list[Finding], limit: int = MAX_UNUSED_DEPS) -> list[Finding]:
    findings.sort(key=lambda f: (f.path, f.line))
    return findings[:limit]


def manifests(root: Path, pattern: str, globs: list[str]) -> list[tuple[Path, Path]]:
    """(abs, root-relative) paths of every manifest matching the pattern."""
    out: list[tuple[Path, Path]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not SKIP_PATTERN.search(d)]
            for name in filenames:
                if not fnmatch.fnmatch(name, pattern):
                    continue
                path = Path(dirpath) / name
                rel = path.relative_to(root)
                if not is_ignored(rel.as_posix(), globs):
                    out.append((path, rel))
    except OSError:
        return []
    return sorted(out, key=lambda pair: pair[1].as_posix())


def scoped(files: list[Path], base: Path) -> list[Path]:
    """Sources under one manifest's directory - its consumption scope."""
    return [f for f in files if f.is_relative_to(base)]


def texts(files: list[Path], exts: tuple[str, ...]) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for path in files:
        if path.suffix.lower() not in exts:
            continue
        try:
            out[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
# Python
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
    """Single-line table: dependencies = [\"a\", \"b\"]."""
    inner = re.match(r"dependencies\s*=\s*\[(.*)\]", stripped)
    if not inner:
        return
    for item in _PY_DEP_ITEM.finditer(inner.group(1)):
        deps.setdefault(item.group(1), lineno)


def _py_runtime_deps(manifest: Path) -> dict[str, int]:
    """pyproject [project] dependencies -> {dist name: declaration line}."""
    try:
        lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
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


def _py_imported_tops(files: list[Path]) -> set[str]:
    tops: set[str] = set()
    for _, text in texts(files, (".py",)).items():
        tops.update(
            m.group(1).split(".")[0]
            for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE)
        )
    return tops


def _py_unused_dep_findings(py_deps: dict[str, int], files: list[Path], rel: str) -> list[Finding]:
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
                path=rel,
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


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------


def _js_runtime_deps(manifest: Path) -> dict[str, int]:
    """package.json dependencies+peerDependencies -> {pkg: declaration line}."""
    try:
        raw = manifest.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
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


def _js_unused_dep_findings(js_deps: dict[str, int], files: list[Path], rel: str) -> list[Finding]:
    specs: set[str] = set()
    for _, text in texts(files, _JS_EXTS).items():
        specs.update(_JS_IMPORT.findall(text))
    findings: list[Finding] = []
    for pkg, lineno in js_deps.items():
        base = pkg.split("/")[0] if not pkg.startswith("@") else pkg
        if any(spec == pkg or spec.startswith(base + "/") for spec in specs):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"runtime package {pkg!r} is never imported - declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def check_unused_dependencies(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """Runtime deps no source file ever consumes (advisory)."""
    if not root.is_dir():
        return []
    findings: list[Finding] = []
    for manifest, rel in manifests(root, "pyproject.toml", globs):
        deps = _py_runtime_deps(manifest)
        findings.extend(
            _py_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    for manifest, rel in manifests(root, "package.json", globs):
        deps = _js_runtime_deps(manifest)
        findings.extend(
            _js_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    # The v2.1 stacks import the shared plumbing from here, so the stack
    # module is pulled in lazily to keep the import direction one-way.
    from slop_deps_stacks import check_stack_dependencies

    findings.extend(check_stack_dependencies(root, files, globs))
    return cap_findings(findings)
