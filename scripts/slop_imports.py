"""Phantom and undeclared import detection across language families."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from slop_files import read_text_lax
from slop_model import Finding, _finding
from slop_patterns import NODE_BUILTINS, PY_IMPORT_ALIASES, SKIP_DIRS, STD_LIB_NAMES

# Scoped suppression on an import line (ruff/flake8 F401 convention).
_NOQA_RE = re.compile(r"#\s*noqa\b")

# --- JavaScript/TypeScript binding-aware import shapes -------------------
#
# The phantom-import question for the js family is binding-aware: an
# import is unused only when NONE of the names it binds is referenced
# outside the import statements. The pre-v2.18 version deleted the
# import statement and searched for the PACKAGE NAME in the rest of the
# file, so `import { calculate } from "math-library"` followed by
# `calculate(10)` was flagged - the binding was used, the package
# string was not. Audit finding A (2026-11-17): 8 of 10 usage shapes
# false-positived, including side-effect imports, which have no
# bindings at all and are intentional by definition.


from slop_js_bindings import _js_phantom_imports  # noqa: E402 - grammar lives beside the parser


def check_phantom_imports(path: Path, text: str, family: str) -> list[Finding]:
    """Imports that are never used in the file (advisory)."""
    if family == "py":
        return _py_phantom_imports(path, text)
    if family == "js":
        return _js_phantom_imports(path, text)
    return []


def _import_aliases(node: ast.AST) -> list[tuple[str, int]]:
    """(bound name, line) for one import statement."""
    if isinstance(node, ast.Import):
        return [(alias.asname or alias.name.split(".")[0], node.lineno) for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [
            (alias.asname or alias.name, node.lineno) for alias in node.names if alias.name != "*"
        ]
    return []


def _py_imported_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Collect (bound name, line) for every import in the module."""
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        imported.extend(_import_aliases(node))
    return imported


def _py_unused_import_findings(
    path: Path,
    body: str,
    imported: list[tuple[str, int]],
    lines: list[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for name, lineno in imported[:40]:
        if name in ("__future__", "annotations"):
            continue
        source = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if _NOQA_RE.search(source):
            # A scoped noqa (ruff F401 convention) declares the unused
            # import intentional: re-export surfaces, side-effect modules.
            continue
        if not re.search(rf"\b{re.escape(name)}\b", body):
            findings.append(
                Finding(
                    path=str(path),
                    line=lineno,
                    check="phantom-import",
                    message=f"import {name!r} is never used",
                    weight=1,
                    advisory=True,
                )
            )
    return findings[:5]


def _py_phantom_imports(path: Path, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imported = _py_imported_names(tree)
    lines = text.splitlines()
    body = "\n".join(line for line in lines if not line.lstrip().startswith(("import ", "from ")))
    return _py_unused_import_findings(path, body, imported, lines)


def _parse_py_manifest(manifest: Path, declared: set[str]) -> None:
    try:
        content = read_text_lax(manifest)
    except OSError:
        return
    if manifest.suffix == ".txt":
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith(("-", "#")):
                continue
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
            if name:
                declared.add(name.lower().replace("_", "-"))
        return
    block = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if block:
        for match in re.finditer(r"[\"']([A-Za-z0-9_.\-]+)", block.group(1)):
            declared.add(match.group(1).lower().replace("_", "-"))
    _parse_py_dep_tables(content, declared)


def _parse_py_dep_tables(content: str, declared: set[str]) -> None:
    """Collect requirements from PEP 735 [dependency-groups] and
    [project.optional-dependencies] tables (section-scoped line scan)."""
    in_table = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            header = stripped.strip("[]")
            in_table = header.startswith("dependency-groups") or header.startswith(
                "project.optional-dependencies"
            )
            continue
        if not in_table or not stripped or stripped.startswith("#"):
            continue
        for match in re.finditer(r"[\"']([A-Za-z0-9_.\-]+)", stripped):
            declared.add(match.group(1).lower().replace("_", "-"))


def _add_package_name(entry: Path, local: set[str]) -> None:
    """A directory with __init__.py is importable by its own name."""
    if (entry / "__init__.py").exists():
        local.add(entry.name)


def _add_src_layout_names(entry: Path, local: set[str]) -> None:
    """src-layout packages: <dir>/src/<pkg>/__init__.py."""
    src = entry / "src"
    if not src.is_dir():
        return
    for sub in sorted(src.iterdir()):
        if sub.is_dir() and (sub / "__init__.py").exists():
            local.add(sub.name)


def _add_flat_layout_names(entry: Path, local: set[str]) -> None:
    """Flat sibling layout: modules one or two directories down
    (scripts/, src/), whose files import their siblings as bare names
    because the interpreter runs from that directory."""
    if entry.name in SKIP_DIRS or (entry / "__init__.py").exists():
        return
    for py in sorted(entry.glob("*.py")):
        local.add(py.stem)
    for nested in sorted(entry.iterdir()):
        if nested.is_dir() and nested.name not in SKIP_DIRS and not nested.name.startswith("."):
            for py in sorted(nested.glob("*.py")):
                local.add(py.stem)


def _local_modules(root: Path) -> set[str]:
    """Importable module names in the project tree: top-level files,
    packages, src layouts, and flat sibling layouts one directory down."""
    local: set[str] = set()
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.startswith("."):
            continue
        if entry.is_dir():
            _add_package_name(entry, local)
            _add_src_layout_names(entry, local)
            _add_flat_layout_names(entry, local)
        elif entry.suffix == ".py":
            local.add(entry.stem)
    return local


def collect_py_dependencies(root: Path) -> tuple[set[str], set[str]]:
    """Declared PyPI distributions and local importable module names."""
    declared: set[str] = set()
    local: set[str] = set()
    if root.is_dir():
        for pattern in ("requirements*.txt", "pyproject.toml"):
            for manifest in root.glob(pattern):
                _parse_py_manifest(manifest, declared)
        local = _local_modules(root)
    return declared, local


def collect_js_dependencies(root: Path) -> set[str]:
    manifest = root / "package.json" if root.is_dir() else None
    if not manifest or not manifest.is_file():
        return set()
    try:
        data = json.loads(read_text_lax(manifest))
    except (OSError, json.JSONDecodeError):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value.keys())
    return deps


def _is_declared(top: str, declared: set[str]) -> bool:
    """True when the module is declared, under any spelling convention."""
    norm = top.lower().replace("_", "-")
    aliased = PY_IMPORT_ALIASES.get(top, "").lower()
    if norm in declared or (aliased and aliased in declared):
        return True
    return norm.replace("-", "") in {d.replace("-", "") for d in declared}


def _py_import_tops(tree: ast.AST) -> list[tuple[str, int]]:
    """(top-level module, line) for every absolute import in the module.

    The inventory comes from the AST, not a line regex: prose such as
    the docstring sentence "from a small seed:" is not an import, and no
    sentence can become one (the self-scan false positive of 2026-11-18,
    on accuracy_benchmark.py). Relative imports (level > 0) have no
    external module to verify and are skipped.
    """
    tops: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.append((node.module.split(".")[0], node.lineno))
    return tops


def _py_undeclared_findings(
    path: Path,
    text: str,
    py_manifests: tuple[set[str], set[str]],
) -> list[Finding]:
    """Python imports that no manifest declares (advisory).

    A file the AST cannot parse yields no findings here -
    analysis-coverage already reports it as parse-failed, so the
    silence is named, never hidden.
    """
    declared, local = py_manifests
    if not declared and not local:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    seen: set[str] = set()
    for top, lineno in _py_import_tops(tree):
        if top in seen or top in STD_LIB_NAMES or top in local or _is_declared(top, declared):
            continue
        seen.add(top)
        findings.append(
            Finding(
                path=str(path),
                line=lineno,
                check="undeclared-import",
                message=(
                    f"import {top!r} is neither stdlib, local nor declared in a "
                    "manifest - verify it exists (slopsquatting)"
                ),
                weight=2,
            )
        )
    return findings[:5]


def _js_undeclared_findings(path: Path, text: str, js_deps: set[str]) -> list[Finding]:
    """npm packages that package.json does not declare (advisory)."""
    findings: list[Finding] = []
    seen_pkgs: set[str] = set()
    for match in re.finditer(
        r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']([^\"']+)[\"']", text
    ):
        spec = match.group(1)
        if spec.startswith((".", "/", "node:")):
            continue
        pkg = spec if spec.startswith("@") else spec.split("/")[0]
        if pkg in seen_pkgs or pkg in NODE_BUILTINS or pkg in js_deps:
            continue
        seen_pkgs.add(pkg)
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "undeclared-import",
                (
                    f"package {pkg!r} is not declared in package.json - verify it "
                    "exists (slopsquatting)"
                ),
                2,
            )
        )
    return findings[:5]


@dataclass(frozen=True)
class DepContext:
    """Manifest knowledge for the undeclared-import check."""

    root: Path
    py_manifests: tuple[set[str], set[str]]
    js_deps: set[str]


def check_undeclared_imports(
    path: Path,
    text: str,
    family: str,
    rel: str,
    deps: DepContext,
) -> list[Finding]:
    """Imports that no manifest declares (slopsquatting guard, advisory)."""
    if family == "py":
        return _py_undeclared_findings(path, text, deps.py_manifests)
    if family == "js" and deps.js_deps:
        return _js_undeclared_findings(path, text, deps.js_deps)
    return []
