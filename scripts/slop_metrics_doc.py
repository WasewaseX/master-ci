#!/usr/bin/env python3
"""The structured metrics document: raw values, not finding messages.

Audit finding G (2026-11-17): the snapshot extractor parsed metric
values out of SARIF finding MESSAGES, so a metric below its threshold
- a healthy measurement - produced no finding and the snapshot store
lost the value. The store therefore kept a history of failures only.

This module is the other source of truth: the scan pipeline's measured
bundle (density facts, corpus coverage, coupling, churn) rendered as a
versioned JSON document where every metric carries

- value      - the actual measurement, present whenever the metric was
                measured, including the healthy below-threshold values
                (a zero or a 0.8% block density is REAL DATA now);
- state      - "measured" or "not-measured" (no corpus, no graph, no
                history) - measured-clean and not-measured finally
                travel separately;
- coverage   - the corpus states beside the ratios: eligible, measured,
                parse-failed (named), unsupported (named), excluded by
                rule (counted), so a shrunken corpus can never read as a
                clean one.

The document is deterministic: no timestamps (the snapshot row owns
sha/ref/ts), values derived only from the scan's own facts. The
snapshot extractor prefers this document (--metrics) and keeps the
SARIF-message path as the fallback for scans that did not emit one.

Contract:
- exit 0  the document written
- exit 2  the path is not writable (fail closed: a requested metrics
    document that cannot be written is a configuration error, never a
    silent gap in the store)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from slop_debt import debt_report
from slop_density import DensityCoverage, DensityFacts
from slop_hotspot import measure_hotspot
from slop_model import Finding, is_blocking
from slop_reliability import (
    RATING_SELF,
    RELIABILITY_CHECKS,
    reliability_grade,
    weighted_score,
)

SCHEMA = "metrics-v1"


def _engine_version() -> str:
    """Best-effort git describe; the snapshot row already owns the sha."""
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _ratio(value: float | None, state: str) -> dict[str, object]:
    return {"value": value, "state": state}


def _density_metrics(
    facts: DensityFacts | None, findings: list[Finding]
) -> dict[str, dict[str, object]]:
    if facts is None:
        return {
            "complexity-density": _ratio(None, "not-measured"),
            "duplication-density-pct": _ratio(None, "not-measured"),
            "dup-block-density-pct": _ratio(None, "not-measured"),
            "debt-ratio-pct": _ratio(None, "not-measured"),
        }
    report = debt_report(findings, facts.exec_lines)
    return {
        "complexity-density": _ratio(1000 * facts.complexity / facts.exec_lines, "measured"),
        "duplication-density-pct": _ratio(100 * facts.duplicated / facts.exec_lines, "measured"),
        "dup-block-density-pct": _ratio(100 * facts.dup_block_lines / facts.exec_lines, "measured"),
        "debt-ratio-pct": _ratio(report.ratio, "measured"),
    }


def _reliability_metrics(findings: list[Finding]) -> dict[str, dict[str, object]]:
    """The grade is always computable from the dimension's severities."""
    rated = [f for f in findings if f.check in RELIABILITY_CHECKS and f.check != RATING_SELF]
    blocking = sum(1 for f in rated if not f.advisory)
    advisory = len(rated) - blocking
    return {
        "reliability-grade": {"value": reliability_grade(blocking, advisory), "state": "measured"},
        "reliability-score": {"value": weighted_score(blocking, advisory), "state": "measured"},
    }


def _coupling_metrics(bundle: dict) -> dict[str, dict[str, object]]:
    coupling = bundle.get("coupling")
    if coupling is None or not coupling.worst_fan_out:
        return {"max-fan-out": _ratio(None, "not-measured")}
    return {"max-fan-out": _ratio(coupling.worst_fan_out[0][1], "measured")}


def _hotspot_metrics(bundle: dict) -> dict[str, dict[str, object]]:
    facts: DensityFacts | None = bundle.get("density")
    coupling = bundle.get("coupling")
    if facts is None:
        return {"hotspot-top-risk": _ratio(None, "not-measured")}
    hotspot = measure_hotspot(facts, coupling, bundle.get("churn", {}), bundle.get("defects", {}))
    if not hotspot.scored:
        return {"hotspot-top-risk": _ratio(None, "not-measured")}
    return {"hotspot-top-risk": _ratio(hotspot.max_risk, "measured")}


def _coverage_doc(coverage: DensityCoverage) -> dict[str, object]:
    excluded = (
        coverage.excluded_test
        + coverage.excluded_generated
        + coverage.excluded_ignored
        + coverage.excluded_nonsource
    )
    return {
        "eligible": coverage.eligible,
        "measured": coverage.measured,
        "parse_failed": list(coverage.parse_failed),
        "unsupported": list(coverage.unsupported_names),
        "unsupported_count": coverage.unsupported,
        "excluded_by_rule": excluded,
    }


def build_document(findings: list[Finding], bundle: dict) -> dict[str, object]:
    """The whole document: engine provenance, per-metric cells, coverage."""
    blocking = sum(1 for f in findings if is_blocking(f.check))
    metrics: dict[str, dict[str, object]] = {
        "blocking-findings": {"value": float(blocking), "state": "measured"},
        "advisory-findings": {"value": float(len(findings) - blocking), "state": "measured"},
        "low-cohesion-classes": {
            "value": float(sum(1 for f in findings if f.check == "low-cohesion")),
            "state": "measured",
        },
        "unstable-hubs": {
            "value": float(sum(1 for f in findings if f.check == "unstable-hub")),
            "state": "measured",
        },
    }
    metrics.update(_density_metrics(bundle.get("density"), findings))
    metrics.update(_reliability_metrics(findings))
    metrics.update(_coupling_metrics(bundle))
    metrics.update(_hotspot_metrics(bundle))
    coverage = bundle.get("coverage")
    return {
        "schema": SCHEMA,
        "engine": {"version": _engine_version(), "checks": len(findings)},
        "metrics": metrics,
        "coverage": _coverage_doc(coverage) if coverage else None,
    }


def write_document(path: str | Path, findings: list[Finding], bundle: dict) -> None:
    """Render and write the document; OSError fails closed at the caller."""
    location = Path(path)
    location.write_text(
        json.dumps(build_document(findings, bundle), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_document(path: str | Path) -> dict[str, object]:
    """Load one metrics document; every surprise is a ValueError."""
    location = Path(path)
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {location}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{location} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{location} is not a {SCHEMA} metrics document")
    if not isinstance(data.get("metrics"), dict):
        raise ValueError(f"{location}: metrics must be an object")
    return data


def metrics_from_document(document: dict[str, object]) -> dict[str, float | str]:
    """The store's metric dictionary: measured values only, others omitted.

    The same omit-not-zero rule the SARIF bridge keeps - but here a
    measured clean scan carries its real value, so the history keeps
    the healthy measurements the message-scraping bridge lost.
    """
    out: dict[str, float | str] = {}
    raw_metrics = document["metrics"]
    if not isinstance(raw_metrics, dict):
        return out
    for key, cell in raw_metrics.items():
        if not isinstance(cell, dict):
            continue
        if cell.get("state") != "measured":
            continue
        value = cell.get("value")
        if value is None:
            continue
        out[str(key)] = value
    return out


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI shim
    """Read a metrics document and print the store's metric dictionary."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metrics", required=True, help="the scan's metrics-v1 document")
    args = parser.parse_args(argv)
    try:
        document = read_document(args.metrics)
    except ValueError as exc:
        print(f"metric document rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(metrics_from_document(document), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
