#!/usr/bin/env python3
"""Coverage gates: measured test assurance, fail-closed.

Roadmap item 4. Test assurance reported inventory before (empty tests,
missing-tests); this module adds proof - the stack test steps run with
coverage, the reports land as artifacts, and the gate consumes them, so
the test-assurance dimension can show measured coverage instead of a
guess.

Formats (the three outputs that cover the mainstream toolchains):

- ``coverage.json``  Python ``coverage json``: totals.percent_covered,
    per-file executed/missing line lists.
- ``coverage.xml``   Cobertura XML: jest/c8/vitest ``--coverage-reporter
    cobertura``, JaCoCo, PHPUnit, gocover-cobertura - one dialect.
- ``cover.out``      Go native coverprofile (``go test -coverprofile``).

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

import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from slop_files import read_text_lax


class CoverageGateError(Exception):
    """A requested gate cannot be evaluated - maps to exit 2."""


@dataclass
class FileCoverage:
    path: str
    percent: float
    covered: set[int] = field(default_factory=set)
    missed: set[int] = field(default_factory=set)


@dataclass
class CoverageReport:
    source: str  # python-json | cobertura | go-cover
    path: str  # report file path, for the report table
    total_percent: float
    files: dict[str, FileCoverage]

    def entry_for(self, changed_path: str) -> FileCoverage | None:
        """Exact match first, then Go-module-style suffix match."""
        entry = self.files.get(changed_path)
        if entry is not None:
            return entry
        suffix = "/" + changed_path
        for candidate in self.files.values():
            if candidate.path.endswith(suffix):
                return candidate
        return None


def _normalize(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _parse_python_json(path: Path) -> CoverageReport:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    totals = data.get("totals") or {}
    if "percent_covered" not in totals:
        # coverage.py always writes totals.percent_covered; its absence
        # means the file is not the report we were promised
        raise CoverageGateError(f"unparseable coverage report {path}: no totals.percent_covered")
    files: dict[str, FileCoverage] = {}
    for name, body in (data.get("files") or {}).items():
        summary = body.get("summary") or {}
        files[_normalize(name)] = FileCoverage(
            path=_normalize(name),
            percent=float(summary.get("percent_covered", 0.0)),
            covered=set(body.get("executed_lines") or []),
            missed=set(body.get("missing_lines") or []),
        )
    total = float(totals.get("percent_covered", 0.0))
    return CoverageReport("python-json", str(path), total, files)


def _parse_cobertura(path: Path) -> CoverageReport:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    rate = root.get("line-rate")
    if rate is None:
        raise CoverageGateError(f"unparseable coverage report {path}: no line-rate")
    files: dict[str, FileCoverage] = {}
    for klass in root.iter("class"):
        name = _normalize(klass.get("filename") or "")
        if not name:
            continue
        covered: set[int] = set()
        missed: set[int] = set()
        for line in klass.iter("line"):
            number = int(line.get("number", 0))
            if int(line.get("hits", 0)) > 0:
                covered.add(number)
            else:
                missed.add(number)
        count = len(covered) + len(missed)
        percent = 100.0 * len(covered) / count if count else 0.0
        files[name] = FileCoverage(name, percent, covered, missed)
    return CoverageReport("cobertura", str(path), 100.0 * float(rate), files)


def _parse_go_cover(path: Path) -> CoverageReport:
    files: dict[str, FileCoverage] = {}
    total_stmts = 0
    covered_stmts = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    for row in lines:
        if not row or row.startswith("mode:"):
            continue
        location, _, rest = row.partition(" ")
        stmts_text, _, count_text = rest.partition(" ")
        file_part, _, span = location.rpartition(":")
        if not file_part or "." not in span:
            continue
        name = _normalize(file_part)
        start_text, _, end_text = span.partition(",")
        try:
            start_line = int(start_text.split(".")[0])
            end_line = int(end_text.split(".")[0])
            stmts = int(stmts_text)
            hits = int(count_text)
        except ValueError as exc:
            raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
        entry = files.setdefault(name, FileCoverage(name, 0.0))
        span_lines = set(range(start_line, end_line + 1))
        if hits > 0:
            entry.covered |= span_lines
        else:
            entry.missed |= span_lines
        total_stmts += stmts
        if hits > 0:
            covered_stmts += stmts
    if total_stmts <= 0:
        raise CoverageGateError(f"unparseable coverage report {path}: no statement rows")
    for entry in files.values():
        seen = len(entry.covered) + len(entry.missed)
        entry.percent = 100.0 * len(entry.covered) / seen if seen else 0.0
        entry.covered -= entry.missed
    total = 100.0 * covered_stmts / total_stmts
    return CoverageReport("go-cover", str(path), total, files)


def parse_report(path: Path) -> CoverageReport:
    """Dispatch on the report's actual shape, not just its name."""
    if path.name == "cover.out":
        return _parse_go_cover(path)
    text = read_text_lax(path)
    if text.lstrip().startswith("{"):
        return _parse_python_json(path)
    if text.lstrip().startswith("<"):
        return _parse_cobertura(path)
    return _parse_go_cover(path)


STANDARD_REPORTS = ("coverage.json", "coverage.xml", "cover.out")


def discover_reports(root: Path, explicit: list[str]) -> list[CoverageReport]:
    """Explicit paths win; otherwise the standard names at the root."""
    candidates = (
        [Path(p) for p in explicit]
        if explicit
        else [root / name for name in STANDARD_REPORTS if (root / name).exists()]
    )
    return [parse_report(path) for path in candidates]


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


def git_changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """Added lines per file between base (merge-base diff) and HEAD."""
    try:
        raw = subprocess.run(
            ["git", "diff", "--unified=0", f"{base}...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout
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
