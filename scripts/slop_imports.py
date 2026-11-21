"""Phantom and undeclared import detection across language families."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from slop_files import read_text_lax
from slop_model import Finding, _finding
from slop_patterns import NODE_BUILTINS, PY_IMPORT_ALIASES, SKIP_DIRS, STD_LIB_NAMES
from slop_span_mask import mask_span_forms, string_spans

# Scoped suppression on an import line (ruff/flake8 F401 convention).
_NOQA_RE = re.compile(r"#\s*noqa\b")

# Static imports (multi-line lists included - a negated class matches
# the newline), re-exports, dynamic import() and require(): a package
# only those shapes name is undeclared too.
_JS_IMPORT_RE = re.compile(
    r"(?:import\s+[^'\"]*?from\s+|import\s*\(\s*|import\s+|"
    r"export\s+[^'\"]*?from\s+|require\s*\(\s*)[\"']([^\"']+)[\"']"
)

# A specifier whose first part fails this npm-name grammar names no
# package: ``@/lib/...`` (empty scope), ``#app/config``, ``~/lib/a``.
_JS_PKG_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The js phantom-import question is binding-aware (audit finding A,
# 2026-11-17): an import is unused only when NONE of the names it binds
# appears outside the import statements - the package-string search the
# pre-v2.18 check ran false-positived 8 of 10 usage shapes.


from slop_js_bindings import _js_phantom_imports  # noqa: E402 - grammar lives beside the parser
from slop_js_manifests import (  # noqa: E402,F401 - the declared-set surface lives beside its walkers
    collect_js_alias_prefixes,
    collect_js_dependencies,
)


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


def _package_of(spec: str) -> str | None:
    """The npm package a bare specifier names, or None when it names no package.

    ``@scope/pkg/sub`` names ``@scope/pkg`` - the subpath rides on the
    same declaration; ``@scope/pkg`` alone too. ``@/components/ui``
    (an empty scope) and ``#app/config`` (a Node subpath import,
    declared in package.json's own ``imports`` field) are resolver
    features, not packages, and never reach the check.
    """
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        if not scope or not rest:
            return None
        pkg, _, _sub = rest.partition("/")
        if not _JS_PKG_PART.fullmatch(pkg):
            return None
        return f"@{scope}/{pkg}"
    pkg = spec.partition("/")[0]
    if not pkg or not _JS_PKG_PART.fullmatch(pkg):
        return None
    return pkg


def _js_undeclared_findings(
    path: Path,
    text: str,
    js_deps: set[str],
    aliases: frozenset[str] = frozenset(),
) -> list[Finding]:
    """npm packages that no manifest declares (advisory).

    The declaration sites this check trusts: every ``package.json`` dep
    section in the tree (workspace members included - a monorepo
    package is importable by its own name), tsconfig/jsconfig
    ``paths`` aliases, Node ``#`` subpath imports, ``node:`` builtins,
    and any specifier the package grammar refuses. A bare package the
    grammar accepts that none of those sites names is the finding -
    the slopsquatting shape, and the project's own ``eslint-plugin-*``
    gap when a config file imports it.

    The scan reads a comment-blanked copy (string contents kept, every
    offset intact), so an import that exists only as a comment -
    ``// export { foo } from 'ghost-package'`` - or inside a regular
    expression's body imports nothing, and a specifier quoted inside a
    longer string or template is example text, not a declaration: only
    a specifier whose quote opens a top-level literal is code.
    """
    findings: list[Finding] = []
    seen_pkgs: set[str] = set()
    code_only = mask_span_forms(text, quotes="\"'`", keep_strings=True)
    spans = string_spans(code_only)
    for match in _JS_IMPORT_RE.finditer(code_only):
        spec = match.group(1)
        if _quoted_inside_string(match.start(1) - 1, spans):
            continue
        if spec.startswith((".", "/", "node:", "#", "data:")):
            continue
        if _is_alias(spec, aliases):
            continue
        pkg = _package_of(spec)
        if pkg is None or pkg in seen_pkgs or pkg in NODE_BUILTINS or pkg in js_deps:
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


def _quoted_inside_string(quote_pos: int, spans: list[tuple[int, int]]) -> bool:
    """True when the specifier's opening quote sits inside another literal.

    A top-level string's own quote starts its span, so it never
    matches; a quote inside a template or an outer string - the
    ``"import x from 'ghost'"`` example shape - does.
    """
    return any(start < quote_pos < end for start, end in spans)


def _is_alias(spec: str, aliases: frozenset[str]) -> bool:
    """True when a tsconfig/jsconfig ``paths`` prefix renames the specifier.

    The prefix matches at a path-segment boundary: alias ``@app``
    covers ``@app/components/x`` but not ``@app-data/x``.
    """
    return any(spec == alias or spec.startswith(alias + "/") for alias in aliases)


@dataclass(frozen=True)
class DepContext:
    """Manifest knowledge for the undeclared-import check."""

    root: Path
    py_manifests: tuple[set[str], set[str]]
    js_deps: set[str]
    js_aliases: frozenset[str] = frozenset()


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
        return _js_undeclared_findings(path, text, deps.js_deps, deps.js_aliases)
    return []
