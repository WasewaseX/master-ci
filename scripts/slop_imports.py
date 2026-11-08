"""Phantom and undeclared import detection across language families."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from slop_model import Finding, _finding
from slop_patterns import NODE_BUILTINS, PY_IMPORT_ALIASES, SKIP_DIRS, STD_LIB_NAMES


def check_phantom_imports(path: Path, text: str, family: str) -> list[Finding]:
    """Imports that are never used in the file (advisory)."""
    if family == "py":
        return _py_phantom_imports(path, text)
    if family == "js":
        return _js_phantom_imports(path, text)
    return []


def _py_imported_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Collect (bound name, line) for every import in the module."""
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported.append((name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    name = alias.asname or alias.name
                    imported.append((name, node.lineno))
    return imported


def _py_unused_import_findings(
    path: Path,
    body: str,
    imported: list[tuple[str, int]],
) -> list[Finding]:
    findings: list[Finding] = []
    for name, lineno in imported[:40]:
        if name in ("__future__", "annotations"):
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
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("import ", "from "))
    )
    return _py_unused_import_findings(path, body, imported)


def _js_phantom_imports(path: Path, text: str) -> list[Finding]:
    import_pattern = r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']([^\"']+)[\"']"
    findings: list[Finding] = []
    seen_specs: set[str] = set()
    for match in re.finditer(import_pattern, text):
        spec = match.group(1)
        if spec.startswith((".", "/", "node:")) or spec in seen_specs:
            continue
        seen_specs.add(spec)
        cleaned = re.sub(
            r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']"
            + re.escape(spec)
            + r"[\"']",
            "",
            text,
        )
        if not re.search(rf"[\"']{re.escape(spec)}[\"']", cleaned) and spec not in cleaned:
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "phantom-import",
                    f"import {spec!r} is never referenced",
                    1,
                )
            )
    return findings[:5]


def _parse_py_manifest(manifest: Path, declared: set[str]) -> None:
    try:
        content = manifest.read_text(encoding="utf-8", errors="replace")
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


def _local_modules(root: Path) -> set[str]:
    """Importable module names in the project tree: top-level files,
    packages, src layouts, and modules one directory down (flat script
    layouts like scripts/ or src/, whose files import their siblings)."""
    local: set[str] = set()
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.startswith("."):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            local.add(entry.name)
        elif entry.is_dir() and (entry / "src").is_dir():
            for sub in sorted((entry / "src").iterdir()):
                if sub.is_dir() and (sub / "__init__.py").exists():
                    local.add(sub.name)
        elif entry.suffix == ".py":
            local.add(entry.stem)
        if entry.is_dir() and entry.name in SKIP_DIRS:
            continue
        if entry.is_dir() and not (entry / "__init__.py").exists():
            # Flat sibling layout: modules beside the importer resolve
            # when the interpreter runs from that directory.
            for py in sorted(entry.glob("*.py")):
                local.add(py.stem)
            for nested in sorted(entry.iterdir()):
                if (
                    nested.is_dir()
                    and nested.name not in SKIP_DIRS
                    and not nested.name.startswith(".")
                ):
                    for py in sorted(nested.glob("*.py")):
                        local.add(py.stem)
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
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value.keys())
    return deps


def _py_undeclared_findings(
    path: Path,
    text: str,
    py_manifests: tuple[set[str], set[str]],
) -> list[Finding]:
    """Python imports that no manifest declares (advisory)."""
    declared, local = py_manifests
    if not declared and not local:
        return []
    findings: list[Finding] = []
    seen: set[str] = set()
    for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE):
        top = match.group(1).split(".")[0]
        if top in seen:
            continue
        seen.add(top)
        norm = top.lower().replace("_", "-")
        if top in STD_LIB_NAMES or top in local:
            continue
        aliased = PY_IMPORT_ALIASES.get(top, "").lower()
        if norm in declared or (aliased and aliased in declared):
            continue
        if norm.replace("-", "") in {d.replace("-", "") for d in declared}:
            continue
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "undeclared-import",
                (
                    f"import {top!r} is neither stdlib, local nor declared in a "
                    "manifest - verify it exists (slopsquatting)"
                ),
                2,
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
