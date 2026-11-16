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

Metric definitions (v2.18, audit finding C - precise, not borrowed):

- reliability rating - severity-based, over the reliability-relevant
    findings the scan emits. Master CI's severity taxonomy is
    two-grade (the registry knows blocking and advisory), so the
    weights are those grades' standing: a blocking defect weighs 100,
    an advisory judgment call 2. The A-E bands are Master CI's own;
    the exact counts each edge encodes are documented at RATING_BANDS.
    SonarQube grades reliability by issue severity as well, with its
    own weights and rule-level severities; this is the same shape of
    metric, not a re-implementation of their numbers and not a claim
    of equivalence.
- remediation share (message evidence, informational) - priced
    reliability minutes over the SQALE-style dev-cost denominator. The
    per-class minutes in slop_debt are engineering estimates for
    prioritization, NOT measured repair times; the share contextualizes
    the grade and is not its input.
- the remediation-cost ratio as a maintainability grade lives in
    slop_debt (debt-ratio), where SonarQube's SQALE math belongs; this
    module no longer divides minutes by dev cost to grade reliability.

Advisory, one finding per scan, off the A band only - a CLEAN scan
stays CLEAN. The finding anchors at the most expensive reliability
finding (ties alphabetically first); renders are bounded and sorted,
stable run to run.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
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

# The severity weights are Master CI's own two-grade taxonomy (the
# registry knows blocking and advisory): a blocking defect weighs 100,
# an advisory judgment call 2. These are not SonarQube's numbers -
# Sonar weights five severities with its own published values.
BLOCKING_WEIGHT = 100
ADVISORY_WEIGHT = 2

# Severity bands over the weighted score. Each edge encodes an exact
# count, so the grade reads back into evidence a reviewer can check:
#   A  0      - clean, the scan stays silent
#   B  <=10   - up to 5 advisory judgment calls, no blocking defect
#   C  <=210  - up to 2 blocking defects (possibly alongside advisories)
#   D  <=420  - up to 4 blocking defects
#   E  beyond - 5 or more blocking defects
RATING_BANDS = ((0, "A"), (10, "B"), (210, "C"), (420, "D"))
RATING_SELF = "reliability-rating"  # the engine's own check, never priced

# Calibrated against this codebase's own self-scan (verified v2.18):
# the template holds zero reliability-dimension findings over ~12.2k
# executable lines, so the self-scan grades A and stays silent. Under
# the severity bands one advisory judgment call would grade B - the
# rating says what the dimension actually holds, not what its minutes
# cost; the finding stays advisory, a CLEAN exit stays 0.
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


def weighted_score(blocking: int, advisory: int) -> int:
    """The severity score the grade reads: counts x their grade's weight."""
    return blocking * BLOCKING_WEIGHT + advisory * ADVISORY_WEIGHT


def reliability_grade(blocking: int, advisory: int) -> str:
    """A-E over the weighted severity score; bands at RATING_BANDS."""
    score = weighted_score(blocking, advisory)
    for edge, grade in RATING_BANDS:
        if score <= edge:
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


@dataclass(frozen=True)
class RatingVerdict:
    """The grade and everything the finding message renders as evidence."""

    grade: str
    blocking: int
    advisory: int
    minutes: int
    share: float

    @property
    def score(self) -> int:
        return weighted_score(self.blocking, self.advisory)


def _rating_finding(facts: DensityFacts, rated: list[Finding], verdict: RatingVerdict) -> Finding:
    """Grade, counts, weighted score and worst checks as evidence."""
    anchor = "."
    if rated:
        anchor = max(sorted(rated, key=lambda f: f.path), key=remediation_minutes).path
    worst = ", ".join(f"{check} at {path}" for check, path in _worst_checks(rated))
    message = (
        f"reliability rating {verdict.grade} ({verdict.blocking} blocking + "
        f"{verdict.advisory} advisory, weighted {verdict.score}; remediation share "
        f"{verdict.share:.1f}% of dev cost): "
        f"{_render_minutes(verdict.minutes)} across {len(rated)} reliability findings "
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
    """The reliability grade: severity-based, advisory, one finding, off A only.

    `facts` is the densities' measured corpus; it bounds the rating to
    projects with a real corpus and prices the informational remediation
    share. The grade itself reads only the dimension's severity counts,
    per the v2.18 definitions in the module docstring.
    """
    if facts.exec_lines < MIN_CORPUS_LINES:
        return []
    rated = _dimension_findings(findings)
    blocking = sum(1 for finding in rated if not finding.advisory)
    advisory = len(rated) - blocking
    grade = reliability_grade(blocking, advisory)
    if grade == "A":
        return []
    minutes = reliability_minutes(findings)
    share = 100 * minutes / (facts.exec_lines * DEV_MIN_PER_LINE)
    verdict = RatingVerdict(grade, blocking, advisory, minutes, share)
    return [_rating_finding(facts, rated, verdict)]
