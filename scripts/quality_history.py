#!/usr/bin/env python3
"""Quality history: the trend table behind the ci-success summary.

A ratchet baseline proves today is not worse than yesterday; it cannot
show where yesterday came from. SonarQube's durable lesson is that
quality is a trajectory, not a snapshot - so the ci-success job appends
one row per run to a JSONL history that ``actions/cache`` carries
across runs on the same branch, and renders the last N rows as a trend
table right under the job matrix.

The file is append-only and self-contained:

    quality_history.py record --file hist.jsonl --run-id 42 --ref main \
        --sha abc123 --verdict failed --coverage-min 80 --failed-jobs rust,java
    quality_history.py trend --file hist.jsonl --last 10

Contract:
- exit 0  row recorded / table rendered (or no history yet - trend
    renders the enable hint instead of failing; absence of history is
    a first-run condition, not an error)
- exit 2  the history file exists but cannot be read or appended -
    fail-closed, same semantics as every gate in this template.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

FIELDS = ("ts", "run_id", "ref", "sha", "verdict", "coverage_min", "failed_jobs")
VERDICTS = ("passed", "failed")


class QualityHistoryError(Exception):
    """The history file cannot be read or appended - maps to exit 2."""


@dataclass
class HistoryRow:
    """One CI run in the trend: the record writer validates it."""

    run_id: int
    ref: str
    sha: str
    verdict: str
    coverage_min: float = 0.0
    failed_jobs: list[str] = field(default_factory=list)
    ts: str = ""

    def normalized(self) -> dict:
        """The dict that hits the file: sorted keys, sorted job names."""
        return {
            "ts": self.ts or _utc_now(),
            "run_id": int(self.run_id),
            "ref": self.ref,
            "sha": self.sha,
            "verdict": self.verdict,
            "coverage_min": float(self.coverage_min),
            "failed_jobs": sorted(self.failed_jobs),
        }


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(path: Path, row: HistoryRow) -> dict:
    """Append one deterministic JSONL row; returns it for display."""
    if row.verdict not in VERDICTS:
        raise QualityHistoryError(f"verdict must be one of {VERDICTS}, got {row.verdict!r}")
    data = row.normalized()
    line = json.dumps(data, sort_keys=True, separators=(",", ":"))
    try:
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        raise QualityHistoryError(f"cannot append to {path}: {exc}") from exc
    return data


def load_rows(path: Path) -> list[dict]:
    """Parse the JSONL history, skipping malformed rows quietly.

    The file lives in a cache that survives tool migrations; a torn or
    ancient row must never break the trend table - it just drops out.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise QualityHistoryError(f"cannot read {path}: {exc}") from exc
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "run_id" in data:
            rows.append(data)
    rows.sort(key=lambda row: (row.get("run_id", 0), str(row.get("ts", ""))))
    return rows


def trend_table(rows: list[dict], last: int) -> list[str]:
    """Markdown trend: the most recent ``last`` runs, oldest first."""
    subset = rows[-last:] if last > 0 else list(rows)
    lines = [
        "### Quality trend",
        "",
        "| Run | When (UTC) | Verdict | Coverage floor | Failed jobs |",
        "|---|---|---|---|---|",
    ]
    for row in subset:
        verdict = str(row.get("verdict", "?"))
        verdict_cell = verdict if verdict == "passed" else f"**{verdict}**"
        failed = row.get("failed_jobs") or []
        failed_text = ", ".join(str(name) for name in failed) if failed else "-"
        floor = float(row.get("coverage_min") or 0)
        lines.append(
            f"| {row.get('run_id', '?')} | {row.get('ts', '?')} | {verdict_cell} "
            f"| {floor:g}% | {failed_text} |"
        )
    lines.append("")
    return lines


def run_trend(path: Path, last: int) -> list[str]:
    rows = load_rows(path)
    if not rows:
        return [
            "### Quality trend",
            "",
            "No history yet - the trend table appears once this branch has",
            "recorded a run. History is carried by the CI cache on this ref.",
            "",
        ]
    return trend_table(rows, last)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="append one run to the history")
    rec.add_argument("--file", type=Path, required=True)
    rec.add_argument("--run-id", type=int, required=True)
    rec.add_argument("--ref", default="")
    rec.add_argument("--sha", default="")
    rec.add_argument("--verdict", required=True, choices=VERDICTS)
    rec.add_argument("--coverage-min", type=float, default=0.0)
    rec.add_argument("--failed-jobs", default="", help="comma-separated job names that failed")
    rec.add_argument("--ts", default="", help="ISO-8601 UTC; defaults to now")

    tre = sub.add_parser("trend", help="render the markdown trend table")
    tre.add_argument("--file", type=Path, required=True)
    tre.add_argument("--last", type=int, default=10)

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            failed = [name for name in args.failed_jobs.split(",") if name]
            data = record(
                args.file,
                HistoryRow(
                    run_id=args.run_id,
                    ref=args.ref,
                    sha=args.sha,
                    verdict=args.verdict,
                    coverage_min=args.coverage_min,
                    failed_jobs=failed,
                    ts=args.ts,
                ),
            )
            print(f"recorded run {data['run_id']} -> {args.file}")
        else:
            print("\n".join(run_trend(args.file, args.last)), end="")
    except QualityHistoryError as exc:
        print(f"quality history rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
