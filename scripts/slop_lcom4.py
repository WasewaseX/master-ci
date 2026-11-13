"""LCOM4: class cohesion through connected method groups.

The roadmap's P1 metric layer opens with the audit's largest single gap:
no class-cohesion measurement. LCOM4 (Hitz & Montazeri, 1995) counts the
connected components of one class's method graph, where two methods are
joined when they touch the same instance attribute or one calls the
other. One component is one responsibility; two or more mean the class
carries unrelated duties and the honest fix is a split - the roadmap's
own bar: a class with five unrelated methods is worse than one with
twenty related ones.

The graph is native where class structure is sound today, one ecosystem
at a time (the same trade the import-cycle graph makes):

- Python - ast. Exact: attribute loads and stores on ``self``, calls
    through ``self``, and reads of ``@property`` names (a read of a
    property is a call to its getter, so it joins the graph the same
    way).
- JS/TS - the string-aware block scanner in slop_structural: class
    bodies by brace depth, methods by header shape, state through
    ``this.attr`` / ``this.#attr`` and calls through ``this.attr(...)``.

Go methods are package-scoped across files, so per-file class views
would be fiction; JVM, PHP, C#, Ruby and C++ need their native class
models (Roslyn, javac metadata, PHPStan, the Ruby parser) before their
graphs can be trusted - out of scope by design until then, like the
other repo-level oracles.

Exclusions are the classic ones, each with a false-positive reason:
constructors and dunder protocol methods are out (they touch every
field by construction and would glue unrelated groups into fake
cohesion), ``@staticmethod``/``@classmethod`` and JS ``static`` members
are out (they hold no instance state), and test files are out (a
TestCase's methods are independent assertions, not a responsibility
split). The verdict is deliberately conservative: a component whose
members touch no instance state is dropped before counting - a
stateless helper cluster is dispatch code, not a second responsibility
- and a class where every method is stateless is skipped entirely,
because cohesion over stateless functions is undefined. What remains
is reported per class: the LCOM4 count and the disconnected groups,
advisory, maintainability dimension, capped and deterministic.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from slop_files import is_ignored, is_test_path, read_text_lax
from slop_model import Finding
from slop_structural import _brace_depths, _close_line, _code_lines

# One measurement per class, capped so a pathological repo cannot flood
# the report; order is deterministic (sorted rel paths, class order).
MAX_LCOM4_FINDINGS = 12
MAX_GROUPS_RENDER = 4
MAX_NAMES_RENDER = 4

_PY_EXTS = (".py",)
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
COHESION_EXTS = frozenset(_PY_EXTS + _JS_EXTS)


@dataclass(frozen=True)
class MethodView:
    """One eligible method: its name, line and what it touches."""

    name: str
    line: int
    attrs: frozenset[str]
    calls: frozenset[str]


@dataclass(frozen=True)
class ClassView:
    """One class measured as a cohesion graph; line is the class header."""

    name: str
    line: int
    methods: tuple[MethodView, ...]


# --- Python: the ast-backed class view -------------------------------------


def _py_method(method: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodView | None:
    """One Python method as facts; None for excluded protocol members."""
    if method.name.startswith("__") and method.name.endswith("__"):
        return None  # dunder protocol: framework-invoked, not a responsibility
    for dec in method.decorator_list:
        dec_name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", "")
        if dec_name in {"staticmethod", "classmethod"}:
            return None  # no instance state, no place in the graph
    attrs: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self":
                attrs.add(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            calls.add(node.func.attr)
    return MethodView(method.name, method.lineno, frozenset(attrs), frozenset(calls))


def _py_methods(node: ast.ClassDef) -> list[MethodView]:
    """The eligible methods of one class body, in source order."""
    methods: list[MethodView] = []
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        method = _py_method(item)
        if method is not None:
            methods.append(method)
    return methods


def _py_classes(text: str) -> list[ClassView]:
    """Every class in one Python file, AST-backed."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[ClassView] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = _py_methods(node)
        properties = _py_properties(node)
        if properties:
            methods = _join_properties(methods, properties)
        out.append(ClassView(node.name, node.lineno, tuple(methods)))
    return out


def _py_properties(node: ast.ClassDef) -> set[str]:
    """Names the class exposes as properties (the getter method's name)."""
    return {
        item.name
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(dec, ast.Name) and dec.id == "property" for dec in item.decorator_list)
    }


# --- JS/TS: the scanner-backed class view -----------------------------------

_JS_CLASS = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:declare\s+)?"
    r"class\s+[A-Za-z_$][\w$]*(?:\s+extends\s+[^{]*)?"
)
_JS_HEADER = re.compile(
    r"^\s*(?:(?:public|private|protected|readonly|override|abstract|"
    r"async|get|set)\s+)*(?:\*\s*)?(#?[A-Za-z_$][\w$]*)\s*(?:<[^()]*>)?\s*\("
)
_JS_ARROW = re.compile(
    r"^\s*(?:readonly\s+)?(#?[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:function\s+)?"
    r"(?:<[^()]*>)?\(.*\)\s*(?::\s*[^=]*)?=>\s*\{"
)
_JS_CTRL = frozenset({"if", "for", "while", "switch", "catch", "return", "do", "else", "try"})
_JS_ATTR = re.compile(r"this\.(#?[A-Za-z_$][\w$]*)")
_JS_CALL = re.compile(r"this\.(#?[A-Za-z_$][\w$]*)\s*\??\(")


def _js_accessor(line: str, name_start: int) -> str | None:
    """The property name when the header declares a getter or setter."""
    if not re.search(r"\b(?:get|set)\b", line[:name_start]):
        return None
    match = _JS_HEADER.match(line)
    return match.group(1) if match else None


def _js_classes(text: str) -> list[ClassView]:
    """Every class in one JS/TS file, from the string-aware scanner view."""
    code_lines = _code_lines(text, "js")
    depth_end, _max_depth, _ctl = _brace_depths(code_lines)
    out: list[ClassView] = []
    idx = 0
    while idx < len(code_lines):
        if _JS_CLASS.match(code_lines[idx]):
            name = re.search(r"class\s+([A-Za-z_$][\w$]*)", code_lines[idx])
            open_idx = _js_open_line(code_lines, idx)
            if name and open_idx is not None:
                close = _close_line(depth_end, open_idx)
                if close is not None:
                    out.append(
                        _js_class_view(
                            name.group(1), idx + 1, code_lines, depth_end, open_idx, close
                        )
                    )
                    idx = close + 1
                    continue
        idx += 1
    return out


def _js_open_line(code_lines: list[str], idx: int) -> int | None:
    """The line carrying the class body's opening brace."""
    if code_lines[idx].rstrip().endswith("{"):
        return idx
    for nxt in range(idx + 1, min(idx + 3, len(code_lines))):
        if code_lines[nxt].strip().startswith("{"):
            return nxt
    return None


def _js_header_fact(code: str) -> tuple[str, int] | None:
    """(method name, name offset) when the line opens an instance method.

    Arrow-field methods and written-out signatures both qualify; control
    keywords, the constructor and ``static`` members never do.
    """
    arrow = _JS_ARROW.match(code)
    header = arrow or _JS_HEADER.match(code)
    if not header:
        return None
    opens = bool(re.search(r"=>\s*\{\s*$", code)) if arrow else code.rstrip().endswith("{")
    if not opens:
        return None
    name = header.group(1)
    if name in _JS_CTRL or name == "constructor":
        return None
    if re.search(r"\bstatic\b", code[: header.start(1)]):
        return None  # static: no instance state, no place in the graph
    return name, header.start(1)


def _js_method_fact(code_lines: list[str], name: str, idx: int, close: int) -> MethodView:
    """One method's facts from its body span: this.-attrs minus calls."""
    body = "\n".join(code_lines[idx : close + 1])
    calls = {m.group(1) for m in _JS_CALL.finditer(body)}
    attrs = {m.group(1) for m in _JS_ATTR.finditer(body)} - calls
    return MethodView(name, idx + 1, frozenset(attrs), frozenset(calls))


def _join_properties(methods: list[MethodView], properties: set[str]) -> list[MethodView]:
    """A read of a property is a call to its getter: join the graph."""
    return [
        MethodView(m.name, m.line, m.attrs, frozenset(m.calls | (m.attrs & properties)))
        for m in methods
    ]


def _js_class_view(
    name: str,
    line: int,
    code_lines: list[str],
    depth_end: list[int],
    open_idx: int,
    close_idx: int,
) -> ClassView:
    """One JS/TS class extracted from the brace-bookkeeping view."""
    body_depth = depth_end[open_idx]
    accessors: set[str] = set()
    methods: list[MethodView] = []
    idx = open_idx + 1
    while idx < close_idx:
        code = code_lines[idx]
        at_method_level = (depth_end[idx - 1] if idx else 0) == body_depth
        fact = _js_header_fact(code) if at_method_level else None
        if fact:
            name_tok, name_start = fact
            accessor = _js_accessor(code, name_start)
            if accessor:
                accessors.add(accessor)
            close = _close_line(depth_end, idx)
            if close is not None:
                methods.append(_js_method_fact(code_lines, name_tok, idx, close))
                idx = close + 1
                continue
        idx += 1
    if accessors:
        methods = _join_properties(methods, accessors)
    return ClassView(name, line, tuple(methods))


# --- the graph and the verdict ----------------------------------------------


def _components(methods: tuple[MethodView, ...]) -> list[list[MethodView]]:
    """Connected components over shared attributes and self-calls."""
    parent = list(range(len(methods)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (i, a), (j, b) in combinations(enumerate(methods), 2):
        if (a.attrs & b.attrs) or b.name in a.calls or a.name in b.calls:
            parent[find(i)] = find(j)
    groups: dict[int, list[MethodView]] = {}
    for i, method in enumerate(methods):
        groups.setdefault(find(i), []).append(method)
    return [sorted(group, key=lambda m: m.line) for group in groups.values()]


def _state_groups(methods: tuple[MethodView, ...]) -> list[list[MethodView]]:
    """Components that survive the conservative policy filter.

    A component none of whose members touch instance state is dropped:
    stateless helpers are dispatch, not a second responsibility, and a
    class with no state at all has no cohesion to measure.
    """
    if not any(m.attrs for m in methods):
        return []
    return [group for group in _components(methods) if any(m.attrs for m in group)]


def _render_groups(groups: list[list[MethodView]]) -> str:
    """The disconnected groups as bounded evidence for the message."""
    parts = []
    for group in groups[:MAX_GROUPS_RENDER]:
        names = ", ".join(m.name for m in group[:MAX_NAMES_RENDER])
        if len(group) > MAX_NAMES_RENDER:
            names += f", +{len(group) - MAX_NAMES_RENDER}"
        parts.append(f"[{names}]")
    text = ", ".join(parts)
    if len(groups) > MAX_GROUPS_RENDER:
        text += f", +{len(groups) - MAX_GROUPS_RENDER} more"
    return text


def _message(class_view: ClassView, groups: list[list[MethodView]]) -> str:
    """The evidence-first verdict text: count, budget, then the groups."""
    return (
        f"class {class_view.name}: LCOM4 {len(groups)} (budget 1): "
        f"disconnected method groups {_render_groups(groups)}"
    )


def _rels(root: Path, files: list[Path], globs: list[str]) -> list[tuple[Path, str]]:
    """Eligible (path, rel) pairs: class-family sources outside tests."""
    out: list[tuple[Path, str]] = []
    for path in files:
        if path.suffix.lower() not in COHESION_EXTS:
            continue
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs) or is_test_path(rel):
            continue
        out.append((path, rel))
    return out


def check_class_cohesion(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """LCOM4 per class across the graph-sound families (advisory, capped)."""
    findings: list[Finding] = []
    for path, rel in sorted(_rels(root, files, globs), key=lambda pair: pair[1]):
        text = read_text_lax(path)
        views = _py_classes(text) if path.suffix.lower() == ".py" else _js_classes(text)
        for view in views:
            groups = _state_groups(view.methods)
            if len(groups) < 2:
                continue
            findings.append(
                Finding(
                    path=rel,
                    line=view.line,
                    check="low-cohesion",
                    message=_message(view, groups),
                    weight=1,
                    advisory=True,
                )
            )
            if len(findings) >= MAX_LCOM4_FINDINGS:
                return findings
    return findings
