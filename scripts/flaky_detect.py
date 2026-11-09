#!/usr/bin/env python3
"""The flake hunt: find tests whose verdict changes between iterations.

Runs a repository's own test suite N times (the hunt workflow drives the
runners), reads every ``run-<i>`` result file the iterations produced,
and classifies each test:

- **flaky** - both a pass and a fail across iterations: the test's
  verdict is a coin flip, the suite cannot be trusted green
- **always-failed** - failed every iteration: the main CI should already
  be red; listed for completeness, never quarantined
- stable - everything else

A committed quarantine baseline (``.flaky-baseline.json``) records known
flaky tests; the hunt reports them without failing so real regressions
stand out, and ``--fail-on-flaky`` fails the run the moment a *new* flaky
test appears. Healing a flake (fixing it) is an improvement the summary
celebrates, and ``--write-baseline`` records the current flaky set as a
reviewable commit.

Exit codes: 0 clean (quarantined flakes included), 1 new flakes under
``--fail-on-flaky``, 2 fail-closed - a missing or unreadable result file,
non-contiguous run numbering, conflicting verdicts inside one file, zero
tests overall, or an unreadable baseline. A hunt that cannot be executed
loudly is never a silent pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flaky_parsers import FAILED, PASSED, SUFFIXES, FlakyParseError, Outcome, parse_file

BASELINE_VERSION = 1
DEFAULT_BASELINE = ".flaky-baseline.json"
DEFAULT_REPORT = "flaky-report.json"
MAX_SUMMARY_ROWS = 20
ABSENT = "absent"

_RUN_RE = re.compile(r"^run-(\d+)")


class HuntError(Exception):
    """The hunt cannot be executed; the caller must fail closed (exit 2)."""


@dataclass(frozen=True)
class TestVerdict:
    """One test's outcomes across every iteration, in run order."""

    key: str
    runs: tuple[str, ...]

    @property
    def is_flaky(self) -> bool:
        return PASSED in self.runs and FAILED in self.runs

    @property
    def always_fails(self) -> bool:
        return bool(self.runs) and set(self.runs) == {FAILED}


@dataclass(frozen=True)
class BaselineDelta:
    """Quarantined, new and healed flaky tests against the baseline."""

    quarantined: tuple[str, ...]
    new: tuple[str, ...]
    healed: tuple[str, ...]


def load_runs(results_dir: Path, iterations: int | None) -> tuple[dict[int, list[Outcome]], int]:
    """Read every ``run-<i>`` result file; fail closed on any gap or bad file."""
    if not results_dir.is_dir():
        raise HuntError(f"results directory {results_dir} does not exist")
    grouped: dict[int, list[Path]] = defaultdict(list)
    for path in sorted(results_dir.iterdir()):
        match = _RUN_RE.match(path.name)
        if match and path.suffix.lower() in frozenset(SUFFIXES):
            grouped[int(match.group(1))].append(path)
    if not grouped:
        raise HuntError(
            f"no run-<i> result files in {results_dir} - the iterations "
            "produced nothing; the hunt cannot pretend it ran"
        )
    top = max(grouped)
    if set(grouped) != set(range(1, top + 1)):
        missing = sorted(set(range(1, top + 1)) - set(grouped))
        raise HuntError(
            f"non-contiguous runs in {results_dir}: missing iteration(s) "
            f"{', '.join(map(str, missing))} - every iteration must "
            "produce a result file, even if the suite crashed"
        )
    if iterations is not None and iterations != top:
        raise HuntError(f"--iterations {iterations} but the results cover {top} run(s)")
    runs: dict[int, list[Outcome]] = {}
    for number, paths in sorted(grouped.items()):
        outcomes: list[Outcome] = []
        for path in paths:
            try:
                outcomes.extend(parse_file(path))
            except FlakyParseError as exc:
                raise HuntError(f"{path.name}: {exc}") from exc
        _reject_conflicts(number, outcomes)
        runs[number] = outcomes
    return runs, top


def _reject_conflicts(run: int, outcomes: list[Outcome]) -> None:
    """One file naming a test both passed and failed is corruption, not data."""
    seen: dict[str, str] = {}
    for outcome in outcomes:
        prior = seen.setdefault(outcome.key, outcome.outcome)
        if prior != outcome.outcome:
            raise HuntError(
                f"run {run}: test {outcome.key!r} is recorded as both "
                f"{prior!r} and {outcome.outcome!r} in one result file"
            )


def classify(runs: dict[int, list[Outcome]]) -> list[TestVerdict]:
    """Per-test outcomes in run order; a test absent from a run is absent."""
    order: dict[int, int] = {number: i for i, number in enumerate(sorted(runs))}
    merged: dict[str, dict[int, str]] = defaultdict(dict)
    for number, outcomes in runs.items():
        for outcome in outcomes:
            merged[outcome.key][number] = outcome.outcome
    verdicts = []
    for key in sorted(merged):
        verdicts.append(TestVerdict(key, tuple(merged[key].get(n, ABSENT) for n in sorted(order))))
    return verdicts


def load_baseline(path: Path) -> dict[str, dict]:
    """Read the quarantine list; a floor that cannot be read is not a floor."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HuntError(f"baseline {path} does not exist") from None
    except json.JSONDecodeError as exc:
        raise HuntError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != BASELINE_VERSION:
        raise HuntError(
            f"baseline {path} must be version {BASELINE_VERSION} - re-record "
            "it with --write-baseline"
        )
    tests = payload.get("tests")
    if not isinstance(tests, dict) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("first_seen"), str)
        for entry in tests.values()
    ):
        raise HuntError(f"baseline {path}: 'tests' must map keys to first_seen records")
    return tests


def write_baseline(path: Path, flaky_keys: list[str]) -> None:
    """Record the current flaky set; deterministic bytes, reviewed commit.

    Two-space indent and a trailing newline - the template's own hygiene
    gate enforces the editorconfig on consumer trees, and a baseline that
    tripped it would block the commit that records the quarantine.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    payload = {
        "version": BASELINE_VERSION,
        "tests": {key: {"first_seen": today} for key in flaky_keys},
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare(flaky: list[TestVerdict], baseline: dict[str, dict]) -> BaselineDelta:
    """Split the flaky set against the quarantine list."""
    keys = frozenset(v.key for v in flaky if v.is_flaky)
    known = frozenset(baseline)
    return BaselineDelta(
        quarantined=tuple(sorted(keys & known)),
        new=tuple(sorted(keys - known)),
        healed=tuple(sorted(known - keys)),
    )


def render(verdicts: list[TestVerdict], delta: BaselineDelta, iterations: int) -> list[str]:
    """Markdown summary for the step summary, new flakes first."""
    flaky = [v for v in verdicts if v.is_flaky]
    failing = [v for v in verdicts if v.always_fails]
    lines = [
        f"flake hunt: {len(verdicts)} test(s) over {iterations} iteration(s) - "
        f"{len(flaky)} flaky, {len(failing)} always-failing, "
        f"{len(delta.new)} new, {len(delta.healed)} healed",
        "",
    ]
    if not flaky:
        lines.append("No test changed its verdict between iterations.")
        return lines
    rows = [(v, "NEW" if v.key in delta.new else "quarantined") for v in flaky]
    lines += ["| Test | Outcomes | Status |", "|---|---|---|"]
    for verdict, status in rows[:MAX_SUMMARY_ROWS]:
        outcomes = " -> ".join(verdict.runs)
        lines.append(f"| `{verdict.key}` | {outcomes} | {status} |")
    if len(rows) > MAX_SUMMARY_ROWS:
        lines.append(f"| ... | | {len(rows) - MAX_SUMMARY_ROWS} more |")
    if failing:
        lines += ["", "Always failing (the main CI should already be red):"]
        lines += [f"- `{v.key}`" for v in failing[:MAX_SUMMARY_ROWS]]
    if delta.healed:
        lines += ["", "Healed - no longer flaky, removable from the baseline:"]
        lines += [f"- `{key}`" for key in delta.healed[:MAX_SUMMARY_ROWS]]
    return lines


def build_report(verdicts: list[TestVerdict], delta: BaselineDelta, iterations: int) -> dict:
    """Machine-readable result for the workflow artifact.

    ``status`` names the verdict so the workflow can gate the quarantine
    loop on data instead of parsing prose: ``new-flakes`` (delta has new
    flaky tests), ``quarantined-only`` (flaky but all known) or ``clean``.
    """
    flaky = [{"key": v.key, "outcomes": list(v.runs)} for v in verdicts if v.is_flaky]
    status = "new-flakes" if delta.new else ("quarantined-only" if flaky else "clean")
    return {
        "version": BASELINE_VERSION,
        "status": status,
        "iterations": iterations,
        "tests_total": len(verdicts),
        "flaky": flaky,
        "always_failed": [v.key for v in verdicts if v.always_fails],
        "quarantined": list(delta.quarantined),
        "new_flaky": list(delta.new),
        "healed": list(delta.healed),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="flaky-test detection")
    parser.add_argument("--results-dir", required=True, help="directory of run-<i> files")
    parser.add_argument("--iterations", type=int, default=None, help="expected run count")
    parser.add_argument("--baseline", default=None, help=f"quarantine list ({DEFAULT_BASELINE})")
    parser.add_argument("--fail-on-flaky", action="store_true", help="exit 1 on new flakes")
    parser.add_argument(
        "--write-baseline", action="store_true", help="record the current flaky set"
    )
    parser.add_argument("--report", default=DEFAULT_REPORT, help="JSON report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        runs, iterations = load_runs(Path(args.results_dir), args.iterations)
        verdicts = classify(runs)
        if not verdicts:
            raise HuntError("the result files parsed to zero tests")
    except HuntError as exc:
        print(f"flake hunt: {exc}", file=sys.stderr)
        return 2
    if args.write_baseline:
        keys = sorted(v.key for v in verdicts if v.is_flaky)
        write_baseline(Path(args.baseline or DEFAULT_BASELINE), keys)
        print(f"flake hunt: recorded {len(keys)} flaky test(s) in the baseline")
        return 0
    try:
        delta = BaselineDelta((), tuple(v.key for v in verdicts if v.is_flaky), ())
        if args.baseline:
            delta = compare(verdicts, load_baseline(Path(args.baseline)))
    except HuntError as exc:
        print(f"flake hunt: {exc}", file=sys.stderr)
        return 2
    report = build_report(verdicts, delta, iterations)
    Path(args.report).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for line in render(verdicts, delta, iterations):
        print(line)
    if args.fail_on_flaky and delta.new:
        print(
            f"flake hunt: {len(delta.new)} new flaky test(s) - fix or "
            f"quarantine them in {args.baseline or DEFAULT_BASELINE}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
