"""The v2.15 metric ceilings on the merge-base diff: the P2 gates row.

The new-code gate grew its fourth, fifth and sixth ceilings and the
gate module hit the same 400-line budget it enforces on consumers -
the same split the CLI made from the detector and REMEDIES made from
the registry. This module owns the three metric ceilings and their
sections; new_code_gate.py owns the diff, the parser promise and the
scan.

- duplication density on added lines - duplicated long lines (the
    densities' numerator: stripped, non-comment, 40+ characters, seen
    twice or more) as a percent of the non-blank added lines. The
    project metric divides by lexer-counted lines; the diff gate counts
    non-blank lines, text-only, so the gate never needs a lexer the
    runner may not have for an arbitrary file.
- debt ratio on added lines - findings on added lines priced by the
    debt engine's registry minutes over the SQALE cost of the added
    lines (DEV_MIN_PER_LINE per added line). The ratio computes only
    over what the change brought in: legacy debt never inflates it.
- design findings on added lines - the LCOM4 cohesion verdict and the
    coupling pair (low-cohesion, high-coupling, unstable-hub) anchored
    on lines the change adds. Zero tolerance by default: a design
    verdict the change itself introduces fails the gate.

One scan feeds all three finding-based ceilings upstream; these
functions receive the pre-filtered new-line findings. Every ceiling
defaults off (percentages at 100, the count at -1) and renders one
markdown section, breached or not, so the run summary always shows
what was measured.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from slop_debt import DEV_MIN_PER_LINE, remediation_minutes
from slop_files import read_text_lax
from slop_model import Finding
from slop_patterns import DUP_LINE_MIN_LEN
from slop_reporting import MAX_ANNOTATIONS

OFF_PERCENT = 100.0  # a percent ceiling at or above 100 is off
OFF_COUNT = -1  # a count ceiling below 0 is off

# The design-budget family the diff gate holds: LCOM4's cohesion
# verdict and the coupling pair, anchored on lines the change adds.
DESIGN_GATE_CHECKS = frozenset({"low-cohesion", "high-coupling", "unstable-hub"})


@dataclass(frozen=True)
class MetricCeilings:
    """The v2.15 metric ceilings on the diff; every default is off."""

    new_duplication: float = OFF_PERCENT
    new_debt: float = OFF_PERCENT
    design_findings: int = OFF_COUNT

    @property
    def requested(self) -> bool:
        """True when any metric ceiling is switched on."""
        return (
            self.new_duplication < OFF_PERCENT
            or self.new_debt < OFF_PERCENT
            or self.design_findings >= 0
        )

    @property
    def wants_findings(self) -> bool:
        """True when a finding-based ceiling needs the scan."""
        return self.new_debt < OFF_PERCENT or self.design_findings >= 0


def _minutes(total: int) -> str:
    """45 -> '45m', 850 -> '14h 10m' - effort as a reader prices it."""
    if total < 60:
        return f"{total}m"
    return f"{total // 60}h {total % 60:02d}m"


def added_line_texts(root: Path, changed: dict[str, set[int]]) -> list[str]:
    """The added lines as they stand now, file by file, in line order."""
    out: list[str] = []
    for rel, numbers in sorted(changed.items()):
        path = root / rel
        if not path.is_file():
            continue
        file_lines = read_text_lax(path).splitlines()
        out.extend(
            file_lines[number - 1] for number in sorted(numbers) if 1 <= number <= len(file_lines)
        )
    return out


def duplication_pct(added: list[str]) -> tuple[float, int, int]:
    """Duplicated long lines as a percent of the non-blank added lines."""
    counter: Counter[str] = Counter()
    eligible = 0
    for raw in added:
        line = raw.strip()
        if not line or line.startswith(("#", "//", "*", "<!--")):
            continue
        eligible += 1
        if len(line) >= DUP_LINE_MIN_LEN:
            counter[line] += 1
    duplicated = sum(count for count in counter.values() if count >= 2)
    if not eligible:
        return 0.0, 0, 0
    return 100.0 * duplicated / eligible, duplicated, eligible


def duplication_ceiling(
    root: Path, changed: dict[str, set[int]], ceiling: float
) -> tuple[int, list[str]]:
    """The diff window's duplication density against its ceiling."""
    if ceiling >= OFF_PERCENT:
        return 0, []
    pct, duplicated, eligible = duplication_pct(added_line_texts(root, changed))
    exit_code = 1 if pct > ceiling else 0
    section = (
        "### New-code gate: duplication density on added lines\n\n"
        f"{duplicated} duplicated line(s) of {eligible} eligible added lines - "
        f"density **{pct:.1f}%** against the {ceiling:g}% ceiling.\n\n"
    )
    return exit_code, [section]


def debt_ceiling(
    new_line_findings: list[Finding], changed: dict[str, set[int]], ceiling: float
) -> tuple[int, list[str]]:
    """The change's priced findings over the SQALE cost of its lines."""
    if ceiling >= OFF_PERCENT:
        return 0, []
    added = sum(len(numbers) for numbers in changed.values())
    priced = sum(
        remediation_minutes(finding)
        for finding in new_line_findings
        if finding.check != "debt-ratio"
    )
    dev_minutes = added * DEV_MIN_PER_LINE
    ratio = 100.0 * priced / dev_minutes if dev_minutes else 0.0
    exit_code = 1 if ratio > ceiling else 0
    section = (
        "### New-code gate: debt ratio on added lines\n\n"
        f"{_minutes(priced)} remediation over {added} added line(s) "
        f"(dev cost {_minutes(dev_minutes)}) - ratio **{ratio:.1f}%** "
        f"against the {ceiling:g}% ceiling.\n\n"
    )
    return exit_code, [section]


def design_ceiling(new_line_findings: list[Finding], ceiling: int) -> tuple[int, list[str]]:
    """The design verdicts the change introduced against its ceiling."""
    if ceiling < 0:
        return 0, []
    design = [finding for finding in new_line_findings if finding.check in DESIGN_GATE_CHECKS]
    exit_code = 1 if len(design) > ceiling else 0
    lines = [
        "### New-code gate: design findings on added lines",
        "",
        f"{len(design)} design finding(s) on added lines against the "
        f"{ceiling:g}-finding ceiling (low-cohesion, high-coupling, unstable-hub).",
        "",
    ]
    if design:
        lines += ["| Check | Location | Message |", "|---|---|---|"]
        for item in design[:MAX_ANNOTATIONS]:
            lines.append(f"| `{item.check}` | `{item.path}:{item.line}` | {item.message} |")
        lines.append("")
    return exit_code, ["\n".join(lines)]
