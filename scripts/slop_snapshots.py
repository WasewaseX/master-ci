#!/usr/bin/env python3
"""Metric snapshots: what the quality engines measured, in a versioned store.

``quality_history.py`` records how CI voted - verdict, coverage floor,
failed jobs - into a JSONL that ``actions/cache`` carries across runs.
That history has two blind spots: it never holds the numbers the quality
engines measured, and a cache unused for seven days is evicted, so the
record evaporates with it. The roadmap's P2 "durable history" row asks
for the other half: metric snapshots in a versioned store a workflow can
commit to a dedicated branch, so the measured numbers survive tool
migrations, cache eviction and engine rewrites. This module is
quality_history's metric sibling - one row per scan per project, the
numbers the engines produced, nothing about how CI judged them.

    slop_snapshots.py record --file store.jsonl --sha <sha> --ref <ref> \
        --project <name> --metrics '{"debt-ratio-pct": 0.14}'
    slop_snapshots.py trend --file store.jsonl --last 10 [--project P]
    slop_snapshots.py diff --file store.jsonl [--project P] \
        [--from <ts|index>] [--to <ts|index>]

METRIC_FIELDS, each with its unit and declared direction (DIRECTIONS):

- debt-ratio-pct - remediation cost as a percent of the SQALE development cost (slop_debt); lower is better
- complexity-density - cyclomatic decision points per 1000 executable lines (slop_density); lower is better
- duplication-density-pct - duplicated executable lines as a percent of the measured corpus (slop_density); lower is better
- reliability-score - the reliability rating as a 0-100 band score, 0 the clean A band (slop_reliability); lower is better
- reliability-grade - the rating's letter band A-E (slop_reliability); categorical, rendered in the trend, judged by no arrow
- hotspot-top-risk - risk points at the hottest file, (churn + 3 x fixes) x ccn x (1 + fan_in / 10) (slop_hotspot); lower is better
- max-fan-out - distinct in-repo modules imported by the worst offender (slop_coupling); lower is better
- unstable-hubs - count of unstable-hub verdicts (slop_coupling); lower is better
- low-cohesion-classes - count of low-cohesion LCOM4 verdicts (slop_lcom4); lower is better
- blocking-findings - count of blocking-severity findings; lower is better
- advisory-findings - count of advisory findings; lower is better - fewer even of the advisory kind is the direction of travel

Absent metrics are omitted, never zero-filled: a zero would read as a
measured clean scan when it means not measured - the engines' own
degrade-quietly contract, kept by the store (the loader drops a row
only when it cannot possibly be ours: a torn line, a non-dict, a
foreign schema version - an ancient row must never break the trend).
The trend renders "-", the diff renders "n/a" and withholds judgment.

Contract:
- exit 0  row recorded / table or diff rendered; a missing store on trend
    is a first-run condition and prints the enable hint.
- exit 2  fail-closed: an unreadable store, a rejected row (unknown key,
    wrong type, non-finite number), a diff over a missing store, a thin
    pool, an unresolvable --from/--to selector, or two sides from
    different projects. The trend/diff asymmetry is deliberate: a trend
    is meaningful before the first row exists, a diff is not - absence
    is not a diffable state.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

SCHEMA_VERSION = 1


class Direction(StrEnum):
    """How a metric's movement maps to quality; CATEGORICAL renders but is never judged."""

    LOWER_IS_BETTER = "lower-is-better"
    HIGHER_IS_BETTER = "higher-is-better"
    CATEGORICAL = "categorical"


METRIC_FIELDS: tuple[str, ...] = (
    "debt-ratio-pct",
    "complexity-density",
    "duplication-density-pct",
    "reliability-score",
    "reliability-grade",
    "hotspot-top-risk",
    "max-fan-out",
    "unstable-hubs",
    "low-cohesion-classes",
    "blocking-findings",
    "advisory-findings",
)

# One direction per field, same membership as METRIC_FIELDS; the diff's verdict derives from here.
DIRECTIONS: dict[str, Direction] = {
    "debt-ratio-pct": Direction.LOWER_IS_BETTER,
    "complexity-density": Direction.LOWER_IS_BETTER,
    "duplication-density-pct": Direction.LOWER_IS_BETTER,
    "reliability-score": Direction.LOWER_IS_BETTER,
    "reliability-grade": Direction.CATEGORICAL,
    "hotspot-top-risk": Direction.LOWER_IS_BETTER,
    "max-fan-out": Direction.LOWER_IS_BETTER,
    "unstable-hubs": Direction.LOWER_IS_BETTER,
    "low-cohesion-classes": Direction.LOWER_IS_BETTER,
    "blocking-findings": Direction.LOWER_IS_BETTER,
    "advisory-findings": Direction.LOWER_IS_BETTER,
}


class QualitySnapshotError(Exception):
    """A row was rejected or the store is not diffable - maps to exit 2."""


@dataclass
class SnapshotRow:
    """One scan's metrics for one project; record validates it."""

    sha: str
    ref: str
    project: str
    metrics: dict[str, float | str] = field(default_factory=dict)
    ts: str = ""

    def normalized(self) -> dict[str, object]:
        """The dict that hits the file: validated and schema-stamped."""
        return {
            "ts": self.ts or _utc_now(),
            "sha": self.sha,
            "ref": self.ref,
            "project": self.project,
            "schema": SCHEMA_VERSION,
            "metrics": _validate_metrics(self.metrics),
        }


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_metrics(metrics: Mapping[str, object]) -> dict[str, float | str]:
    """Check keys and value types; absent keys stay absent, never zeroed.

    An unknown key rejects the row - a store that kept a key it cannot judge would grow
    columns no reader speaks; judged fields take finite numbers, the grade a string.
    """
    checked: dict[str, float | str] = {}
    for key, value in metrics.items():
        if key not in METRIC_FIELDS:
            known = ", ".join(METRIC_FIELDS)
            raise QualitySnapshotError(f"unknown metric key {key!r}; this store speaks: {known}")
        if DIRECTIONS[key] is Direction.CATEGORICAL:
            if not isinstance(value, str):
                raise QualitySnapshotError(f"metric {key!r} takes a letter grade, got {value!r}")
            checked[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QualitySnapshotError(f"metric {key!r} takes a number, got {value!r}")
        if not math.isfinite(float(value)):
            raise QualitySnapshotError(f"metric {key!r} must be finite, got {value!r}")
        checked[key] = value
    return checked


def parse_metrics(raw: str) -> dict[str, float | str]:
    """Parse the --metrics JSON object; anything but an object rejects."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QualitySnapshotError(f"--metrics must be a JSON object: {exc}") from exc
    if not isinstance(data, dict):
        raise QualitySnapshotError("--metrics must be a JSON object of metric keys to values")
    return _validate_metrics(data)


def record(path: Path, row: SnapshotRow) -> dict[str, object]:
    """Append one deterministic JSONL row; returns it for display."""
    data = row.normalized()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError as exc:
        raise QualitySnapshotError(f"cannot append to {path}: {exc}") from exc
    return data


def _sort_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    return (str(row.get("ts", "")), str(row.get("sha", "")), str(row.get("project", "")))


def load_rows(path: Path) -> list[dict[str, object]]:
    """Parse the JSONL store, sorted by (ts, sha, project).

    A line is dropped only when it cannot possibly be ours: torn JSON, a non-dict value,
    a foreign or missing schema version. A current-schema row is kept as written, even
    if a hand-edit gutted it - the render tolerates what a torn row may lack.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise QualitySnapshotError(f"cannot read {path}: {exc}") from exc
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA_VERSION:
            rows.append(data)
    rows.sort(key=_sort_key)
    return rows


def _metrics_of(row: Mapping[str, object]) -> dict[str, object]:
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _fmt(value: object) -> str:
    """One metric cell: numbers compact, the grade as-is, absent as '-'."""
    if value is None:
        return "-"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:g}"


def trend_table(
    rows: list[dict[str, object]], last: int, title: str = "### Metric snapshots"
) -> list[str]:
    """Markdown trend: the most recent ``last`` snapshots, oldest first."""
    subset = rows[-last:] if last > 0 else list(rows)
    header = "| When | Sha | Project | " + " | ".join(METRIC_FIELDS) + " |"
    rule = "|" + "---|" * (len(METRIC_FIELDS) + 3)
    lines = [title, "", header, rule]
    for row in subset:
        metrics = _metrics_of(row)
        identity = " | ".join(str(row.get(key, "?")) for key in ("ts", "sha", "project"))
        cells = " | ".join(_fmt(metrics.get(name)) for name in METRIC_FIELDS)
        lines.append(f"| {identity} | {cells} |")
    lines.append("")
    return lines


def run_trend(path: Path, last: int, project: str = "") -> list[str]:
    """The trend table, or the first-run hint when nothing is recorded."""
    rows = [row for row in load_rows(path) if not project or row.get("project") == project]
    if not rows:
        return [
            "### Metric snapshots",
            "",
            "No snapshots yet - the trend table appears once a scan records",
            "its metrics to the store. Keep the store on a dedicated branch:",
            "cache eviction and tool migrations must not erase the numbers.",
            "",
        ]
    title = f"### Metric snapshots: {project}" if project else "### Metric snapshots"
    return trend_table(rows, last, title)


def _as_number(value: object, name: str) -> float:
    """A non-number stored value rejects the diff: a delta over garbage is a guessed number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualitySnapshotError(f"metric {name!r} is not a number: {value!r}")
    return float(value)


def _judge(direction: Direction, old: float, new: float) -> str:
    """Verdict cell: the word judges via DIRECTIONS, the arrow (^ up, v down) records movement."""
    if new == old:
        return "same"
    if direction is Direction.CATEGORICAL:
        return "-"
    better = new < old if direction is Direction.LOWER_IS_BETTER else new > old
    arrow = "(v)" if new < old else "(^)"
    return f"{'better' if better else 'worse'} {arrow}"


def diff_table(old: Mapping[str, object], new: Mapping[str, object]) -> list[str]:
    """Per-metric deltas between two snapshots of the same project."""
    old_metrics, new_metrics = _metrics_of(old), _metrics_of(new)
    lines = [
        f"### Snapshot diff: {old.get('project', '?')}",
        "",
        f"from {old.get('sha', '?')} @ {old.get('ts', '?')} "
        f"to {new.get('sha', '?')} @ {new.get('ts', '?')}",
        "",
        "| Metric | From | To | Delta | Verdict |",
        "|---|---|---|---|---|",
    ]
    for name in METRIC_FIELDS:
        direction = DIRECTIONS[name]
        if name not in old_metrics or name not in new_metrics:
            from_cell = _fmt(old_metrics[name]) if name in old_metrics else "n/a"
            to_cell = _fmt(new_metrics[name]) if name in new_metrics else "n/a"
            lines.append(f"| {name} | {from_cell} | {to_cell} | n/a | n/a |")
            continue
        before, after = old_metrics[name], new_metrics[name]
        if direction is Direction.CATEGORICAL:
            lines.append(f"| {name} | {_fmt(before)} | {_fmt(after)} | - | - |")
            continue
        old_number, new_number = _as_number(before, name), _as_number(after, name)
        delta = f"{new_number - old_number:+g}"
        verdict = _judge(direction, old_number, new_number)
        lines.append(f"| {name} | {_fmt(before)} | {_fmt(after)} | {delta} | {verdict} |")
    lines.append("")
    return lines


def _resolve(pool: list[dict[str, object]], selector: str) -> dict[str, object]:
    """A selector is a 1-based index into the pool or an exact timestamp."""
    index = int(selector) if selector.isdigit() else 0
    if index:
        if index > len(pool):
            raise QualitySnapshotError(f"snapshot index {selector} out of range 1..{len(pool)}")
        return pool[index - 1]
    for row in pool:
        if str(row.get("ts", "")) == selector:
            return row
    raise QualitySnapshotError(f"no snapshot matches {selector!r}")


def run_diff(path: Path, project: str = "", from_sel: str = "", to_sel: str = "") -> list[str]:
    """Diff two snapshots of one project; default is the last two. An absent
    store, a thin pool, and an unresolvable selector all fail closed - a
    diff needs two snapshots, and absence is not a diffable state.
    """
    scope = f" for project {project!r}" if project else ""
    rows = load_rows(path)
    pool = [row for row in rows if row.get("project") == project] if project else rows
    if not pool:
        raise QualitySnapshotError(f"no snapshots in {path}{scope} - a diff needs two snapshots")
    if from_sel or to_sel:
        old = _resolve(pool, from_sel) if from_sel else pool[-1]
        new = _resolve(pool, to_sel) if to_sel else pool[-1]
    else:
        if len(pool) < 2:
            raise QualitySnapshotError(f"a diff needs two snapshots; {path} holds {len(pool)}")
        old, new = pool[-2], pool[-1]
    if old is new:
        raise QualitySnapshotError("both selectors resolve to the same snapshot")
    old_project, new_project = str(old.get("project", "")), str(new.get("project", ""))
    if old_project != new_project:
        raise QualitySnapshotError(
            f"cannot diff {old_project!r} against {new_project!r}: "
            "a diff compares two snapshots of the same project"
        )
    return diff_table(old, new)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="append one snapshot to the store")
    rec.add_argument("--file", type=Path, required=True)
    rec.add_argument("--sha", required=True)
    rec.add_argument("--ref", default="")
    rec.add_argument("--project", required=True)
    rec.add_argument("--metrics", required=True, help="JSON object of metric keys to values")
    rec.add_argument("--ts", default="", help="ISO-8601 UTC; defaults to now")

    tre = sub.add_parser("trend", help="render the markdown trend table")
    tre.add_argument("--file", type=Path, required=True)
    tre.add_argument("--last", type=int, default=10)
    tre.add_argument("--project", default="")

    dif = sub.add_parser("diff", help="diff two snapshots of one project")
    dif.add_argument("--file", type=Path, required=True)
    dif.add_argument("--project", default="")
    dif.add_argument("--from", dest="from_sel", default="", help="1-based index or exact timestamp")
    dif.add_argument("--to", dest="to_sel", default="", help="1-based index or exact timestamp")

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            data = record(
                args.file,
                SnapshotRow(
                    sha=args.sha,
                    ref=args.ref,
                    project=args.project,
                    metrics=parse_metrics(args.metrics),
                    ts=args.ts,
                ),
            )
            print(f"recorded snapshot for {data['project']} at {data['sha']} -> {args.file}")
        elif args.command == "trend":
            print("\n".join(run_trend(args.file, args.last, args.project)), end="")
        else:
            print("\n".join(run_diff(args.file, args.project, args.from_sel, args.to_sel)), end="")
    except QualitySnapshotError as exc:
        print(f"metric snapshot rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
