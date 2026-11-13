#!/usr/bin/env python3
"""Coverage gates: measured test assurance, fail-closed.

Roadmap item 4. Test assurance reported inventory before (empty tests,
missing-tests); this module adds proof - the stack test steps run with
coverage, the reports land as artifacts, and the gate consumes them, so
the test-assurance dimension can show measured coverage instead of a
guess.

The report dialects (coverage.json, Cobertura XML, JaCoCo XML, Go
coverprofile) live in ``coverage_parsers.py``; this module is the gate:
the overall floor across every report, the new-code floor on the lines
the merge-base diff adds, and the fail-closed contract.

Contract:
- exit 0  every requested gate holds (or no gate requested)
- exit 1  measured coverage is below a requested floor
- exit 2  a requested gate cannot be evaluated: no report found, a
    report unparseable, no diff base for the new-code gate, or git
    failed. Fail-closed - a gate that cannot be evaluated never
    silently passes, same semantics as the architecture config.

The overall floor gates every report: in a polyglot repo each stack's
report covers its own slice, and every slice must meet the floor. The
new-code gate intersects the diff's added lines with each report's
line data, so "coverage" means the lines this change actually added.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from coverage_parsers import (  # noqa: F401 - re-exported, one import door
    STANDARD_REPORTS,
    CoverageGateError,
    CoverageReport,
    FileCoverage,
    discover_reports,
    parse_report,
)
from coverage_subjects import is_coverage_subject


def _normalize(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


@dataclass
class NewCodeView:
    total_percent: float
    changed_total: int
    covered_total: int
    per_file: dict[str, tuple[int, int, int]]  # path -> (covered, changed, first_line)


def new_code_view(
    reports: list[CoverageReport],
    changed: dict[str, set[int]],
) -> NewCodeView:
    """Coverage restricted to the added lines, across all reports."""
    per_file: dict[str, tuple[int, int, int]] = {}
    covered_total = 0
    changed_total = 0
    for path, lines in sorted(changed.items()):
        if not is_coverage_subject(path):
            continue
        entry = None
        for report in reports:
            entry = report.entry_for(path)
            if entry is not None:
                break
        covered = len(lines & entry.covered) if entry else 0
        per_file[path] = (covered, len(lines), min(lines))
        covered_total += covered
        changed_total += len(lines)
    total = 100.0 * covered_total / changed_total if changed_total else 100.0
    return NewCodeView(total, changed_total, covered_total, per_file)


def _hunk_added_lines(rows: list[str], start: int, count: int) -> set[int]:
    """Line numbers of the first `count` '+' rows of one hunk body."""
    added: set[int] = set()
    line_no = start
    seen = 0
    for row in rows:
        if seen >= count:
            break
        if row.startswith("\\"):
            # "\ No newline at end of file" annotates, never advances
            continue
        if row.startswith("+"):
            added.add(line_no)
            line_no += 1
            seen += 1
        elif row.startswith("-"):
            continue
        else:
            line_no += 1
    return added


def _flush_hunk(
    changed: dict[str, set[int]],
    current: str | None,
    hunk_rows: list[str],
    start: int,
    count: int,
) -> None:
    if current is not None:
        changed[current] |= _hunk_added_lines(hunk_rows, start, count)


def _diff_raw(root: Path, spec: str) -> str:
    """Run one unified=0 diff and return its stdout; raise on failure."""
    return subprocess.run(
        ["git", "diff", "--unified=0", spec],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout


def _diff_change(root: Path, base: str) -> str:
    """Diff text of the change under review against base.

    Primary reading is the merge-base (three-dot) diff. On a
    pull_request checkout the runner fetches the merge ref at depth 1,
    so neither side carries the common history and three-dot fails with
    exit 128 (no merge base) even though both refs resolve - the hosted
    canary PR exercise hit exactly that. The fallback is the two-dot
    tree diff, which needs no ancestry: on a merge ref the tree already
    contains the base branch, so the tree diff is exactly the change
    under review. Both failing is fatal - the gate never guesses.
    """
    try:
        return _diff_raw(root, f"{base}...HEAD")
    except (subprocess.SubprocessError, OSError):
        return _diff_raw(root, f"{base}..HEAD")


def git_changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """Added lines per file between base and HEAD.

    The diff semantics - merge-base first, tree-diff fallback on the
    shallow pull_request checkout - live in `_diff_change`; the gate
    maps every git failure to its own fail-closed error.
    """
    try:
        raw = _diff_change(root, base)
    except (subprocess.SubprocessError, OSError) as exc:
        raise CoverageGateError(f"git diff against {base!r} failed: {exc}") from exc
    changed: dict[str, set[int]] = {}
    current: str | None = None
    hunk_rows: list[str] = []
    start = count = 0
    for row in raw.splitlines():
        if row.startswith("+++ /dev/null"):
            _flush_hunk(changed, current, hunk_rows, start, count)
            current, hunk_rows = None, []
        elif row.startswith("+++ b/"):
            _flush_hunk(changed, current, hunk_rows, start, count)
            current = _normalize(row[len("+++ b/") :])
            changed.setdefault(current, set())
            hunk_rows = []
        elif row.startswith("@@") and current is not None:
            _flush_hunk(changed, current, hunk_rows, start, count)
            hunk_rows = []
            plus = row.partition("+")[2].partition("@")[0].strip()
            start_text, _, count_text = plus.partition(",")
            start = int(start_text)
            count = int(count_text) if count_text else 1
        elif current is not None and count > 0:
            hunk_rows.append(row)
    _flush_hunk(changed, current, hunk_rows, start, count)
    return {path: lines for path, lines in changed.items() if lines}


@dataclass
class GateResult:
    exit_code: int
    findings: list[tuple[str, int, str]]  # path, line, message for low-new-coverage
    sections: list[str]  # markdown for the report


def _overall_section(reports: list[CoverageReport], floor: float) -> tuple[list[str], int]:
    lines = [
        "### Test assurance: measured coverage",
        "",
        "| Report | Toolchain | Overall | Floor |",
        "|---|---|---|---|",
    ]
    failing = 0
    for report in reports:
        mark = "" if report.total_percent >= floor else " **below floor**"
        if mark:
            failing += 1
        lines.append(
            f"| `{report.path}` | {report.source} | {report.total_percent:.1f}% "
            f"| {floor:g}%{mark} |"
        )
    lines.append("")
    return lines, failing


def _new_code_section(
    view: NewCodeView, floor: float
) -> tuple[list[str], list[tuple[str, int, str]]]:
    lines = [
        "### Test assurance: new-code coverage",
        "",
        f"{view.covered_total} of {view.changed_total} added line(s) exercised "
        f"- **{view.total_percent:.1f}%** against a {floor:g}% floor.",
        "",
    ]
    if not view.changed_total:
        lines.append("No changed lines in the diff window - nothing to assure.")
        lines.append("")
        return lines, []
    worst = sorted(view.per_file.items(), key=lambda kv: kv[1][0] / max(1, kv[1][1]))[:10]
    lines += ["| File | Covered | Added |", "|---|---|---|"]
    findings: list[tuple[str, int, str]] = []
    for path, (covered, total, first_line) in worst:
        percent = 100.0 * covered / total if total else 100.0
        lines.append(f"| `{path}` | {covered} | {total} |")
        if percent < floor:
            findings.append(
                (
                    path,
                    first_line,
                    f"new-code coverage {percent:.0f}% ({covered} of {total} added "
                    f"lines) below the {floor:g}% floor",
                )
            )
    lines.append("")
    return lines, findings


def run_gate(
    root: Path,
    *,
    min_coverage: float,
    min_new_coverage: float,
    diff_base: str,
    report_paths: list[str],
) -> GateResult:
    """Evaluate the requested coverage gates; raise on unevaluable gates."""
    gate_requested = min_coverage > 0 or min_new_coverage > 0
    if not gate_requested:
        return GateResult(0, [], [])
    reports = discover_reports(root, report_paths)
    if not reports:
        raise CoverageGateError(
            "coverage gate requested but no report found "
            f"(looked for: {', '.join(report_paths) or ', '.join(STANDARD_REPORTS)})"
        )
    exit_code = 0
    sections: list[str] = []
    findings: list[tuple[str, int, str]] = []
    if min_coverage > 0:
        overall, failing = _overall_section(reports, min_coverage)
        sections += overall
        if failing:
            exit_code = 1
    if min_new_coverage > 0:
        if not diff_base:
            raise CoverageGateError("new-code gate requested but --diff-base is empty")
        changed = git_changed_lines(root, diff_base)
        view = new_code_view(reports, changed)
        section, new_findings = _new_code_section(view, min_new_coverage)
        sections += section
        findings += new_findings
        if view.total_percent < min_new_coverage and view.changed_total:
            exit_code = 1
    return GateResult(exit_code, findings, sections)


def main(argv: list[str] | None = None) -> int:
    """Standalone gate entry for the CI job that runs after the stacks."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="Repo path the reports belong to")
    parser.add_argument("--min-coverage", type=float, default=0.0)
    parser.add_argument("--min-new-coverage", type=float, default=0.0)
    parser.add_argument("--diff-base", default="")
    parser.add_argument("--coverage-report", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        gate = run_gate(
            args.root,
            min_coverage=args.min_coverage,
            min_new_coverage=args.min_new_coverage,
            diff_base=args.diff_base,
            report_paths=args.coverage_report,
        )
    except CoverageGateError as exc:
        print(f"coverage gate rejected: {exc}", file=sys.stderr)
        return 2
    for path, line, message in gate.findings:
        print(f"::warning file={path},line={line}::[low-new-coverage] {message}")
    if gate.sections:
        print("\n".join(gate.sections))
    return gate.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
