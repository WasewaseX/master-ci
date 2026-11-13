"""Reliability: the dedicated defect dimension and its rating.

The metric layer's fifth slice. Until now reliability lived blended
into code health - a swallowed exception priced like a bad comment and
disappeared into one blended ratio. The roadmap's split gives defects
their own dimension and an A-E rating the way SonarQube grades it,
over the reliability-relevant findings the scan already emits, plus
two new AST-backed defect checks filling the named gaps:

- unclosed-resource - an open() result assigned to a plain name no
    with-block anywhere touches and no .close() call anywhere ends.
    Those are the two legitimate exits; inline consumption
    (text = open(p).read()) is a call chain, not a held handle. The
    check passes no advisory: that is registry data, decided later.
- shared-mutable-state - a class attribute born a list, dict or set
    literal, or a zero-argument list/dict/set call: one object shared
    by every instance, so a mutation through one is visible through
    all. ClassVar is the one exclusion (the sharing is declared
    intentional); enums and dataclasses legitimately flag.

The rating prices the reliability-relevant findings with the debt
engine's registry minutes - no second taxonomy - over the same SQALE
denominator: DEV_MIN_PER_LINE per executable line of the densities'
measured corpus, handed over by the pipeline. Bands are SonarQube's:
A to 3% of dev cost, B to 5%, C to 10%, D to 20%, E past that.

Advisory, one finding per scan, off the A band only - a CLEAN scan
stays CLEAN. The finding anchors at the most expensive reliability
finding (ties alphabetically first); renders are bounded and sorted,
stable run to run.
"""

from __future__ import annotations

import ast
from pathlib import Path

from slop_debt import DEV_MIN_PER_LINE, remediation_minutes
from slop_density import MIN_CORPUS_LINES, DensityFacts
from slop_model import Finding

# The dimension's membership: the reliability-relevant findings the
# scan already emits, plus the two AST-backed checks below.
_RELIABILITY_IDS = """
bare-except empty-catch empty-rescue go-ignored-error java-broad-throws
mutable-default redundant-try-catch shared-mutable-state silent-recovery
swallowed-exception unclosed-resource unwrap-density
"""
RELIABILITY_CHECKS = frozenset(_RELIABILITY_IDS.split())

# SonarQube's reliability-rating bands: priced reliability minutes as
# a percent of the corpus's reconstruction cost. A is the healthy
# grade the scan stays silent under; past D's 20% line the rating is E.
RATING_BANDS = ((3.0, "A"), (5.0, "B"), (10.0, "C"), (20.0, "D"))
RATING_BUDGET = RATING_BANDS[0][0]  # the grade-A line, quoted in the message
RATING_SELF = "reliability-rating"  # the engine's own check, never priced

# Calibrated at adoption against this codebase's own self-scan: the
# error-handling family prices zero minutes over ~12.2k executable
# lines (grade A), unclosed-resource flags nothing, and the one
# shared-mutable-state flag is a read-only dispatch-table constant.
MAX_WORST_RENDER = 3  # worst checks named before the render cuts off


def _parse(text: str) -> ast.Module | None:
    """The parsed tree, or None for empty text or a file Python rejects."""
    if not text.strip():
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _open_assignments(tree: ast.AST) -> list[tuple[str, int]]:
    """Name and line of every plain assignment whose value is open(...)."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if call.func.id != "open":
            continue
        out.extend(
            (target.id, node.lineno) for target in node.targets if isinstance(target, ast.Name)
        )
    return out


def _exits(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names any with statement touches, and names a .close() call ends."""
    held: set[str] = set()
    closed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            held.update(name.id for name in ast.walk(node) if isinstance(name, ast.Name))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
        ):
            closed.add(node.func.value.id)
    return held, closed


def check_unclosed_resource(path: Path, text: str) -> list[Finding]:
    """open() results held outside any with-block and never closed."""
    tree = _parse(text)
    if tree is None:
        return []
    held, closed = _exits(tree)
    leaks = [
        (name, line)
        for name, line in _open_assignments(tree)
        if name not in held and name not in closed
    ]
    return [
        Finding(
            path=str(path),
            line=line,
            check="unclosed-resource",
            message=f"{name!r} holds an open() result with no with-block and no {name}.close()",
            weight=1,
        )
        for name, line in sorted(leaks, key=lambda pair: (pair[1], pair[0]))
    ]


def _is_mutable_build(value: ast.expr | None) -> bool:
    """A container literal, or a zero-argument list/dict/set call."""
    if isinstance(value, (ast.List, ast.Dict, ast.Set)):
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in ("list", "dict", "set")
        and not value.args
        and not value.keywords
    )


def _attr_names(stmt: ast.stmt) -> list[str]:
    """Simple-name targets of a class-level assignment; ClassVar skips."""
    if isinstance(stmt, ast.Assign):
        return [target.id for target in stmt.targets if isinstance(target, ast.Name)]
    if isinstance(stmt, ast.AnnAssign):
        if "ClassVar" in ast.unparse(stmt.annotation):
            return []
        return [stmt.target.id] if isinstance(stmt.target, ast.Name) else []
    return []


def _shared_attrs(node: ast.ClassDef) -> list[tuple[str, int]]:
    """Class-level attributes born mutable; ClassVar annotations skip."""
    out: list[tuple[str, int]] = []
    for stmt in node.body:
        names = _attr_names(stmt)
        value: ast.expr | None = None
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            value = stmt.value
        if names and _is_mutable_build(value):
            out.extend((name, stmt.lineno) for name in names)
    return out


def check_shared_mutable_state(path: Path, text: str) -> list[Finding]:
    """Class attributes born mutable: one object shared by every instance."""
    tree = _parse(text)
    if tree is None:
        return []
    found: list[tuple[ast.ClassDef, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            found.extend((node, name, line) for name, line in _shared_attrs(node))
    found.sort(key=lambda item: (item[2], item[1]))
    return [
        Finding(
            path=str(path),
            line=line,
            check="shared-mutable-state",
            message=(
                f"class {node.name!r} shares the mutable attribute {name!r}: a mutation "
                "through one instance is visible through all the others"
            ),
            weight=2,
            advisory=True,
        )
        for node, name, line in found
    ]


def _dimension_findings(findings: list[Finding]) -> list[Finding]:
    """The findings this dimension prices; the rating never prices itself."""
    return [f for f in findings if f.check in RELIABILITY_CHECKS and f.check != RATING_SELF]


def reliability_minutes(findings: list[Finding]) -> int:
    """Remediation minutes across the reliability-relevant findings."""
    return sum(remediation_minutes(finding) for finding in _dimension_findings(findings))


def reliability_grade(ratio: float) -> str:
    """SonarQube's A-E bands over remediation as a percent of dev cost."""
    for line, grade in RATING_BANDS:
        if ratio <= line:
            return grade
    return "E"


def _render_minutes(total: int) -> str:
    """45 -> '45m', 850 -> '14h 10m' - effort as a reader prices it."""
    if total < 60:
        return f"{total}m"
    return f"{total // 60}h {total % 60:02d}m"


def _worst_checks(rated: list[Finding]) -> list[tuple[str, str]]:
    """Priciest checks, minutes desc then name, each with its first path."""
    spent: dict[str, int] = {}
    where: dict[str, str] = {}
    for finding in sorted(rated, key=lambda f: f.path):
        spent[finding.check] = spent.get(finding.check, 0) + remediation_minutes(finding)
        where.setdefault(finding.check, finding.path)
    ranked = sorted(spent.items(), key=lambda item: (-item[1], item[0]))
    return [(check, where[check]) for check, _spent in ranked[:MAX_WORST_RENDER]]


def _rating_finding(
    facts: DensityFacts, rated: list[Finding], minutes: int, ratio: float, grade: str
) -> Finding:
    """Grade, totals and worst checks as evidence; anchored at the top price."""
    anchor = "."
    if rated:
        anchor = max(sorted(rated, key=lambda f: f.path), key=remediation_minutes).path
    worst = ", ".join(f"{check} at {path}" for check, path in _worst_checks(rated))
    message = (
        f"reliability rating {grade} ({ratio:.1f}% of dev cost, "
        f"budget {RATING_BUDGET:g}% for A): "
        f"{_render_minutes(minutes)} across {len(rated)} reliability findings "
        f"over {facts.exec_lines} executable lines; worst: {worst}"
    )
    return Finding(
        path=anchor,
        line=1,
        check=RATING_SELF,
        message=message,
        weight=2,
        advisory=True,
    )


def check_reliability_rating(facts: DensityFacts, findings: list[Finding]) -> list[Finding]:
    """The reliability grade: advisory, one finding, off the A band only.

    `facts` is the densities' measured corpus, shared with the debt
    ratio, so both divide by exactly the same denominator.
    """
    if facts.exec_lines < MIN_CORPUS_LINES:
        return []
    rated = _dimension_findings(findings)
    minutes = reliability_minutes(findings)
    ratio = 100 * minutes / (facts.exec_lines * DEV_MIN_PER_LINE)
    grade = reliability_grade(ratio)
    if grade == "A":
        return []
    return [_rating_finding(facts, rated, minutes, ratio, grade)]
