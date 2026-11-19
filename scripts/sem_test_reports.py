"""The Semaphore test-reports triad: compile, combine, gen-pipeline-report.

Semaphore's flow is three commands: each job emits JUnit XML, an
epilogue publishes it, and an after-pipeline job merges every report
into one failed-first, slowest-first pipeline report. This module is
that triad in one tool:

- parse: one or more JUnit XML files (pytest --junitxml, jest-junit,
    GoTestSum, ...) into the canonical JSON shape - one record per
    test case with suite/class/name identity, status, duration and
    the failure message,
- combine: the merged, deduplicated corpus across all inputs,
- report: the markdown pipeline report (failed first, then slowest)
    ready for GITHUB_STEP_SUMMARY, plus a --generate-mcp-summary
    style compact JSON for agent consumption.

Exit code: 1 when the merged corpus holds failures (the gate path),
0 otherwise. `--epilogue` inverts it: report-only, always 0, the
always-run publishing semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    pass

MAX_MESSAGE_CHARS = 200
MAX_REPORT_ROWS = 50


def _norm_id(classname: str, name: str) -> str:
    """The test identity Semaphore's dashboard shows: suite::class::name."""
    return "::".join(part for part in (classname, name) if part)


def _text_of(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def compile_junit(path: Path) -> list[dict]:
    """One JUnit XML file into canonical case records.

    Malformed XML yields [] with a stderr note - a missing report must
    degrade loudly in the summary, not crash the pipeline report.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        print(f"WARN: {path}: unreadable JUnit XML ({exc})", file=sys.stderr)
        return []
    cases: list[dict] = []
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        classname = case.get("classname", "")
        try:
            duration = float(case.get("time", "0") or 0)
        except ValueError:
            duration = 0.0
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None or error is not None:
            status = "failed"
            message = _text_of(failure if failure is not None else error)
        elif skipped is not None:
            status = "skipped"
            message = _text_of(skipped)
        else:
            status = "passed"
            message = ""
        cases.append(
            {
                "test_id": _norm_id(classname, name),
                "file": str(path),
                "status": status,
                "duration_s": round(duration, 6),
                "message": message[:MAX_MESSAGE_CHARS],
            }
        )
    return cases


def _merge_one(test_id: str, runs: list[dict]) -> dict:
    """The combined record for one test id seen across report files.

    A test id seen twice with differing status is the flaky signal -
    the failure stays (the gate still fails) and the flake is counted,
    mirroring Semaphore's same-commit-differs rule.
    """
    statuses = {run["status"] for run in runs}
    if "failed" in statuses:
        status = "failed"
    elif statuses == {"skipped"}:
        status = "skipped"
    elif "skipped" in statuses:
        status = "flaky"
    else:
        status = "passed"
    durations = [run["duration_s"] for run in runs]
    messages = [run["message"] for run in runs if run["message"]]
    return {
        "test_id": test_id,
        "status": status,
        "duration_s": max(durations),
        "runs": len(runs),
        "flaky": len(statuses - {"skipped"}) > 1,
        "message": messages[0] if messages else "",
    }


def combine(reports: list[list[dict]]) -> dict:
    """The deduplicated corpus and the totals across every input file."""
    by_id: dict[str, list[dict]] = {}
    for report in reports:
        for case in report:
            by_id.setdefault(case["test_id"], []).append(case)
    merged = [_merge_one(test_id, runs) for test_id, runs in by_id.items()]
    failed = sum(1 for case in merged if case["status"] == "failed")
    skipped = sum(1 for case in merged if case["status"] == "skipped")
    # The flaky bucket overlaps failed: a test that failed in one report
    # and passed in another still fails the gate, and it still counts as
    # the disruption Semaphore's dashboard tracks.
    flaky = sum(1 for case in merged if case["flaky"])
    total = len(merged)
    passed = sum(1 for case in merged if case["status"] == "passed")
    gate = total - skipped
    rate = round(passed / gate, 4) if gate else 1.0
    return {
        "totals": {
            "tests": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "flaky": flaky,
            "pass_rate": rate,
        },
        "tests": merged,
    }


def pipeline_report(corpus: dict) -> str:
    """The failed-first, slowest-first markdown report."""
    totals = corpus["totals"]
    lines = [
        "## Test report",
        "",
        f"- tests: {totals['tests']}  passed: {totals['passed']}  "
        f"failed: {totals['failed']}  flaky: {totals['flaky']}  "
        f"skipped: {totals['skipped']}",
        f"- pass rate: {totals['pass_rate']:.1%}",
        "",
    ]
    ranked = sorted(
        corpus["tests"],
        key=lambda case: (
            case["status"] != "failed",
            case["status"] != "flaky",
            -case["duration_s"],
        ),
    )
    rows = [case for case in ranked if case["status"] in ("failed", "flaky", "skipped")]
    slowest = sorted(ranked, key=lambda case: -case["duration_s"])[:5]
    if rows:
        lines.append("| status | test | duration |")
        lines.append("| --- | --- | --- |")
        for case in rows[:MAX_REPORT_ROWS]:
            lines.append(f"| {case['status']} | `{case['test_id']}` | {case['duration_s']:.3f}s |")
        if len(rows) > MAX_REPORT_ROWS:
            lines.append(f"| ... | {len(rows) - MAX_REPORT_ROWS} more | |")
        lines.append("")
    if slowest:
        lines.append("### Slowest")
        lines.append("")
        for case in slowest:
            lines.append(f"- `{case['test_id']}` {case['duration_s']:.3f}s")
        lines.append("")
    return "\n".join(lines)


def mcp_summary(corpus: dict) -> dict:
    """The compact agent-consumption summary (test-results --generate-mcp-summary shape)."""
    problems = [
        {"test_id": case["test_id"], "status": case["status"], "message": case["message"]}
        for case in corpus["tests"]
        if case["status"] in ("failed", "flaky")
    ]
    return {"totals": corpus["totals"], "problems": problems}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sem_test_reports",
        description="JUnit XML compile/combine/pipeline-report in one tool",
    )
    parser.add_argument(
        "--report",
        action="append",
        required=True,
        metavar="PATH",
        help="JUnit XML file; repeat for every job's report",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        metavar="PATH",
        help="write the canonical combined corpus JSON here",
    )
    parser.add_argument(
        "--summary",
        dest="summary_out",
        metavar="PATH",
        help="write the markdown pipeline report here",
    )
    parser.add_argument(
        "--epilogue", action="store_true", help="report-only mode: always exit 0 (epilogue.always)"
    )
    parser.add_argument(
        "--print-summary", action="store_true", help="also print the markdown report to stdout"
    )
    args = parser.parse_args(argv)

    reports = [compile_junit(Path(path)) for path in args.report]
    corpus = combine(reports)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(corpus, indent=2, sort_keys=True), encoding="utf-8"
        )
    report_text = pipeline_report(corpus)
    if args.summary_out:
        Path(args.summary_out).write_text(report_text, encoding="utf-8")
    if args.print_summary:
        print(report_text)
    print(json.dumps(mcp_summary(corpus)))
    if args.epilogue:
        return 0
    return 1 if corpus["totals"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
