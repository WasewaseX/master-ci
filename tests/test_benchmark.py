"""Tests for the performance benchmark harness.

These run at the small corpus scale and stay fast; they verify the parts
that must hold on every machine: deterministic corpus, metric schema
integrity, baseline checking logic and table rendering. Timing values
themselves are machine-dependent and are never asserted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts" / "benchmark.py")
assert _spec and _spec.loader  # static check: the module file must exist
bench = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("benchmark", bench)
_spec.loader.exec_module(bench)

EXPECTED_METRICS = {
    "detector_ms",
    "detector_files_per_sec",
    "detector_mb_per_sec",
    "detector_findings",
    "fix_plan_ms",
    "fix_plan_tools",
    "fix_generic_scan_ms",
    "fix_generic_files",
    "pathfilter_ms",
    "pathfilter_paths_per_sec",
    "pathfilter_stacks",
    "mcp_startup_ms",
    "mcp_roundtrip_ms",
}


def test_corpus_is_deterministic(tmp_path: Path) -> None:
    counts_a = bench.build_corpus(tmp_path / "a", "small")
    counts_b = bench.build_corpus(tmp_path / "b", "small")
    assert counts_a == counts_b
    assert counts_a["files"] == sum(bench.SCALES["small"])
    for path_a in sorted((tmp_path / "a").rglob("*")):
        if path_a.is_file():
            path_b = tmp_path / "b" / path_a.relative_to(tmp_path / "a")
            digest_a = hashlib.sha256(path_a.read_bytes()).hexdigest()
            digest_b = hashlib.sha256(path_b.read_bytes()).hexdigest()
            assert digest_a == digest_b, f"nondeterministic corpus file: {path_a.name}"


def test_full_suite_returns_every_contracted_metric() -> None:
    metrics = bench.run_suite(scale="small", repeat=1)
    assert set(metrics) == EXPECTED_METRICS
    timing = ("detector_ms", "fix_plan_ms", "fix_generic_scan_ms", "pathfilter_ms")
    for name, value in metrics.items():
        assert isinstance(value, float)
        if name in timing:
            assert value > 0.0, f"{name} must measure real work"
        else:
            assert value >= 0.0


def test_schema_metadata_covers_every_metric() -> None:
    assert set(bench.DIRECTIONS) == EXPECTED_METRICS
    assert set(bench.UNITS) == EXPECTED_METRICS
    timed = {n for n, d in bench.DIRECTIONS.items() if d == "down"}
    rates = {n for n, d in bench.DIRECTIONS.items() if d == "up"}
    info = {n for n, d in bench.DIRECTIONS.items() if d == "info"}
    assert timed | rates | info == EXPECTED_METRICS
    assert not (timed & rates) and not (timed & info) and not (rates & info)


def test_check_flags_slowdowns_and_ignores_info_metrics() -> None:
    current = {name: 1.0 for name in EXPECTED_METRICS}
    # identical numbers: healthy
    assert bench.check_against(current, dict(current), fail_above=3.0) == []
    # a timing metric 5x slower must be flagged
    slow = dict(current)
    slow["detector_ms"] = 5.0
    problems = bench.check_against(slow, current, fail_above=3.0)
    assert len(problems) == 1 and "detector_ms" in problems[0]
    # a rate metric 5x lower must be flagged
    low = dict(current)
    low["detector_files_per_sec"] = 0.1
    problems = bench.check_against(low, current, fail_above=3.0)
    assert len(problems) == 1 and "detector_files_per_sec" in problems[0]
    # info metrics (findings, file counts) never gate
    moved = dict(current)
    moved["detector_findings"] = 99.0
    moved["fix_generic_files"] = 99.0
    assert bench.check_against(moved, current, fail_above=3.0) == []
    # metrics absent from the baseline never gate (forward compatibility)
    assert bench.check_against(current, {}, fail_above=3.0) == []
    # zero baseline never divides by zero
    assert bench.check_against(current, {**current, "detector_ms": 0.0}, 3.0) == []


def test_render_table_lists_every_metric() -> None:
    metrics = bench.run_suite(scale="small", repeat=1)
    table = bench.render_table(metrics)
    for name in EXPECTED_METRICS:
        assert f"`{name}`" in table
    lines = table.splitlines()
    assert lines[0].startswith("| Metric |")
    assert lines[1] == "|---|---|---|"


def test_cli_json_roundtrip_and_check(tmp_path: Path) -> None:
    out = tmp_path / "bench.json"
    rc = bench.main(["--scale", "small", "--repeat", "1", "--json", str(out)])
    assert rc == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert set(saved) == EXPECTED_METRICS
    # a 1000x-faster baseline must trip the check
    fast = {name: value / 1000.0 for name, value in saved.items()}
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(fast), encoding="utf-8")
    rc = bench.main(["--scale", "small", "--repeat", "1", "--no-mcp", "--check", str(baseline)])
    assert rc == 1
