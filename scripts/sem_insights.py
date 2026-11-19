"""The Semaphore Insights collector: performance, reliability, frequency, MTTR.

Semaphore's insights dashboards compute four metric series per
(pipeline file, branch) over the run history: performance (median
duration), reliability (pass rate EXCLUDING user-stopped and
auto-cancelled runs - the exclusion rule is the honest part of the
metric), frequency (runs per week) and mean time to recovery. This
module computes the same four from GitHub run history:

- `--runs runs.json` reads `gh run list --json ...` output
    (workflowName, conclusion, createdAt, updatedAt, event, headBranch),
- no `--runs` and `gh` on PATH: the collector shells out and takes
    the last 500 runs,
- output: the metrics JSON (default stdout, `--json` to a file) and
    a markdown table (`--summary` or `--print-summary`) ready for
    GITHUB_STEP_SUMMARY.

Reliability reproduces the exclusion rule exactly: cancelled runs
(the auto-cancel/fail-fast casualties) and neutral/skip_pending
states do not depress the pass rate. `--fail-under` turns the
collector into a gate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    pass

GH_RUN_FIELDS = [
    "workflowName",
    "conclusion",
    "createdAt",
    "updatedAt",
    "event",
    "headBranch",
    "displayTitle",
]
# Conclusions that say nothing about the code: the fail-fast/auto-cancel
# casualties (cancelled) and the never-ran states. Semaphore excludes
# stopped-by-user and auto-cancelled runs from reliability for the same
# reason.
NEUTRAL_CONCLUSIONS = frozenset({"cancelled", "skipped", "neutral", "startup_failure"})
PASS_CONCLUSIONS = frozenset({"success"})
WEEK = timedelta(days=7)


def parse_ts(value: str) -> datetime | None:
    """ISO-8601 run timestamps from the GitHub API."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_seconds(run: dict) -> float | None:
    """UpdatedAt - createdAt when both parse and the run finished."""
    started = parse_ts(str(run.get("createdAt", "")))
    finished = parse_ts(str(run.get("updatedAt", "")))
    if started is None or finished is None:
        return None
    delta = (finished - started).total_seconds()
    return delta if delta >= 0 else None


def load_runs(runs_path: Path | None) -> list[dict]:
    """Run records from a JSON dump or from `gh run list`."""
    if runs_path is not None:
        return json.loads(runs_path.read_text(encoding="utf-8"))
    proc = subprocess.run(
        ["gh", "run", "list", "--limit", "500", "--json", ",".join(GH_RUN_FIELDS)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"WARN: gh run list failed: {proc.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return []


def group_series(runs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Runs keyed by (workflow, branch) - the insights dashboard's axes."""
    series: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        key = (str(run.get("workflowName", "?")), str(run.get("headBranch", "?")))
        series.setdefault(key, []).append(run)
    return series


def _mttr_minutes(finished: list[dict]) -> float | None:
    """Mean time from each failure to the next success, in minutes."""
    stamped: list[tuple[datetime, dict]] = []
    for run in finished:
        ts = parse_ts(str(run.get("createdAt", "")))
        if ts is not None:
            stamped.append((ts, run))
    stamped.sort(key=lambda pair: pair[0])
    recovery_times: list[float] = []
    pending_failure: datetime | None = None
    for ts, run in stamped:
        verdict = str(run.get("conclusion"))
        if verdict not in NEUTRAL_CONCLUSIONS and verdict not in PASS_CONCLUSIONS:
            if pending_failure is None:
                pending_failure = ts
        elif verdict in PASS_CONCLUSIONS and pending_failure is not None:
            recovery_times.append((ts - pending_failure).total_seconds())
            pending_failure = None
    return round(statistics.mean(recovery_times) / 60, 1) if recovery_times else None


def _reliability(finished: list[dict]) -> tuple[float | None, int, int]:
    """Pass rate with the neutral conclusions excluded, plus the counts.

    The exclusion rule is Semaphore's: cancelled (the auto-cancel and
    fail-fast casualties) and never-ran states do not depress the rate.
    """
    judged = [run for run in finished if run.get("conclusion") not in NEUTRAL_CONCLUSIONS]
    passed = sum(1 for run in judged if run["conclusion"] in PASS_CONCLUSIONS)
    failed = len(judged) - passed
    rate = round(passed / len(judged), 4) if judged else None
    return rate, passed, failed


def _recent_count(runs: list[dict], now: datetime) -> int:
    """Runs in the 7-day window ending at `now`."""
    count = 0
    for run in runs:
        ts = parse_ts(str(run.get("createdAt", "")))
        if ts is not None and now - ts <= WEEK:
            count += 1
    return count


def metrics_for(runs: list[dict], now: datetime) -> dict:
    """The four insight metrics for one (workflow, branch) series."""
    durations = [d for d in (duration_seconds(run) for run in runs) if d is not None]
    finished = [run for run in runs if run.get("conclusion")]
    reliability, passed, failed = _reliability(finished)
    return {
        "runs": len(runs),
        "median_duration_s": round(statistics.median(durations), 1) if durations else None,
        "p95_duration_s": round(_p95(durations), 1) if durations else None,
        "reliability": reliability,
        "passed": passed,
        "failed": failed,
        "mttr_minutes": _mttr_minutes(finished),
        "runs_last_7d": _recent_count(runs, now),
    }


def _p95(values: list[float]) -> float:
    """The 95th percentile, nearest-rank; an empty list never reaches here."""
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[rank]


def collect(runs: list[dict], now: datetime | None = None) -> dict:
    """The full insights document over every (workflow, branch) series."""
    now = now or datetime.now(timezone.utc)
    series = group_series(runs)
    entries = {}
    for (workflow, branch), group in sorted(series.items()):
        entries[f"{workflow}@{branch}"] = metrics_for(group, now)
    return {"generated_at": now.isoformat(), "series": entries}


def render_markdown(document: dict) -> str:
    """The GITHUB_STEP_SUMMARY table: worst reliability first."""
    lines = [
        "## CI insights",
        "",
        "| series | runs | reliability | median | p95 | MTTR | last 7d |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    ranked = sorted(
        document["series"].items(),
        key=lambda kv: (kv[1]["reliability"] is None, kv[1]["reliability"] or 0),
    )
    for name, metrics in ranked:
        lines.append(
            f"| {name} | {metrics['runs']} "
            f"| {metrics['reliability'] if metrics['reliability'] is not None else '-'} "
            f"| {metrics['median_duration_s'] or '-'}s "
            f"| {metrics['p95_duration_s'] or '-'}s "
            f"| {metrics['mttr_minutes'] if metrics['mttr_minutes'] is not None else '-'}m "
            f"| {metrics['runs_last_7d']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sem_insights",
        description="Semaphore-style CI insights from GitHub run history",
    )
    parser.add_argument("--runs", metavar="PATH", help="gh run list --json dump; omit to shell out")
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="write metrics JSON here")
    parser.add_argument("--summary", metavar="PATH", help="write the markdown table here")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        metavar="RATE",
        help="exit 1 when any series' reliability is below RATE (0-1)",
    )
    args = parser.parse_args(argv)

    runs = load_runs(Path(args.runs) if args.runs else None)
    if not runs:
        print("WARN: no runs to analyze", file=sys.stderr)
    document = collect(runs)
    payload = json.dumps(document, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(payload, encoding="utf-8")
    table = render_markdown(document)
    if args.summary:
        Path(args.summary).write_text(table, encoding="utf-8")
    if args.print_summary:
        print(table)
    if args.fail_under is not None:
        offenders = {
            name: metrics["reliability"]
            for name, metrics in document["series"].items()
            if metrics["reliability"] is not None and metrics["reliability"] < args.fail_under
        }
        if offenders:
            print(f"FAIL: reliability under {args.fail_under}: {offenders}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
