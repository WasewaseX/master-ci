"""Round-2 scanaislop adoption: dead residue and pythonic-shape debts.

Patterns catalogued by scanaislop.com's 58-pattern research, ported to
the detector's registry model (Python AST family):

- dead residue: unreachable code, constant conditions, duplicate imports
    (scanaislop ``ai-slop/unreachable-code``, ``constant-condition``,
    ``duplicate-import``)
- pythonic-shape debts: ``for i in range(len(x))`` loops, isinstance
    ladders and longhand string dispatch (four-plus branches on one
    subject), catch blocks that only rethrow, catch blocks that log
    without the caught error and carry on
- test integrity: assertions that cannot fail (``assert True``, ``x == x``)
- naming: module-level identifiers and functions named data/result/
    value/temp/obj/info/item/... - nouns that say "this is a noun"

Commentary-side checks (meta comments, prose blocks, hardcoded URLs,
brace-family residue) live in slop_commentary; this module calls them
through ``check_commentary`` so the workflow keeps a single entry.

All findings are advisory: they are judgment calls reported and
annotated, never failing a build on their own (the deficit score and
the workflow's slop gate turn aggregate pressure into enforcement).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from slop_commentary import check_commentary
from slop_files import is_test_path
from slop_model import Finding

LADDER_MIN = 4  # branches before a hand-rolled dispatch table is flagged

GENERIC_NAMES = frozenset(
    {
        "data",
        "result",
        "value",
        "temp",
        "obj",
        "info",
        "item",
        "handle",
        "process",
        "manager",
        "util",
        "utils",
        "helper",
        "misc",
        "stuff",
    }
)


def _call_name(func: ast.expr) -> str:
    """Best-effort dotted callable name for a Call's func expression."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _walk_body(body: list[ast.stmt]) -> ast.AST:
    """A synthetic module so ast.walk works over a bare statement list."""
    return ast.Module(body=body, type_ignores=[])


def _is_exit(stmt: ast.stmt) -> bool:
    return isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue))


def check_unreachable(path: Path, tree: ast.AST) -> Finding | None:
    """Code after a return/raise/break/continue in the same block."""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for prev, cur in zip(body, body[1:]):
            if _is_exit(prev):
                return Finding(
                    path=str(path),
                    line=cur.lineno,
                    check="unreachable-code",
                    message="statement after return/raise/break/continue never runs",
                    weight=1,
                    advisory=True,
                )
    return None


def check_constant_condition(path: Path, tree: ast.AST) -> list[Finding]:
    """`if True:`-style branches whose outcome is fixed in the source.

    `while True:` loops are idiomatic and are not flagged; `while 1:`
    and `while False:` are.
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            findings.append(
                Finding(
                    path=str(path),
                    line=node.lineno,
                    check="constant-condition",
                    message=f"branch condition is the constant {node.test.value!r}",
                    weight=1,
                    advisory=True,
                )
            )
        elif isinstance(node, ast.While) and isinstance(node.test, ast.Constant):
            if node.test.value is not True:
                findings.append(
                    Finding(
                        path=str(path),
                        line=node.lineno,
                        check="constant-condition",
                        message=f"loop condition is the constant {node.test.value!r}",
                        weight=1,
                        advisory=True,
                    )
                )
    return findings


def check_range_len_loop(path: Path, tree: ast.AST) -> list[Finding]:
    """C-style index loops where Python offers iteration or enumerate."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        iter_call = node.iter
        if (
            isinstance(iter_call, ast.Call)
            and isinstance(iter_call.func, ast.Name)
            and iter_call.func.id == "range"
            and iter_call.args
            and isinstance(iter_call.args[0], ast.Call)
            and isinstance(iter_call.args[0].func, ast.Name)
            and iter_call.args[0].func.id == "len"
        ):
            findings.append(
                Finding(
                    path=str(path),
                    line=node.lineno,
                    check="range-len-loop",
                    message="for i in range(len(...)) - iterate directly or enumerate",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def _isinstance_subject(test: ast.expr) -> str | None:
    """Subject key when the test is isinstance(name, ...), else None."""
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and test.args
        and isinstance(test.args[0], ast.Name)
    ):
        return f"isinstance:{test.args[0].id}"
    return None


def _eq_string_subject(test: ast.expr) -> str | None:
    """Subject key when the test is name == 'literal', else None."""
    if (
        isinstance(test, ast.Compare)
        and test.ops
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Name)
        and test.comparators
        and isinstance(test.comparators[0], ast.Constant)
        and isinstance(test.comparators[0].value, str)
    ):
        return f"eq:{test.left.id}"
    return None


def _chain_subjects(root: ast.If) -> dict[str, int]:
    """Count branch tests per subject across one if/elif chain."""
    subjects: dict[str, int] = {}
    node: ast.If | None = root
    while node is not None:
        subject = _isinstance_subject(node.test) or _eq_string_subject(node.test)
        if subject:
            subjects[subject] = subjects.get(subject, 0) + 1
        nxt = (
            node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
        )
        node = nxt
    return subjects


def check_dispatch_ladders(path: Path, tree: ast.AST) -> list[Finding]:
    """Four-plus isinstance or ==-string branches testing the same subject."""
    chain_members: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and len(node.orelse) == 1
            and isinstance(node.orelse[0], ast.If)
        ):
            chain_members.add(id(node.orelse[0]))
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in chain_members:
            continue
        for subject, count in _chain_subjects(node).items():
            if count < LADDER_MIN:
                continue
            findings.append(
                Finding(
                    path=str(path),
                    line=node.lineno,
                    check=(
                        "isinstance-ladder"
                        if subject.startswith("isinstance:")
                        else "repetitive-dispatch"
                    ),
                    message=f"{count} branches on one subject - use a dispatch table",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def _imported_modules(node: ast.stmt) -> list[str]:
    """Full dotted modules one import statement pulls in.

    The whole path is the identity, not the first component:
    ``collections`` and ``collections.abc`` are different modules and
    importing both is not a duplicate.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _record_import(counts: dict, first_line: dict, module: str, lineno: int) -> None:
    if module in counts:
        counts[module] += 1
    else:
        counts[module] = 1
        first_line[module] = lineno


def check_duplicate_imports(path: Path, tree: ast.AST, rel: str) -> list[Finding]:
    """Two top-level imports of one module in a file (re-export shims exempt).

    Function-local imports are a deliberate scoping pattern and are not
    counted - the scanaislop duplicate-import rule targets file scope.
    """
    if Path(rel).name == "__init__.py":
        return []
    counts: dict[str, int] = {}
    first_line: dict[str, int] = {}
    for node in getattr(tree, "body", []):
        for module in _imported_modules(node):
            _record_import(counts, first_line, module, node.lineno)
    return [
        Finding(
            path=str(path),
            line=first_line[module],
            check="duplicate-import",
            message=f"module {module!r} imported {count} times at file scope",
            weight=1,
            advisory=True,
        )
        for module, count in sorted(counts.items())
        if count > 1
    ]


_LOGISH_CALL = re.compile(r"(?i)\b(?:log(?:ger)?|logging|warn(?:ing)?|print|echo)\b")


def _handler_debt(path: Path, node: ast.ExceptHandler) -> Finding | None:
    """The debt one handler carries, if any."""
    scope = _walk_body(node.body)
    if len(node.body) == 1 and isinstance(node.body[0], ast.Raise) and node.body[0].exc is None:
        return Finding(
            path=str(path),
            line=node.lineno,
            check="redundant-try-catch",
            message="handler only rethrows - the try block adds nothing",
            weight=1,
            advisory=True,
        )
    bound = node.name
    if not bound:
        return None
    used = any(isinstance(n, ast.Name) and n.id == bound for n in ast.walk(scope))
    propagates = any(isinstance(n, (ast.Raise, ast.Return)) for n in ast.walk(scope))
    logs = any(
        isinstance(n, ast.Call) and _LOGISH_CALL.search(_call_name(n.func)) for n in ast.walk(scope)
    )
    if not used and not propagates and logs:
        return Finding(
            path=str(path),
            line=node.lineno,
            check="silent-recovery",
            message="handler logs without the caught error and carries on",
            weight=1,
            advisory=True,
        )
    return None


def check_handler_debts(path: Path, tree: ast.AST) -> list[Finding]:
    """Catch blocks that only rethrow, or log without the error and go on."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            debt = _handler_debt(path, node)
            if debt is not None:
                findings.append(debt)
    return findings


def check_tautological_tests(path: Path, tree: ast.AST, rel: str) -> list[Finding]:
    """Assertions that compare fixed literals and cannot fail."""
    if not is_test_path(rel):
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        tautology = isinstance(node.test, ast.Constant) and bool(node.test.value)
        if not tautology and isinstance(node.test, ast.Compare):
            ops = node.test.ops
            left = node.test.left
            comps = node.test.comparators
            if (
                len(ops) == 1
                and isinstance(ops[0], (ast.Eq, ast.Is))
                and isinstance(left, ast.Name)
                and len(comps) == 1
                and isinstance(comps[0], ast.Name)
                and left.id == comps[0].id
            ):
                tautology = True
        if tautology:
            findings.append(
                Finding(
                    path=str(path),
                    line=node.lineno,
                    check="tautological-test",
                    message="assertion compares fixed values and cannot fail",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def _generic_targets(node: ast.stmt) -> list[tuple[str, int]]:
    """Generic module-level names one statement defines."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [(f"{node.name}()", node.lineno)] if node.name in GENERIC_NAMES else []
    if isinstance(node, ast.Assign):
        return [
            (target.id, node.lineno)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id in GENERIC_NAMES
        ]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.lineno)] if node.target.id in GENERIC_NAMES else []
    return []


def check_generic_names(path: Path, tree: ast.AST, rel: str) -> list[Finding]:
    """Module-level nouns that name no noun in particular (data, result...)."""
    if is_test_path(rel):
        return []
    offenders = [entry for node in getattr(tree, "body", []) for entry in _generic_targets(node)]
    if not offenders:
        return []
    names = ", ".join(sorted({name for name, _ in offenders}))
    return [
        Finding(
            path=str(path),
            line=offenders[0][1],
            check="generic-name",
            message=f"generic top-level names say nothing: {names}",
            weight=1,
            advisory=True,
        )
    ]


def check_residue(
    path: Path,
    text: str,
    family: str,
    rel: str,
    comments: list,
) -> list[Finding]:
    """Round-2 checks for one file: AST residue + commentary checks."""
    findings: list[Finding] = []
    tree: ast.AST | None = None
    if family == "py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
    if tree is not None:
        unreachable = check_unreachable(path, tree)
        if unreachable is not None:
            findings.append(unreachable)
        findings.extend(check_range_len_loop(path, tree))
        findings.extend(check_dispatch_ladders(path, tree))
        findings.extend(check_constant_condition(path, tree))
        findings.extend(check_duplicate_imports(path, tree, rel))
        findings.extend(check_handler_debts(path, tree))
        findings.extend(check_tautological_tests(path, tree, rel))
        findings.extend(check_generic_names(path, tree, rel))
    findings.extend(check_commentary(path, text, family, rel, comments))
    return findings
