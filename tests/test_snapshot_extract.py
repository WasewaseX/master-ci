"""Contract tests for the SARIF-to-snapshot bridge (v2.16.0).

The bridge feeds slop_snapshots.record from the scan's SARIF. Its
contract: finding-count metrics always speak (zero is a real
measurement), ratio metrics are parsed from the engines' one finding
per scan and omitted when no finding exists (never zero-filled), the
grade letter and its percent travel together, and anything unreadable
fails closed with exit 2 - never a guessed metric row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import slop_snapshot_extract as sx  # noqa: E402


def _result(rule: str, text: str, blocking: bool) -> dict:
    """One SARIF result shaped exactly as the writer emits it."""
    return {
        "ruleId": rule,
        "message": {"text": text},
        "properties": {"security-severity": "8.0" if blocking else "3.0"},
    }


def _sarif(*results: object) -> dict:
    return {"runs": [{"results": list(results)}]}


def test_count_metrics_always_speak_even_on_a_clean_scan() -> None:
    """A scan that ran measured something: zero counts are real rows."""
    metrics = sx.extract(_sarif())
    assert metrics["blocking-findings"] == 0.0
    assert metrics["advisory-findings"] == 0.0
    assert metrics["low-cohesion-classes"] == 0.0
    assert metrics["unstable-hubs"] == 0.0


def test_blocking_split_reads_the_stamped_severity() -> None:
    """The 8.0/3.0 severity the writer stamped is the blocking verdict."""
    metrics = sx.extract(
        _sarif(
            _result("elided-code", "elided code in a.py", True),
            _result("low-cohesion", "class X", False),
        )
    )
    assert metrics["blocking-findings"] == 1.0
    assert metrics["advisory-findings"] == 1.0
    assert metrics["low-cohesion-classes"] == 1.0


def test_debt_ratio_parsed_from_the_finding_message() -> None:
    """The engines emit one debt finding per scan; its ratio is THE value."""
    metrics = sx.extract(_sarif(_result("debt-ratio", "debt ratio 0.1% (budget 5.0%): 50m", False)))
    assert metrics["debt-ratio-pct"] == 0.1


def test_density_and_hotspot_and_fan_out_parse() -> None:
    """Every ratio metric travels in its finding message; all are read."""
    metrics = sx.extract(
        _sarif(
            _result(
                "complexity-density", "complexity density 247 per 1000 executable lines", False
            ),
            _result("duplication-density", "duplication density 2.3% (budget 5.0%)", False),
            _result("hotspot-risk", "hotspot risk 352 (budget 1200)", False),
            _result("high-coupling", "coupling fan-out 22 (budget 30)", False),
        )
    )
    assert metrics["complexity-density"] == 247.0
    assert metrics["duplication-density-pct"] == 2.3
    assert metrics["hotspot-top-risk"] == 352.0
    assert metrics["max-fan-out"] == 22.0


def test_reliability_grade_and_score_travel_together() -> None:
    """The band letter and the percent it was graded from are one finding."""
    metrics = sx.extract(
        _sarif(_result("reliability-rating", "reliability rating A (0.0% of dev cost", False))
    )
    assert metrics["reliability-grade"] == "A"
    assert metrics["reliability-score"] == 0.0


def test_no_finding_means_omitted_never_zero() -> None:
    """SARIF cannot prove 'measured clean'; the ratio metric stays out."""
    metrics = sx.extract(_sarif())
    for absent in (
        "debt-ratio-pct",
        "complexity-density",
        "duplication-density-pct",
        "hotspot-top-risk",
        "max-fan-out",
        "reliability-grade",
        "reliability-score",
    ):
        assert absent not in metrics


def test_output_is_one_sorted_json_line() -> None:
    """The bridge's stdout is exactly a record --metrics payload."""
    import subprocess

    engine = Path(__file__).resolve().parents[1] / "scripts" / "slop_snapshot_extract.py"
    path = Path("/tmp") / f"extract-{id(object())}.sarif"
    path.write_text(
        json.dumps(_sarif(_result("debt-ratio", "debt ratio 0.1%", False))), encoding="utf-8"
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(engine), "--sarif", str(path)],
            capture_output=True,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["debt-ratio-pct"] == 0.1
    assert proc.stdout == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def test_missing_file_fails_closed_with_exit_two() -> None:
    """An unreadable feed is a configuration error, never empty metrics."""
    with pytest.raises(sx.ExtractError):
        sx.load_sarif("/nonexistent/path.sarif")


def test_non_sarif_json_fails_closed() -> None:
    """Valid JSON without a runs array is not SARIF; refuse to guess."""
    with pytest.raises(sx.ExtractError, match="no runs array"):
        sx.extract({"hello": "world"})


def test_malformed_results_field_fails_closed() -> None:
    """A run whose results is not a list is shaped impossible; exit 2."""
    with pytest.raises(sx.ExtractError):
        sx.extract({"runs": [{"results": "oops"}]})


def test_torn_results_are_skipped_not_fatal() -> None:
    """A foreign row inside results drops out quietly, like the store."""
    metrics = sx.extract(_sarif("not-a-dict", {"ruleId": "x", "message": {}, "properties": {}}))
    assert metrics["advisory-findings"] == 1.0
