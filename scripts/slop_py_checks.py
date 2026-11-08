"""Deterministic content-defect checks: elided code, placeholders, Python AST."""

from __future__ import annotations

import ast
from pathlib import Path

from slop_model import Finding, _finding
from slop_patterns import ELIDED_PATTERNS, PLACEHOLDER_IMPL_PATTERNS, PLACEHOLDER_NOTE_PATTERNS
from slop_structural import BLOAT_MAX_PARAMS, GOD_FUNCTION_LINES, MAX_NESTING


def check_elided_code(path: Path, text: str) -> list[Finding]:
    for pattern in ELIDED_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "elided-code",
                    f"elided code marker {pattern.pattern!r}",
                    3,
                )
            ]
    return []


def check_placeholder_impl(path: Path, text: str) -> list[Finding]:
    for pattern in PLACEHOLDER_IMPL_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-impl",
                    f"placeholder implementation: {pattern.pattern!r}",
                    2,
                )
            ]
    for pattern in PLACEHOLDER_NOTE_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-note",
                    "template note left in code",
                    1,
                )
            ]
    return []


# Control-flow nodes a reader has to track as one more nesting level.
_NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
)
# Nested definitions are measured on their own; their internal depth
# must not contaminate the enclosing function's budget.
_FUNC_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _nesting_depth(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNC_BOUNDARY):
            continue
        step = depth + 1 if isinstance(child, _NESTING_NODES) else depth
        best = max(best, _nesting_depth(child, step))
    return best


def _py_func_findings(path: Path, node) -> list[Finding]:
    """Size, nesting and mutable-default checks for one Python function."""
    findings: list[Finding] = []
    length = (node.end_lineno or node.lineno) - node.lineno + 1
    if length > GOD_FUNCTION_LINES:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="god-function",
                message=f"function {node.name!r} spans {length} lines",
                weight=1,
                advisory=True,
            )
        )
    if _nesting_depth(node) > MAX_NESTING:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="deep-nesting",
                message=f"function {node.name!r} nests more than {MAX_NESTING} levels",
                weight=1,
                advisory=True,
            )
        )
    defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
    if any(
        isinstance(d, (ast.List, ast.Dict, ast.Set))
        or (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Name)
            and d.func.id in ("list", "dict", "set")
        )
        for d in defaults
    ):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="mutable-default",
                message=f"function {node.name!r} has a mutable default argument",
                weight=1,
                advisory=True,
            )
        )
    params = (
        len(node.args.posonlyargs)
        + len(node.args.args)
        + len(node.args.kwonlyargs)
        + (1 if node.args.vararg else 0)
        + (1 if node.args.kwarg else 0)
    )
    if params > BLOAT_MAX_PARAMS:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="bloat-param-count",
                message=(
                    f"function {node.name!r} takes {params} parameters (budget {BLOAT_MAX_PARAMS})"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


def _py_stub_body(body: list[ast.stmt]) -> bool:
    stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(stmts) != 1:
        return False
    only = stmts[0]
    if isinstance(only, ast.Pass):
        return True
    if isinstance(only, ast.Raise):
        exc = only.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            return exc.func.id in ("NotImplementedError", "NotImplemented")
        if isinstance(exc, ast.Name):
            return exc.id == "NotImplementedError"
    return False


# Context managers that assert behavior: a `with pytest.raises(ValueError):`
# body fails when the exception is not raised, exactly like an `assert`.
_RAISES_STYLE_NAMES = frozenset(
    {"raises", "assertRaises", "assertWarns", "warns", "deprecated_call", "fail"}
)


def _py_has_assert(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.Assert, ast.Raise)):
            return True
        if isinstance(child, ast.withitem) and isinstance(child.context_expr, ast.Call):
            func = child.context_expr.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else None)
            )
            if name in _RAISES_STYLE_NAMES:
                return True
    return False


def _py_def_findings(path: Path, node) -> list[Finding]:
    """FunctionDef branch: stub bodies, assertion-less tests, function smells."""
    findings: list[Finding] = []
    if _py_stub_body(node.body):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="stub-function",
                message=f"function {node.name!r} has a stub-only body",
                weight=2,
            )
        )
    if node.name.startswith("test_") and not _py_has_assert(node):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="empty-test",
                message=f"test {node.name!r} contains no assert or raise",
                weight=2,
            )
        )
    findings.extend(_py_func_findings(path, node))
    return findings


def _py_handler_findings(path: Path, node: ast.ExceptHandler) -> list[Finding]:
    """ExceptHandler branch: bare excepts and swallowed exceptions."""
    if node.type is not None and not all(isinstance(s, ast.Pass) for s in node.body):
        return []
    if node.type is None:
        return [
            Finding(
                path=str(path),
                line=node.lineno,
                check="bare-except",
                message="bare `except:` catches everything including SystemExit",
                weight=2,
            )
        ]
    return [
        Finding(
            path=str(path),
            line=node.lineno,
            check="swallowed-exception",
            message="exception swallowed (pass-only handler)",
            weight=2,
        )
    ]


def check_py_ast(path: Path, text: str, family: str) -> list[Finding]:
    """AST-backed Python checks: stubs, empty tests, exceptions, smells."""
    if family != "py":
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_py_def_findings(path, node))
        elif isinstance(node, ast.ExceptHandler):
            findings.extend(_py_handler_findings(path, node))
        elif isinstance(node, ast.ImportFrom) and node.module and node.names:
            if any(a.name == "*" for a in node.names):
                findings.append(
                    Finding(
                        path=str(path),
                        line=node.lineno,
                        check="star-import",
                        message=f"star import from {node.module!r}",
                        weight=1,
                        advisory=True,
                    )
                )
    if _has_chained_dict_get(tree):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="chained-dict-get",
                message="chained .get(..., {}) defaults hide missing-data cases",
                weight=1,
                advisory=True,
            )
        )
    return findings


def _has_chained_dict_get(tree: ast.AST) -> bool:
    """True when a .get(...) with an empty-container default feeds another .get."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == "get"
        ):
            inner = node.func.value
            if len(inner.args) >= 2 and isinstance(inner.args[1], (ast.Dict, ast.List)):
                return True
    return False


# Bloat budget: function, file and comment-pressure checks. Python is
# measured through its AST; brace families through a line scanner; Ruby
# through def/end pairing. Every check is advisory, and every function
# over a budget is reported - not just the worst one.
