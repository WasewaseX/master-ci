"""Python IR extractor: the native ``ast`` module lowers ``.py`` files.

The standard-library parser is compiler-grade for the language this
repository itself is written in, so the strongest extractor ships with
the interpreter: symbols with real nesting (methods inside classes),
imports with their bound names, call sites, and the identifier pool.
Nothing textual is consulted - a name that appears only inside a
comment or a string literal never reaches the IR, which is the
precision property the semantic-dead-symbol check and the architecture
benchmark measure. A parse failure rides out as ``parse_ok=False``
with the reason; a failed parse never masquerades as a clean file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from slop_files import read_text_lax
from slop_ir import EXTRACTORS, FileIR, ImportIR, SymbolIR
from slop_patterns import MAX_FILE_BYTES

# Container nodes whose bodies may hold module- or class-level defs.
_CONTAINER_BODIES: dict[type, tuple[str, ...]] = {
    ast.If: ("body", "orelse"),
    ast.Try: ("body", "orelse", "finalbody"),
    ast.With: ("body",),
    ast.AsyncWith: ("body",),
    ast.For: ("body", "orelse"),
    ast.AsyncFor: ("body", "orelse"),
    ast.While: ("body",),
}


def _all_target_names(node: ast.Assign) -> set[str]:
    """The string constants of one ``__all__ = [...]`` assignment."""
    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
    if "__all__" not in targets or not isinstance(node.value, (ast.List, ast.Tuple)):
        return set()
    return {
        elt.value
        for elt in node.value.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }


def _dunder_all(tree: ast.Module) -> set[str]:
    """Names a module-level ``__all__`` lists, when it is a plain literal."""
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            exported |= _all_target_names(node)
    return exported


def _visit_body(
    body: list[ast.stmt],
    prefix: str,
    exported_all: set[str],
    symbols: list[SymbolIR],
    in_class: bool,
) -> None:
    """One lexical scope's defs, recursing through classes and containers."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if in_class else "function"
            qual = f"{prefix}{node.name}"
            exported = not node.name.startswith("_") or node.name in exported_all
            symbols.append(
                SymbolIR(
                    kind=kind, name=node.name, qualname=qual, line=node.lineno, exported=exported
                )
            )
            _visit_body(node.body, f"{qual}.", exported_all, symbols, in_class=False)
        elif isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}"
            exported = not node.name.startswith("_") or node.name in exported_all
            symbols.append(
                SymbolIR(
                    kind="class", name=node.name, qualname=qual, line=node.lineno, exported=exported
                )
            )
            _visit_body(node.body, f"{qual}.", exported_all, symbols, in_class=True)
        else:
            for attr in _CONTAINER_BODIES.get(type(node), ()):
                _visit_body(getattr(node, attr), prefix, exported_all, symbols, in_class)


def _extract_symbols(tree: ast.Module, exported_all: set[str]) -> list[SymbolIR]:
    """Module-level walk: top-level defs plus their nested scopes."""
    symbols: list[SymbolIR] = []
    _visit_body(tree.body, "", exported_all, symbols, in_class=False)
    return symbols


def _extract_imports(tree: ast.Module) -> list[ImportIR]:
    """One ImportIR per import statement, with the names it binds."""
    imports: list[ImportIR] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            imports.append(ImportIR(module="", names=names, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            dots = "." * node.level
            module = f"{dots}{node.module or ''}"
            names = [alias.name for alias in node.names]
            imports.append(ImportIR(module=module, names=names, line=node.lineno))
    return imports


def _call_name(func: ast.expr) -> str | None:
    """The callee name of a call's function expression, when nameable."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _extract_uses(tree: ast.Module) -> tuple[list[tuple[str, int]], list[str]]:
    """The identifier pool and the call names, from real parse nodes."""
    identifiers: list[tuple[str, int]] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute):
            identifiers.append((node.attr, node.lineno))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                calls.append(name)
    return identifiers, calls


def extract_python(path: Path) -> FileIR:
    """Lower one ``.py`` file to the shared IR."""
    parser = EXTRACTORS["python"]["parser"]
    ir = FileIR(path=str(path), language="python", parser=parser, parse_ok=False)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            ir.parse_error = "file exceeds MAX_FILE_BYTES"
            return ir
        text = read_text_lax(path)
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError) as exc:
        ir.parse_error = f"{type(exc).__name__}: {exc}"
        return ir
    ir.parse_ok = True
    exported_all = _dunder_all(tree)
    ir.symbols = _extract_symbols(tree, exported_all)
    ir.imports = _extract_imports(tree)
    ir.identifiers, ir.calls = _extract_uses(tree)
    return ir
