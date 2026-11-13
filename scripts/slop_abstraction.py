"""Abstraction overhead: forwarding chains and no-behavior classes.

The roadmap's P1 metric layer's abstraction slice. The thin-wrapper rule
judges one function and sees one function; this engine judges the pattern
around it: a delegation ladder where every hop only passes the call
through, and a class whose every method forwards to the same injected
object and adds nothing of its own. The whole-relationship-graph judgment
is the semantic-bloat phase's business; this slice upgrades the
single-wrapper rule to chains (within one module) and whole classes, and
stays conservative where the truth is uncertain.

Python family only, ast-backed, one file per call; chains are intra-module
only - cross-module resolution is the semantic phase's graph work. A rung
joins a ladder only when it forwards to the same target base (same
attribute chain, same called name); differing bases stay silent. Module
functions chain by bare target name, methods by their own name on one
attribute chain, async forwards included. Both checks are advisory, one
finding per check per file, silent on a clean scan, bounded and stable.

Exclusions carry their false-positive reason: dunder and decorated
functions change the contract a forward must keep; Protocol/ABC/Interface
bases and ABCMeta metaclasses declare rather than behave; @dataclass and
@total_ordering generate dunders; exception and enum bases bring their own.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from slop_model import Finding

# forwarding-chain budget - four same-target rungs with no transformation
# anywhere is a ladder no reader can debug; three is ordinary layering.
# Calibrated on this codebase's own sources: both checks stay silent there.
FORWARD_CHAIN_DEPTH = 4

# no-behavior-class budget - two pure forwards around one injected object
# already make the class a shell; one forward alone is the thin-wrapper rule.
NO_BEHAVIOR_MIN_METHODS = 2

MAX_CHAIN_RENDER = 6  # hops named before the "+N more" tail
MAX_CLASSES_RENDER = 4  # classes named before the "+N more" tail

_DECORATOR_EXEMPT = frozenset({"dataclass", "total_ordering"})
_DECLARATION_TOKENS = ("Protocol", "ABC", "Interface")


@dataclass(frozen=True)
class ForwardFact:
    """One pass-through forward: qualified name, def line and target base."""

    qualname: str
    line: int
    root: str  # dotted attribute chain ("self.db"); "" for a bare name
    callee: str  # the called function or method name


def _passthrough(call: ast.Call, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when every argument is one of the function's own parameters."""
    head = node.args
    params = {arg.arg for arg in head.posonlyargs + head.args + head.kwonlyargs}
    params.update(arg.arg for arg in (head.vararg, head.kwarg) if arg is not None)
    values = [arg.value if isinstance(arg, ast.Starred) else arg for arg in call.args]
    values += [keyword.value for keyword in call.keywords]
    return all(isinstance(value, ast.Name) and value.id in params for value in values)


def _call_base(call: ast.Call) -> tuple[str, str] | None:
    """(root attribute chain, callee) for Name and dotted targets, else None."""
    if isinstance(call.func, ast.Name):
        return "", call.func.id
    if not isinstance(call.func, ast.Attribute):
        return None
    parts: list[str] = []
    node: ast.expr = call.func.value
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None  # a chained or subscripted target is not a pass-through
    return ".".join([node.id, *reversed(parts)]), call.func.attr


def _forward_fact(
    qualname: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> ForwardFact | None:
    """The function as a pure pass-through forward, or None when it is not."""
    if node.name.startswith("__") or node.decorator_list:
        return None  # dunder hooks and decorated members change the contract
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
        return None
    value = node.body[0].value
    if isinstance(value, ast.Await):  # async forward: return await call(...)
        value = value.value
    if not isinstance(value, ast.Call):
        return None
    base = _call_base(value) if _passthrough(value, node) else None
    if base is None:
        return None
    return ForwardFact(qualname, node.lineno, *base)


def _is_docstring(stmt: ast.stmt) -> bool:
    """A bare string expression: the class body's documentation."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _wired_target(target: ast.expr, allowed: frozenset[str]) -> bool:
    """One assignment target: self.<attr> with the attribute allowed."""
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr in allowed
    )


def _init_body_ok(body: list[ast.stmt], allowed: frozenset[str]) -> bool:
    """Every __init__ statement is pass, the docstring, or the one wiring."""
    for stmt in body:
        if isinstance(stmt, ast.Pass) or _is_docstring(stmt):
            continue
        if not isinstance(stmt, ast.Assign):
            return False
        if not all(_wired_target(target, allowed) for target in stmt.targets):
            return False
    return True


def _plain_name(expr: ast.expr) -> str:
    """The plain name behind a Name or Attribute reference, else empty."""
    return expr.id if isinstance(expr, ast.Name) else getattr(expr, "attr", "")


def _declared_base(name: str) -> bool:
    """Bases that declare rather than behave, or carry their own semantics."""
    if any(token in name for token in _DECLARATION_TOKENS) or "Enum" in name:
        return True  # protocol shapes declare, enum members are data
    return name == "BaseException" or name.endswith(("Error", "Exception"))


def _class_excluded(node: ast.ClassDef) -> bool:
    """The declaration-shaped classes the verdict must stay silent about."""
    for dec in node.decorator_list:
        if _plain_name(dec) in _DECORATOR_EXEMPT:
            return True  # generated dunders and comparisons are real behavior
    bases = [*node.bases, *(kw.value for kw in node.keywords if kw.arg == "metaclass")]
    return any(_declared_base(_plain_name(base)) for base in bases)


def _class_scan(
    node: ast.ClassDef, forwards: list[ForwardFact], shells: list[tuple[str, int]]
) -> None:
    """The class's method forwards join the map; a shell verdict joins hits."""
    if _class_excluded(node):
        return
    methods: list[str] = []
    roots: set[str] = set()
    found = 0
    init: list[ast.stmt] | None = None
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name == "__init__":
            init = item.body
            continue
        if item.name.startswith("__"):
            continue  # dunder protocol members are framework-invoked
        methods.append(item.name)
        fact = _forward_fact(f"{node.name}.{item.name}", item)
        if fact is None:
            continue
        forwards.append(fact)
        roots.add(fact.root)
        found += 1
    if found != len(methods) or len(methods) < NO_BEHAVIOR_MIN_METHODS or len(roots) != 1:
        return  # a real method body, a thin class or mixed roots keep it honest
    shared = next(iter(roots))
    wired = init is None or _init_body_ok(init, frozenset(shared.split(".")[1:2]))
    if not shared.startswith("self.") or not wired:
        return
    shells.append((node.name, node.lineno))


def _module_facts(text: str) -> tuple[list[ForwardFact], list[tuple[str, int]]] | None:
    """Module forwards (methods included) and shell hits; None on parse failure."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    forwards: list[ForwardFact] = []
    shells: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fact = _forward_fact(node.name, node)
            if fact is not None:
                forwards.append(fact)
        elif isinstance(node, ast.ClassDef):
            _class_scan(node, forwards, shells)
    shells.sort(key=lambda hit: (hit[1], hit[0]))
    return forwards, shells


def _longest_ladder(forwards: list[ForwardFact]) -> list[ForwardFact] | None:
    """The longest same-base ladder, ties to the alphabetically first start."""
    groups: dict[tuple[str, str, str], list[ForwardFact]] = {}
    for fact in forwards:
        local = fact.qualname.rsplit(".", 1)[-1]
        if "." in fact.qualname and (fact.root == "" or fact.callee != local):
            continue  # only same-name delegation through a chain is a rung
        if "." not in fact.qualname and fact.root != "":
            continue  # module chains delegate by bare target name only
        kind = "method" if "." in fact.qualname else "module"
        groups.setdefault((kind, fact.root, fact.callee), []).append(fact)
    ladders = [
        sorted(group, key=lambda item: item.qualname)
        for group in groups.values()
        if len(group) >= FORWARD_CHAIN_DEPTH
    ]
    if not ladders:
        return None
    return min(ladders, key=lambda ladder: (-len(ladder), ladder[0].qualname))


def _advisory(path: Path, line: int, check: str, message: str) -> Finding:
    """The advisory weight-2 finding shape both abstraction checks emit."""
    return Finding(path=str(path), line=line, check=check, message=message, weight=2, advisory=True)


def _chain_finding(path: Path, ladder: list[ForwardFact]) -> Finding:
    """One finding: the longest ladder, every hop and the hop count."""
    hops = [fact.qualname for fact in ladder[:MAX_CHAIN_RENDER]]
    if len(ladder) > MAX_CHAIN_RENDER:
        hops.append(f"+{len(ladder) - MAX_CHAIN_RENDER} more")
    message = f"{' -> '.join(hops)} ({len(ladder)} hops to the same call, none transforms it)"
    return _advisory(path, ladder[0].line, "forwarding-chain", message)


def _class_finding(path: Path, shells: list[tuple[str, int]]) -> Finding:
    """One finding listing the shell classes, bounded, first as anchor."""
    names = [name for name, _line in shells[:MAX_CLASSES_RENDER]]
    if len(shells) > MAX_CLASSES_RENDER:
        names.append(f"+{len(shells) - MAX_CLASSES_RENDER} more")
    message = (
        f"{len(shells)} no-behavior class(es) whose every method forwards "
        f"to one injected object: {', '.join(names)}"
    )
    return _advisory(path, shells[0][1], "no-behavior-class", message)


def check_abstraction(path: Path, text: str, family: str) -> list[Finding]:
    """Both abstraction verdicts for one Python file (advisory, bounded)."""
    if family != "py" or not text.strip():
        return []
    facts = _module_facts(text)
    if facts is None:
        return []
    forwards, shells = facts
    findings: list[Finding] = []
    ladder = _longest_ladder(forwards)
    if ladder is not None:
        findings.append(_chain_finding(path, ladder))
    if shells:
        findings.append(_class_finding(path, shells))
    return findings
