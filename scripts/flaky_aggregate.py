#!/usr/bin/env python3
"""Aggregate a per-project flake hunt into one verdict and one table.

v2.11.2 split the hunt across every project ``discover_projects.py``
enumerates: each suite hunts in its own results directory
(``flaky-results/<project>/<suite>/``), each suite's classification
lands in that directory, and this module turns the spread into:

- a per-suite status table for the step summary - a skipped suite is
    named as skipped, with the reason, never dressed up as a completed
    hunt (the P0 audit: "a skipped test suite [must] not look
    equivalent to a fully completed hunt"),
- the merged root report (``flaky-report.json``) the verdict step and
    the quarantine loop consume: the union of the suites' flaky sets,
    the worst status, summed test counts.

A suite directory holds either a ``status.txt`` skip note written by
the workflow (the suite declared itself unhuntable - no runner config,
missing formatter) or a ``report.json`` this module wrote by driving
``flaky_detect`` in-process. A suite whose classification fails
(``flaky_detect`` exit 2 - zero tests, corrupt or missing result
files) is ``failed-hunt`` and fails the aggregate: a hunt that cannot
be executed loudly is never a silent pass, and one failing suite must
not hide behind another's clean verdict.

Exit codes: 0 every hunted suite clean or quarantined-only, 1 any
suite new-flakes (enforcement mode), failed or skipped-but-claimed; 2
the aggregate itself cannot run.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flaky_detect import DEFAULT_REPORT, main as hunt_main

WORST_FIRST = ("failed-hunt", "new-flakes", "quarantined-only", "clean", "skipped")
HUNTED = {"clean", "quarantined-only", "new-flakes"}


@dataclass(frozen=True)
class Suite:
    """One project-suite result: hunted, skipped or failed."""

    project: str
    suite: str
    status: str
    tests_total: int
    flaky: int
    new: int
    detail: str

    @property
    def cell(self) -> str:
        if self.status == "skipped":
            return f"skipped: {self.detail}"
        if self.status == "failed-hunt":
            return f"failed-hunt: {self.detail}"
        counts = f"{self.tests_total} test(s), {self.flaky} flaky"
        counts += f", {self.new} new" if self.new else ""
        return f"{self.status} - {counts}"


def _worst(statuses: list[str]) -> str:
    for status in WORST_FIRST:
        if status in statuses:
            return status
    return "clean"


def classify_suite(directory: Path, args: argparse.Namespace) -> tuple[Suite, dict | None]:
    """Hunt one suite directory, or read the skip note the workflow left.

    Returns the suite's row and its machine report (None when skipped).
    """
    project = directory.parent.name
    family = directory.name
    skip = directory / "status.txt"
    if skip.is_file():
        reason = skip.read_text(encoding="utf-8").strip() or "skipped by the workflow"
        return Suite(project, family, "skipped", 0, 0, 0, reason), None
    report_path = directory / DEFAULT_REPORT
    argv = [
        "--results-dir",
        str(directory),
        "--iterations",
        str(args.iterations),
        "--report",
        str(report_path),
    ]
    if args.baseline:
        argv += ["--baseline", args.baseline]
    if args.fail_on_flaky:
        argv += ["--fail-on-flaky"]
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = hunt_main(argv)
    summary = out.getvalue().rstrip("\n")
    (directory / "summary.md").write_text(summary + "\n", encoding="utf-8")
    if rc == 2 or not report_path.is_file():
        detail = err.getvalue().strip().replace("\n", " ")
        return Suite(project, family, "failed-hunt", 0, 0, 0, detail or "exit 2"), None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return (
        Suite(
            project=project,
            suite=family,
            status=report["status"],
            tests_total=report["tests_total"],
            flaky=len(report["flaky"]),
            new=len(report["new_flaky"]),
            detail="",
        ),
        report,
    )


def merge_report(reports: list[dict], iterations: int) -> dict:
    """One root report in flaky_detect's schema: unions, worst status."""
    merged: dict = {
        "version": reports[0]["version"] if reports else 1,
        "status": _worst([r["status"] for r in reports]) or "clean",
        "iterations": iterations,
        "tests_total": sum(r["tests_total"] for r in reports),
        "flaky": [],
        "always_failed": [],
        "quarantined": [],
        "new_flaky": [],
        "healed": [],
    }
    for key in ("flaky", "always_failed", "quarantined", "new_flaky", "healed"):
        seen: dict[str, object] = {}
        for report in reports:
            for entry in report[key]:
                if isinstance(entry, dict):
                    seen[entry["key"]] = entry
                else:
                    seen[entry] = entry
        merged[key] = [seen[k] for k in sorted(seen, key=str)]
    return merged


def render_table(suites: list[Suite], iterations: int) -> list[str]:
    hunted = [s for s in suites if s.status in HUNTED]
    lines = [
        f"flake hunt: {len(hunted)} suite(s) hunted over {iterations} iteration(s), "
        f"{len(suites)} claimed in total",
        "",
        "| project | suite | result |",
        "|---|---|---|",
    ]
    for suite in sorted(suites, key=lambda s: (s.project, s.suite)):
        lines.append(f"| `{suite.project}` | {suite.suite} | {suite.cell} |")
    return lines


def suite_dirs(results_root: Path) -> list[Path]:
    """Every ``<project>/<suite>`` directory, stable order."""
    if not results_root.is_dir():
        return []
    dirs: list[Path] = []
    for project in sorted(results_root.iterdir()):
        if not project.is_dir():
            continue
        for suite in sorted(project.iterdir()):
            if suite.is_dir():
                dirs.append(suite)
    return dirs


def _safe_classify(directory: Path, args: argparse.Namespace) -> tuple[Suite, dict | None]:
    """Classify one suite; an aggregate that crashes on one suite owns
    the verdict, so every exception becomes a failed-hunt row."""
    try:
        return classify_suite(directory, args)
    except Exception as exc:  # noqa: BLE001 - the aggregate owns the verdict
        return (
            Suite(
                directory.parent.name,
                directory.name,
                "failed-hunt",
                0,
                0,
                0,
                f"{type(exc).__name__}: {exc}",
            ),
            None,
        )


def _build_document(suites: list[Suite], results_root: Path, iterations: int) -> str:
    """The step-summary markdown: the status table, then every hunted
    suite's own flake table."""
    full_summary = ["\n".join(render_table(suites, iterations))]
    for suite in suites:
        per_suite = results_root / suite.project / suite.suite / "summary.md"
        if per_suite.is_file():
            full_summary.append(f"\n### {suite.project} ({suite.suite})\n")
            full_summary.append(per_suite.read_text(encoding="utf-8").rstrip("\n"))
    return "\n".join(full_summary).rstrip("\n") + "\n"


def _verdict(suites: list[Suite], args: argparse.Namespace) -> int:
    """The aggregate's exit ladder: a failed suite always fails, new
    flakes fail in enforcement mode."""
    failed = [s for s in suites if s.status == "failed-hunt"]
    if failed:
        print(
            f"flake hunt: {len(failed)} suite(s) could not be classified - "
            "a hunt that cannot be executed loudly is never a silent pass",
            file=sys.stderr,
        )
        return 1
    new_flakes = [s for s in suites if s.status == "new-flakes"]
    if new_flakes and args.fail_on_flaky:
        print(
            f"flake hunt: {sum(s.new for s in new_flakes)} new flaky test(s) - "
            f"fix or quarantine them in {args.baseline or '.flaky-baseline.json'}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None, /) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True, help="flaky-results root")
    parser.add_argument("--iterations", type=int, required=True)
    parser.add_argument("--baseline", default=None, help="quarantine list per suite")
    parser.add_argument(
        "--fail-on-flaky",
        action="store_true",
        help="exit 1 when a hunted suite reports new flakes",
    )
    parser.add_argument("--report", default="flaky-report.json", help="merged report path")
    parser.add_argument("--summary", default="flaky-summary.md", help="table path")
    args = parser.parse_args(argv)
    results_root = Path(args.results_root)
    suites: list[Suite] = []
    reports: list[dict] = []
    for directory in suite_dirs(results_root):
        suite, report = _safe_classify(directory, args)
        suites.append(suite)
        if report is not None:
            reports.append(report)
    if not suites:
        print(
            "flake hunt: no suite claimed any supported runner - nothing ran; "
            "this is not a pass or a fail"
        )
        return 0
    document = _build_document(suites, results_root, args.iterations)
    Path(args.summary).write_text(document, encoding="utf-8")
    print(document, end="")
    if reports:
        Path(args.report).write_text(
            json.dumps(merge_report(reports, args.iterations), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return _verdict(suites, args)


if __name__ == "__main__":
    sys.exit(main())
