"""Test-redundancy oracle: exact duplicate test bodies in Python test files.

This is the conservative first oracle, stated honestly: two tests are
redundant twins only when their bodies are EXACT duplicates once
normalized. Normalization strips the function's docstring and excludes
the function's own name, then compares the remaining statements as one
``ast.dump`` - identifier names inside the body still count, so renaming
a variable is a real difference at this strictness. Near-duplicates
(bodies that differ in a literal or an assertion) and parameterized or
assertion-overlap analyses are future widening, deliberately not
shipped as guesses.

Eligibility follows the engine's path oracle: ``is_test_path`` governs
and the file must be Python, so a production module never yields the
finding no matter how test-shaped its functions look. The pool is the
module-level ``test_*`` functions plus one class depth of ``test_*``
methods (what pytest collects); their decorators and signatures are not
part of the compared body. A body reduced to a docstring, ``pass`` or
``...`` is the empty-test check's verdict, not redundancy. One advisory
finding per duplicate group, evidence naming the twins with their line
numbers, capped like the other evidence renders.
"""

from __future__ import annotations

import ast
from pathlib import Path

from slop_files import is_test_path
from slop_model import Finding

# Evidence bound per group, the slop_lcom4 render convention.
MAX_TWINS_RENDER = 4


def _normalized_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The stable dump of one test's body: docstring out, name never enters.

    None when the body has no comparable statements: a docstring-only,
    ``pass``-only or ``...``-only test cannot fail (the empty-test
    check's verdict) and cannot be redundant with anything either.
    """
    body = list(func.body)
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]
    if len(body) == 1 and (
        isinstance(body[0], ast.Pass)
        or (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant))
    ):
        return None
    if not body:
        return None
    return ast.dump(ast.Module(body=body, type_ignores=[]))


_TEST_CALLABLE = (ast.FunctionDef, ast.AsyncFunctionDef)


def _test_functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every ``test_*`` callable pytest would collect, in definition order.

    Module-level functions and one class depth of methods (``Class.test``
    evidence names); deeper nesting is out of the conservative pool.
    """
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, _TEST_CALLABLE) and node.name.startswith("test_"):
            found.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            found.extend(
                (f"{node.name}.{item.name}", item)
                for item in node.body
                if isinstance(item, _TEST_CALLABLE) and item.name.startswith("test_")
            )
    return found


def _group_finding(path: Path, twins: list[tuple[str, int]]) -> Finding:
    """One advisory finding per duplicate group; the twins are the evidence."""
    rendered = ", ".join(f"{name} (line {line})" for name, line in twins[:MAX_TWINS_RENDER])
    if len(twins) > MAX_TWINS_RENDER:
        rendered += f", +{len(twins) - MAX_TWINS_RENDER} more"
    return Finding(
        path=str(path),
        line=twins[0][1],
        check="redundant-test",
        message=f"{len(twins)} tests share one exact normalized body: {rendered}",
        weight=1,
        advisory=True,
    )


def check_test_redundancy(path: Path, text: str, rel: str) -> list[Finding]:
    """Exact duplicate test bodies in one Python test file (advisory).

    ``is_test_path`` governs eligibility exactly as it does for the
    other test-scoped checks; production files return empty before the
    AST is even parsed. Groups are keyed by the normalized body dump,
    so order follows first definition - deterministic across runs.
    """
    if not rel.endswith(".py") or not is_test_path(rel):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    pools: dict[str, list[tuple[str, int]]] = {}
    for name, func in _test_functions(tree):
        dump = _normalized_body(func)
        if dump is None:
            continue
        pools.setdefault(dump, []).append((name, func.lineno))
    return [_group_finding(path, twins) for twins in pools.values() if len(twins) > 1]
