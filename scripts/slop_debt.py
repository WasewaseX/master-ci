"""Remediation debt: the scan's findings priced, as a debt ratio.

The metric layer's third slice. LCOM4 judged a class, the densities
judged the codebase's shape; debt prices what every other detector
found and asks the SQALE question: how big is the fix-it pile next to
the cost of writing the code in the first place?

- remediation effort - estimated minutes to fix every finding the scan
  emitted, priced by the class the engine's own registry already gave
  the check. No second taxonomy: the same membership that decides
  severity decides price. Design work (cycles, cohesion, the
  densities) is a refactor conversation and prices highest; structural
  budgets are a split-function edit; blocking defects are a focused
  pre-merge fix; the advisory judgment calls are mechanical edits.
- debt ratio - remediation minutes over reconstruction cost, the SQALE
  denominator: DEV_MIN_PER_LINE per executable line of the measured
  corpus. A ratio is comparable across repos of different sizes where
  a raw hour count is not.

The measured corpus is the densities' corpus - same lexer-backed
sources, no tests, no generated trees, same 250-line floor - handed
over by the pipeline rather than re-derived, so every ratio in the
report divides by exactly the same denominator.

The engine's own finding is excluded from the pricing: debt reports on
the rest of the scan, never on itself.

Advisory, maintainability, one finding per scan, emitted only when the
budget is exceeded - a CLEAN scan stays CLEAN. The finding anchors at
the most expensive single finding and the message carries the totals,
the dev cost and the worst check as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from slop_density import MIN_CORPUS_LINES, DensityFacts
from slop_model import STRUCTURAL_BUDGET_CHECKS, Finding, is_blocking

# Pricing, by registry class - the severity taxonomy is the cost model.
DESIGN_CHECKS = frozenset(
    {
        "import-cycle",
        "low-cohesion",
        "complexity-density",
        "duplication-density",
    }
)
DESIGN_MINUTES = 120  # a refactor conversation, planned - not a mechanical edit
STRUCTURAL_MINUTES = 30  # split a function or file along one axis
BLOCKING_MINUTES = 20  # a defect a reviewer fixes before merge
DEFAULT_MINUTES = 10  # an advisory judgment call: rename, delete, scope

# SQALE reconstruction cost: writing, testing and reviewing one
# executable line. Three minutes is the conservative mid - cheaper
# lines make every ratio look worse, never better.
DEV_MIN_PER_LINE = 3

# Calibrated at v2.14 against this codebase's own priced scan: 0.14%,
# fifty remediation minutes against about 35,600 minutes of
# reconstruction cost over ~11,900 executable lines - far under the
# budget. Advisory evidence, never a gate.
DEBT_RATIO_BUDGET = 5.0  # remediation minutes as percent of dev minutes

MAX_CHECKS_RENDER = 3


@dataclass(frozen=True)
class DebtFacts:
    """The scan priced: totals, the SQALE denominator, worst contributor."""

    findings: int
    minutes: int
    exec_lines: int
    dev_minutes: int
    worst: tuple[str, int, int] | None  # (check, minutes, finding count)
    worst_path: str | None

    @property
    def ratio(self) -> float:
        """Remediation cost as a percent of reconstruction cost."""
        if not self.dev_minutes:
            return 0.0
        return 100 * self.minutes / self.dev_minutes


def remediation_minutes(finding: Finding) -> int:
    """One finding's price, by the class the registry already gave it."""
    if finding.check in DESIGN_CHECKS:
        return DESIGN_MINUTES
    if finding.check in STRUCTURAL_BUDGET_CHECKS:
        return STRUCTURAL_MINUTES
    if is_blocking(finding.check):
        return BLOCKING_MINUTES
    return DEFAULT_MINUTES


def _render_minutes(total: int) -> str:
    """45 -> '45m', 850 -> '14h 10m' - effort as a reader prices it."""
    if total < 60:
        return f"{total}m"
    return f"{total // 60}h {total % 60:02d}m"


def debt_report(findings: list[Finding], exec_lines: int) -> DebtFacts:
    """Price every finding against the measured corpus.

    Deterministic: ties on cost resolve alphabetically, so the report
    is stable run to run.
    """
    priced = [
        (finding, remediation_minutes(finding))
        for finding in findings
        if finding.check != "debt-ratio"
    ]
    minutes = sum(cost for _, cost in priced)
    by_check: dict[str, tuple[int, int]] = {}
    for finding, cost in priced:
        spent, count = by_check.get(finding.check, (0, 0))
        by_check[finding.check] = (spent + cost, count + 1)
    worst: tuple[str, int, int] | None = None
    if by_check:
        name, (spent, count) = max(sorted(by_check.items()), key=lambda kv: kv[1][0])
        worst = (name, spent, count)
    worst_path: str | None = None
    if priced:
        ordered = sorted(priced, key=lambda fc: (fc[0].path, fc[0].line))
        top = max(ordered, key=lambda fc: fc[1])
        worst_path = top[0].path
    return DebtFacts(
        findings=len(priced),
        minutes=minutes,
        exec_lines=exec_lines,
        dev_minutes=exec_lines * DEV_MIN_PER_LINE,
        worst=worst,
        worst_path=worst_path,
    )


def _debt_finding(report: DebtFacts) -> Finding:
    """One finding: the ratio, the totals and the worst check as evidence."""
    assert report.worst is not None  # emitted only when priced findings exist
    check, spent, count = report.worst
    message = (
        f"debt ratio {report.ratio:.1f}% (budget {DEBT_RATIO_BUDGET}%): "
        f"{_render_minutes(report.minutes)} remediation across {report.findings} findings "
        f"over {report.exec_lines} executable lines "
        f"(dev cost {_render_minutes(report.dev_minutes)}); "
        f"worst check: {check} {_render_minutes(spent)} ({count} findings)"
    )
    return Finding(
        path=report.worst_path or ".",
        line=1,
        check="debt-ratio",
        message=message,
        weight=2,
        advisory=True,
    )


def check_debt_metrics(facts: DensityFacts, findings: list[Finding]) -> list[Finding]:
    """The scan priced as a debt ratio (advisory, over budget only).

    `facts` is the densities' measured corpus, computed once by the
    pipeline and shared here - the ratio divides by exactly what the
    density ratios divided by.
    """
    if facts.exec_lines < MIN_CORPUS_LINES:
        return []
    report = debt_report(findings, facts.exec_lines)
    if report.ratio <= DEBT_RATIO_BUDGET:
        return []
    return [_debt_finding(report)]
