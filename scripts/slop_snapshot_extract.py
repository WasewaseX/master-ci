#!/usr/bin/env python3
"""SARIF-to-snapshot bridge: the feed that turns a scan into metric rows.

The metric engines report through the scan's SARIF (``--sarif
ai_integrity.sarif`` in the ai-integrity job), and the snapshot store
speaks metric dictionaries (``slop_snapshots.py record --metrics``).
This bridge is the one-way adapter between the two, so a CI job can
append one durable metric row per run without re-running any engine:

    metrics=$(python3 slop_snapshot_extract.py --sarif ai_integrity.sarif)
    python3 slop_snapshots.py record --file quality-store/snapshots.jsonl \
        --sha "$GITHUB_SHA" --ref "$GITHUB_REF_NAME" --project . \
        --metrics "$metrics"

Extraction rules, in honesty order:

- Finding-count metrics (``blocking-findings``, ``advisory-findings``,
``low-cohesion-classes``, ``unstable-hubs``) count SARIF results by
rule id and severity. A scan that ran always speaks here - zero is a
real measurement, not an absence.
- Ratio metrics (``debt-ratio-pct``, ``complexity-density``,
``duplication-density-pct``, ``hotspot-top-risk``, ``max-fan-out``)
parse the number out of the finding's message. The engines emit at
most one finding per scan for each, so the first match is THE match.
When the engine reported no finding the metric is OMITTED, never
zero-filled - SARIF cannot distinguish "measured clean" from "not
measured", and a zero would read as a measurement that never happened.
- ``reliability-grade`` comes from the rating message's band letter;
``reliability-score`` is the same finding's percent-of-dev-cost, the
number the band was graded from. One finding, both fields.
- Blocking/advisory classification reads the SARIF properties the
writer already stamped (``security-severity`` 8.0 = blocking), which
are derived from the model's blocking registry at write time - the
bridge re-imports no registry and cannot drift from the scan.

Contract:
- exit 0  the metrics JSON printed as one line (keys are exactly the
    snapshot store's METRIC_FIELDS; absent metrics are omitted)
- exit 2  fail-closed: the file is missing, unreadable, not SARIF
    (no runs array), or a result is shaped impossible to read
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

BLOCKING_SEVERITY = "8.0"

COUNT_METRICS = {
    "blocking-findings",
    "advisory-findings",
    "low-cohesion-classes",
    "unstable-hubs",
}

# finding message -> (metric key, pattern). The engines render each
# number exactly once per scan; the first match is the value.
MESSAGE_PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "debt-ratio": ("debt-ratio-pct", re.compile(r"debt ratio (\d+(?:\.\d+)?)%")),
    "complexity-density": ("complexity-density", re.compile(r"complexity density (\d+)")),
    "duplication-density": (
        "duplication-density-pct",
        re.compile(r"duplication density (\d+(?:\.\d+)?)%"),
    ),
    "hotspot-risk": ("hotspot-top-risk", re.compile(r"hotspot risk (\d+(?:\.\d+)?)")),
    "high-coupling": ("max-fan-out", re.compile(r"coupling fan-out (\d+)")),
    "reliability-rating": (
        "reliability-grade",
        re.compile(r"reliability rating ([A-E]) \((\d+(?:\.\d+)?)% of dev cost"),
    ),
}


class ExtractError(Exception):
    """The SARIF cannot be read - maps to exit 2."""


def _severity_of(item: dict) -> str:
    """The writer-stamped blocking verdict, empty when absent."""
    properties = item.get("properties")
    if not isinstance(properties, dict):
        return ""
    return str(properties.get("security-severity", ""))


def _counts(results: list[dict]) -> dict[str, float | str]:
    """The four count metrics - a scan that ran always speaks these."""
    blocking = 0
    cohesion = 0
    hubs = 0
    for item in results:
        if _severity_of(item) == BLOCKING_SEVERITY:
            blocking += 1
        rule = str(item.get("ruleId", ""))
        if rule == "low-cohesion":
            cohesion += 1
        elif rule == "unstable-hub":
            hubs += 1
    return {
        "blocking-findings": float(blocking),
        "advisory-findings": float(len(results) - blocking),
        "low-cohesion-classes": float(cohesion),
        "unstable-hubs": float(hubs),
    }


def _message_metrics(results: list[dict]) -> dict[str, float | str]:
    """Parse the engines' one-finding-per-scan numbers out of messages."""
    found: dict[str, float | str] = {}
    for item in results:
        pair = MESSAGE_PATTERNS.get(str(item.get("ruleId", "")))
        if pair is None or pair[0] in found:
            continue
        key, pattern = pair
        message = item.get("message")
        text = str(message.get("text", "")) if isinstance(message, dict) else ""
        match = pattern.search(text)
        if not match:
            continue
        if key == "reliability-grade":
            found["reliability-grade"] = str(match.group(1))
            found["reliability-score"] = float(match.group(2))
        else:
            found[key] = float(match.group(1))
    return found


def extract(sarif: dict) -> dict[str, float | str]:
    """The metric dictionary one scan contributes to the store."""
    runs = sarif.get("runs")
    if not isinstance(runs, list):
        raise ExtractError("not SARIF: no runs array")
    results: list[dict] = []
    for run in runs:
        batch = run.get("results")
        if batch is None:
            continue
        if not isinstance(batch, list):
            raise ExtractError("SARIF run has a non-list results field")
        results.extend(item for item in batch if isinstance(item, dict))
    metrics = _counts(results)
    metrics.update(_message_metrics(results))
    return metrics


def load_sarif(path: str | Path) -> dict:
    """Read the SARIF file; every surprise is an ExtractError."""
    location = Path(path)
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExtractError(f"cannot read {location}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ExtractError(f"{location} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractError(f"{location} must be a SARIF JSON object")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sarif", required=True, help="the scan's SARIF 2.1.0 file")
    args = parser.parse_args(argv)
    try:
        metrics = extract(load_sarif(args.sarif))
    except ExtractError as exc:
        print(f"metric snapshot extract rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(metrics, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
