"""tree-sitter IR extractor for the TS/TSX/JS/Go families.

One grammar pack, one walk per language family. The capability is
syntax-grade: definitions, imports, calls and the identifier pool come
from the parse tree; scope-aware resolution (module systems, types,
overloads) stays with the language-native engines the architecture doc
lists. This module never pretends to type-check.

Failure is reported, never guessed around: a missing grammar or a tree
that carries error nodes yields ``parse_ok=False`` with the reason, so
a failed parse can not masquerade as a clean file and the
analysis-coverage story keeps its teeth.

The walker is iterative on purpose - deeply chained expressions would
overflow a recursive visitor's frame stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from slop_files import read_text_lax
from slop_ir import EXTRACTORS, FileIR, ImportIR, SymbolIR
from slop_patterns import MAX_FILE_BYTES

try:
    from tree_sitter_language_pack import get_parser  # type: ignore[import-untyped]

    _PACK_ERROR = ""
except ImportError as exc:  # the pack ships with the scanner dependency group
    get_parser = None  # type: ignore[assignment]
    _PACK_ERROR = str(exc)

#: definition node type -> symbol kind, per language family
_DEF_NODES: dict[str, dict[str, str]] = {
    "typescript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
    },
    "tsx": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
    },
}

_NAME_NODES = ("identifier", "property_identifier")
_MAX_CHILDREN = 400


def _first_name(node) -> str:
    """The def node's name: the first identifier-shaped child's text."""
    for child in node.children:
        if child.type in _NAME_NODES:
            return child.text.decode("utf-8", "replace")
    return ""


def _def_symbol(
    node, def_kind: str, language: str, in_export: bool, prefix: str
) -> SymbolIR | None:
    """The SymbolIR for one definition node, or None when nameless."""
    name = _first_name(node)
    if not name:
        return None
    exported = in_export or (language == "go" and name[:1].isupper())
    return SymbolIR(
        kind=def_kind,
        name=name,
        qualname=f"{prefix}{name}",
        line=node.start_point[0] + 1,
        exported=exported,
    )


def _absorb_import(node, language: str, ir: FileIR) -> bool:
    """One import node into the IR; True when fully handled."""
    if node.type == "import_statement":
        imp = _ts_import(node)
        if imp:
            ir.imports.append(imp)
        return False  # its identifiers still count as uses
    if node.type == "import_spec" and language == "go":
        imp = _go_import(node)
        if imp:
            ir.imports.append(imp)
        return True
    return False


def _absorb_node(node, language: str, ir: FileIR) -> None:
    """Calls, imports and identifiers for one non-definition node."""
    if node.type == "call_expression":
        callee = _callee_name(node)
        if callee:
            ir.calls.append(callee)
        return
    if _absorb_import(node, language, ir):
        return
    if node.type in _NAME_NODES:
        text = node.text.decode("utf-8", "replace")
        ir.identifiers.append((text, node.start_point[0] + 1))


@dataclass
class _WalkState:
    """The walk's accumulating IR and its pending node stack."""

    ir: FileIR
    stack: list


def _expand_def(
    node,
    def_kind: str,
    language: str,
    in_export: bool,
    prefix: str,
    state: _WalkState,
) -> bool:
    """One definition into the IR with its scoped children; True when handled."""
    symbol = _def_symbol(node, def_kind, language, in_export, prefix)
    if symbol is None:
        return False
    state.ir.symbols.append(symbol)
    inner = f"{prefix}{symbol.name}."
    state.stack.extend((child, False, inner) for child in node.children)
    return True


def _walk(root, language: str, ir: FileIR) -> None:
    """Iterative parse-tree walk collecting defs, uses, calls, imports."""
    def_nodes = _DEF_NODES[language]
    state = _WalkState(ir=ir, stack=[(root, False, "")])
    while state.stack:
        node, in_export, prefix = state.stack.pop()
        if node.type == "export_statement":
            state.stack.extend((child, True, prefix) for child in node.children)
            continue
        def_kind = def_nodes.get(node.type)
        if def_kind and _expand_def(node, def_kind, language, in_export, prefix, state):
            continue
        _absorb_node(node, language, ir)
        if len(node.children) <= _MAX_CHILDREN:
            state.stack.extend((child, in_export, prefix) for child in node.children)


def _first_child_text(node, node_type: str) -> str:
    """The first child of the given type's text, or empty."""
    for sub in node.children:
        if sub.type == node_type:
            return sub.text.decode("utf-8", "replace")
    return ""


def _callee_name(call_node) -> str:
    """A call's callee: plain identifier or the member's property name."""
    for child in call_node.children:
        if child.type in _NAME_NODES:
            return child.text.decode("utf-8", "replace")
        if child.type == "member_expression":
            return _first_child_text(child, "property_identifier")
        if child.type == "selector_expression":
            return _first_child_text(child, "field_identifier")
    return ""


def _ts_import(node) -> ImportIR | None:
    """A TS/JS import statement: module string plus the names it binds."""
    module = ""
    names: list[str] = []
    for child in node.children:
        if child.type == "string":
            module = child.text.decode("utf-8", "replace").strip("'\"")
        if child.type == "import_clause":
            names = [
                sub.text.decode("utf-8", "replace")
                for sub in child.children
                if sub.type in _NAME_NODES
            ]
    if not module:
        return None
    return ImportIR(module=module, names=names, line=node.start_point[0] + 1)


def _go_import(node) -> ImportIR | None:
    """A Go import spec: the quoted path; the name travels with it."""
    for child in node.children:
        if child.type == "interpreted_string_literal":
            module = child.text.decode("utf-8", "replace").strip("'\"")
            return ImportIR(module=module, names=[], line=node.start_point[0] + 1)
    return None


def extract_treesitter(path: Path, language: str) -> FileIR:
    """Lower one TS/TSX/JS/Go file to the shared IR."""
    parser_family = EXTRACTORS[language]["parser"]
    ir = FileIR(path=str(path), language=language, parser=parser_family, parse_ok=False)
    if get_parser is None:
        ir.parse_error = f"tree-sitter language pack unavailable: {_PACK_ERROR}"
        return ir
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            ir.parse_error = "file exceeds MAX_FILE_BYTES"
            return ir
        parser = get_parser(language)
        text = read_text_lax(path)
        tree = parser.parse(text.encode("utf-8"))
    except (OSError, KeyError, ValueError) as exc:
        ir.parse_error = f"{type(exc).__name__}: {exc}"
        return ir
    if tree.root_node.has_error:
        ir.parse_error = "grammar reported error nodes"
    _walk(tree.root_node, language, ir)
    ir.parse_ok = not ir.parse_error
    return ir
