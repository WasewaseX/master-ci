"""Per-language import-edge extractors behind the cycle oracle.

One module per edge language family: the regex tables and resolvers that
turn source text into ``(node, line)`` edges for slop_cycles' graph.
Resolution is strictly conservative - an edge only exists when the
target resolves to a file the scan already sees, so a hallucinated or
external import never manufactures a cycle. Dynamic ``import()`` is
deliberately excluded: a lazy edge is a legitimate way to *break* a
cycle, and counting it would punish the fix. (Split out of
slop_cycles.py when that file outgrew its own 400-line budget.)
"""

from __future__ import annotations

import ast
import posixpath
import re
from pathlib import Path, PurePosixPath

from slop_files import read_text_lax

_JS_EXTS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
_PY_EXTS = (".py",)
_RB_EXTS = (".rb",)
_GO_EXTS = (".go",)

# Static ESM edges: `import x from './y'`, `import './y'`, `export ... from './y'`.
# Anchored at line start so dynamic `await import(...)` never matches.
_JS_EDGE = re.compile(
    r"^\s*(?:import\s+[^'\"();]*?\s*from\s*|import\s*|export\s+[^'\"();]*?\s*from\s*)"
    r"[\"'](\.[^\"']+)[\"']",
    re.MULTILINE,
)
_JS_REQUIRE = re.compile(r"[^\w.]require\s*\(\s*[\"'](\.[^\"']+)[\"']\s*\)")
_JS_REEXPORT = re.compile(r"^\s*export\s+(?:\*|\{[^}]*\})\s+from\s")

_GO_BLOCK = re.compile(r"^\s*import\s*\(([^)]*)\)", re.MULTILINE)
_GO_ONE = re.compile(r"^\s*import\s+(?:_\s+|[.\w]+\s+)?[\"']([^\"']+)[\"']", re.MULTILINE)
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)

_RB_RELATIVE = re.compile(r"^\s*require_relative\s+[\"']([^\"']+)[\"']", re.MULTILINE)


def _probe(file_set: set[str], stem: str) -> str | None:
    """Resolve a file node from an extension-less stem, or None."""
    if stem in file_set:
        return stem
    for ext in _PY_EXTS + _JS_EXTS + _RB_EXTS:
        if stem + ext in file_set:
            return stem + ext
    return None


def _first_with_ext(candidate: str, file_set: set[str]) -> str | None:
    for ext in _PY_EXTS + _JS_EXTS + _RB_EXTS:
        if candidate + ext in file_set:
            return candidate + ext
    return None


def _js_probe(file_set: set[str], spec: str, parent: str) -> str | None:
    """Resolve './x' against the known files: exact, ext, ts-swap, index."""
    stem = posixpath.normpath(posixpath.join(parent, spec))
    for candidate in (stem, *_swap_variants(stem)):
        if candidate in file_set:
            return candidate
        with_ext = _first_with_ext(candidate, file_set)
        if with_ext:
            return with_ext
    return _probe(file_set, f"{stem}/index")


def _swap_variants(stem: str) -> list[str]:
    """TypeScript's compiled-extension convention: './b.js' means './b.ts'."""
    swaps = {".js": ".ts", ".jsx": ".tsx", ".mjs": ".mts", ".cjs": ".cts"}
    base, ext = posixpath.splitext(stem)
    return [base + swaps[ext]] if ext in swaps else []


def _py_edges(rel: str, text: str, file_set: set[str]) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    parent = posixpath.dirname(rel)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(_py_plain_import(node, file_set))
        elif isinstance(node, ast.ImportFrom):
            out.extend(_py_from_import(node, parent, file_set))
    return out


def _py_plain_import(node: ast.Import, file_set: set[str]) -> list[tuple[str, int]]:
    """`import a.b.c` - one edge to the deepest resolvable module file."""
    out: list[tuple[str, int]] = []
    for alias in node.names:
        target = _resolve_stem("", alias.name.split("."), file_set)
        if target:
            out.append((target, node.lineno))
    return out


def _py_from_import(node: ast.ImportFrom, parent: str, file_set: set[str]) -> list[tuple[str, int]]:
    """`from a import b` edges, relative or absolute."""
    base = _base_dir(parent, node.level)
    module_parts = (node.module or "").split(".") if node.module else []
    out: list[tuple[str, int]] = []
    for alias in node.names:
        if alias.name == "*":
            continue
        target = _py_from_target(base, module_parts, alias.name, file_set)
        if target:
            out.append((target, node.lineno))
    return out


def _py_from_target(
    base: str, module_parts: list[str], name: str, file_set: set[str]
) -> str | None:
    # `from a import b`: b may be a submodule of package a, or an attribute
    # of module a - try the submodule first, fall back to the module itself.
    # The package __init__ is glue and already absent from the file set.
    target = _resolve_stem(base, module_parts + [name], file_set)
    if target is None and module_parts:
        return _resolve_stem(base, module_parts, file_set)
    return target


def _base_dir(parent: str, level: int) -> str:
    """Base dir an ImportFrom at the given level resolves against.

    Level 0 is an absolute import from the scan root; level 1 is the
    file's own package; each further level climbs one package.
    """
    if level == 0:
        return ""
    base = parent
    for _ in range(level - 1):
        base = posixpath.dirname(base)
    return base


def _resolve_stem(base: str, parts: list[str], file_set: set[str]) -> str | None:
    if not parts:
        return None
    stem = posixpath.normpath(posixpath.join(base, *parts)) if base else posixpath.join(*parts)
    if stem.startswith(".."):
        return None
    return _probe(file_set, stem)


def _js_edges(rel: str, text: str, file_set: set[str]) -> list[tuple[str, int]]:
    parent = posixpath.dirname(rel)
    out: list[tuple[str, int]] = []
    for match in _JS_EDGE.finditer(text):
        target = _js_probe(file_set, match.group(1), parent)
        if target:
            out.append((target, text[: match.start()].count("\n") + 1))
    for match in _JS_REQUIRE.finditer(text):
        target = _js_probe(file_set, match.group(1), parent)
        if target:
            out.append((target, text[: match.start()].count("\n") + 1))
    return out


def _go_module(root: Path) -> str | None:
    manifest = root / "go.mod"
    if not manifest.is_file():
        return None
    match = _GO_MODULE.search(read_text_lax(manifest))
    return match.group(1) if match else None


def _go_edges(rel: str, text: str, module: str, go_dirs: set[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    paths: list[tuple[str, int]] = []
    for block in _GO_BLOCK.finditer(text):
        base = text[: block.start()].count("\n") + 1
        for offset, line in enumerate(block.group(1).splitlines(), 1):
            one = re.search(r"[\"']([^\"']+)[\"']", line)
            if one:
                paths.append((one.group(1), base + offset - 1))
    for match in _GO_ONE.finditer(text):
        paths.append((match.group(1), text[: match.start()].count("\n") + 1))
    for path, line in paths:
        if path == module:
            continue  # the module root package: skipped by design
        if not path.startswith(module + "/"):
            continue
        pkg = path[len(module) + 1 :]
        if pkg in go_dirs:
            out.append((pkg, line))
    return out


def _rb_edges(rel: str, text: str, file_set: set[str]) -> list[tuple[str, int]]:
    parent = posixpath.dirname(rel)
    out: list[tuple[str, int]] = []
    for match in _RB_RELATIVE.finditer(text):
        stem = posixpath.normpath(posixpath.join(parent, match.group(1)))
        target = _probe(file_set, stem)
        if target:
            out.append((target, text[: match.start()].count("\n") + 1))
    return out


def _is_glue(rel: str, text: str) -> bool:
    """Namespace glue: package init or a pure re-export barrel."""
    name = PurePosixPath(rel).name
    if name == "__init__.py":
        return True
    if rel.endswith(_JS_EXTS):
        effective = [
            line for line in text.splitlines() if line.strip() and not line.strip().startswith("//")
        ]
        return bool(effective) and all(_JS_REEXPORT.match(line) for line in effective)
    return False
