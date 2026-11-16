#!/usr/bin/env python3
"""Metrics-to-snapshot bridge: the feed that turns a scan into metric rows.

Two sources of truth, in honesty order (v2.18, audit finding G):

- ``--metrics metrics.json`` - the scan's structured metrics document
    (``slop_metrics_doc.py`` via ``--metrics-json``). Preferred: every
    metric carries its ACTUAL value with a measured/not-measured state,
    so a healthy below-threshold measurement reaches the store instead
    of vanishing with the finding it never produced. The document also
    carries the corpus's analysis-coverage states beside the ratios.
- ``--sarif ai_integrity.sarif`` - the legacy path. Ratio metrics are
    parsed out of finding message text, so a metric below its threshold
    (no finding) is OMITTED, never zero-filled: SARIF cannot
    distinguish "measured clean" from "not measured". Kept for scans
    that did not emit the document; the store keeps that history's
    shape.

Extraction rules, either source:

- Finding-count metrics (``blocking-findings``, ``advisory-findings``,
``low-cohesion-classes``, ``unstable-hubs``) count results by rule id
and severity. A scan that ran always speaks here - zero is a real
measurement, not an absence.
- ``reliability-grade``/``reliability-score`` - severity-based (v2.18,
audit finding C): the band letter and the weighted severity score the
band was graded from. The structured document always carries them; the
SARIF path parses the rating message.
- Blocking/advisory classification reads the SARIF properties the
writer already stamped (``security-severity`` 8.0 = blocking), which
are derived from the model's blocking registry at write time - the
bridge re-imports no registry and cannot drift from the scan.

Contract:
- exit 0  the metrics JSON printed as one line (keys are exactly the
    snapshot store's METRIC_FIELDS; absent metrics are omitted)
- exit 2  fail-closed: both sources missing, a file unreadable, not
    the expected shape, or a result impossible to read
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from slop_metrics_doc import metrics_from_document, read_document

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
    "dup-block-density": (
        "dup-block-density-pct",
        re.compile(r"duplication blocks (\d+(?:\.\d+)?)%"),
    ),
    "hotspot-risk": ("hotspot-top-risk", re.compile(r"hotspot risk (\d+(?:\.\d+)?)")),
    "high-coupling": ("max-fan-out", re.compile(r"coupling fan-out (\d+)")),
    "reliability-rating": (
        "reliability-grade",
        re.compile(
            r"reliability rating ([A-E]) \((\d+) blocking \+ (\d+) advisory, "
            r"weighted (\d+)"
        ),
    ),
}


class ExtractError(Exception):
    """The SARIF cannot be read - maps to exit 2."""


def _rule_severities(sarif: dict) -> dict[str, str]:
    """ruleId -> security-severity, from the writer's rule metadata stamp.

    The writer stamps ``security-severity`` on the rule descriptor
    (SARIF 2.1's reportingDescriptor), not on each result - reading it
    per result silently counted ZERO blocking findings since the
    bridge shipped. Result-level properties still win when present
    (the historical fixture shape), but the rule table is the source.
    """
    severities: dict[str, str] = {}
    for run in sarif.get("runs") or []:
        if not isinstance(run, dict):
            continue
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        rules = driver.get("rules") if isinstance(driver, dict) else None
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                continue
            properties = rule.get("properties")
            if isinstance(properties, dict) and "security-severity" in properties:
                severities[rule["id"]] = str(properties["security-severity"])
    return severities


def _severity_of(item: dict, rule_severities: dict[str, str] | None = None) -> str:
    """The writer-stamped blocking verdict, empty when absent."""
    properties = item.get("properties")
    if isinstance(properties, dict) and "security-severity" in properties:
        return str(properties.get("security-severity", ""))
    if rule_severities is not None:
        return rule_severities.get(str(item.get("ruleId", "")), "")
    return ""


def _counts(
    results: list[dict], rule_severities: dict[str, str] | None = None
) -> dict[str, float | str]:
    """The four count metrics - a scan that ran always speaks these."""
    blocking = 0
    cohesion = 0
    hubs = 0
    for item in results:
        if _severity_of(item, rule_severities) == BLOCKING_SEVERITY:
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
            found["reliability-score"] = float(match.group(4))
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
    metrics = _counts(results, _rule_severities(sarif))
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
    parser.add_argument(
        "--metrics",
        help="the scan's structured metrics-v1 document (preferred source)",
    )
    parser.add_argument("--sarif", help="the scan's SARIF 2.1.0 file (legacy source)")
    args = parser.parse_args(argv)
    if not args.metrics and not args.sarif:
        print(
            "metric snapshot extract rejected: give --metrics (preferred) or --sarif",
            file=sys.stderr,
        )
        return 2
    try:
        if args.metrics:
            metrics = metrics_from_document(read_document(args.metrics))
        else:
            metrics = extract(load_sarif(args.sarif))
    except (ExtractError, ValueError) as exc:
        print(f"metric snapshot extract rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(metrics, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
