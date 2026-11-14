"""Contract tests for the metric snapshot store (the durable-history row).

Covers the documented contract: one appended row per scan with
deterministic bytes, metric keys validated against METRIC_FIELDS (an
unknown key is exit 2, never a quiet row), absent metrics omitted and
never zero-filled, torn and foreign-schema rows dropped quietly while
current-schema rows survive, (ts, sha, project) sort order, the trend
table's shape and first-run hint, the diff's better/worse arrows
derived from DIRECTIONS with the categorical grade never judged, and
the deliberate asymmetry: a missing store is a first-run condition for
trend (exit 0) but fail-closed for diff (exit 2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import slop_snapshots as snap  # noqa: E402


def _record(
    path: Path, ts: str, metrics: "dict[str, float | str]", **kw: object
) -> dict[str, object]:
    return snap.record(
        path,
        snap.SnapshotRow(
            sha=str(kw.get("sha", "a1b2c3d")),
            ref=str(kw.get("ref", "main")),
            project=str(kw.get("project", "alpha")),
            metrics=metrics,
            ts=ts,
        ),
    )


def test_record_bytes_are_deterministic(tmp_path: Path) -> None:
    """Why: the store is meant to be committed to a dedicated branch, so
    the same scan input must produce byte-identical rows or every append
    shows up as a spurious diff in the history branch."""
    first = tmp_path / "one.jsonl"
    second = tmp_path / "two.jsonl"
    payload = '{"debt-ratio-pct": 0.14, "complexity-density": 247}'
    for store in (first, second):
        assert (
            snap.main(
                [
                    "record",
                    "--file",
                    str(store),
                    "--sha",
                    "a1b2c3d",
                    "--ref",
                    "main",
                    "--project",
                    "alpha",
                    "--metrics",
                    payload,
                    "--ts",
                    "2026-11-15T00:00:00Z",
                ]
            )
            == 0
        )
    assert first.read_bytes() == second.read_bytes()
    line = first.read_text(encoding="utf-8").strip()
    expected = json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))
    assert line == expected


def test_record_row_shape_and_schema_stamp(tmp_path: Path) -> None:
    """Why: readers across tool migrations key on the six-field row and
    the schema stamp; a row missing any of them is not a snapshot."""
    data = _record(tmp_path / "store.jsonl", "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14})
    assert set(data) == {"ts", "sha", "ref", "project", "schema", "metrics"}
    assert data["schema"] == snap.SCHEMA_VERSION == 1
    assert data["ts"] == "2026-11-15T00:00:00Z"
    rows = snap.load_rows(tmp_path / "store.jsonl")
    assert rows[0]["ref"] == "main" and rows[0]["project"] == "alpha"


def test_unknown_metric_key_is_rejected_and_store_untouched(tmp_path: Path) -> None:
    """Why: a store that silently kept a key it cannot judge would grow
    columns no reader speaks; the row is refused before it touches the
    file, the same fail-closed move as an unknown verdict."""
    path = tmp_path / "store.jsonl"
    with pytest.raises(snap.QualitySnapshotError, match="unknown metric key"):
        _record(path, "2026-11-15T00:00:00Z", {"bloat-index": 42})
    assert not path.exists()


def test_metric_values_are_type_and_finite_checked(tmp_path: Path) -> None:
    """Why: a string or NaN masquerading as a measurement would poison
    every later delta; JSON has no NaN, so the row never gets written."""
    path = tmp_path / "store.jsonl"
    with pytest.raises(snap.QualitySnapshotError, match="takes a number"):
        _record(path, "t1", {"debt-ratio-pct": "low"})
    with pytest.raises(snap.QualitySnapshotError, match="finite"):
        _record(path, "t1", {"debt-ratio-pct": float("nan")})
    with pytest.raises(snap.QualitySnapshotError, match="takes a number"):
        _record(path, "t1", {"debt-ratio-pct": True})  # bool is an int subclass
    with pytest.raises(snap.QualitySnapshotError, match="letter grade"):
        _record(path, "t1", {"reliability-grade": 7})
    assert not path.exists()


def test_absent_metrics_are_omitted_never_zero_filled(tmp_path: Path) -> None:
    """Why: a zero would read as a measured clean scan when it means not
    measured - the engines' degrade-quietly contract, kept by the store:
    what a scan did not produce is absent, rendered "-" or n/a later."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14})
    row = snap.load_rows(path)[0]
    metrics_field = row["metrics"]
    assert isinstance(metrics_field, dict)
    assert {str(k) for k in metrics_field} == {"debt-ratio-pct"}
    body = "\n".join(snap.run_trend(path, last=10))
    assert "| 0.14 | - |" in body, "absent metrics render '-', never 0"


def test_torn_and_foreign_rows_drop_out_quietly(tmp_path: Path) -> None:
    """Why: the store outlives tool migrations; a torn write, a non-dict
    value or a row stamped with another schema version must never break
    the trend - it just drops out, the quality_history precedent."""
    path = tmp_path / "store.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "t1",
                        "sha": "s1",
                        "ref": "main",
                        "project": "alpha",
                        "schema": 1,
                        "metrics": {"debt-ratio-pct": 0.14},
                    }
                ),
                "{torn line, never parsed",
                "",
                json.dumps(["a", "list", "is", "not", "a", "row"]),
                json.dumps({"ts": "t2", "schema": 2, "metrics": {}}),  # a future schema
                json.dumps({"ts": "t3", "metrics": {}}),  # schema stamp missing
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = snap.load_rows(path)
    assert len(rows) == 1 and rows[0]["ts"] == "t1"


def test_rows_sort_by_ts_sha_project(tmp_path: Path) -> None:
    """Why: the trend window takes the most recent rows, so load order
    must be deterministic in (ts, sha, project) regardless of the order
    the appends landed in the file."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:02Z", {}, sha="b4c5d6e", project="zeta")
    _record(path, "2026-11-15T00:00:01Z", {}, sha="z9y8x7w", project="alpha")
    _record(path, "2026-11-15T00:00:01Z", {}, sha="a1b2c3d", project="alpha")
    rows = snap.load_rows(path)
    assert [(r["ts"], r["sha"], r["project"]) for r in rows] == [
        ("2026-11-15T00:00:01Z", "a1b2c3d", "alpha"),
        ("2026-11-15T00:00:01Z", "z9y8x7w", "alpha"),
        ("2026-11-15T00:00:02Z", "b4c5d6e", "zeta"),
    ]


def test_trend_renders_last_n_oldest_first(tmp_path: Path) -> None:
    """Why: the summary table is a trajectory - the window drops older
    rows and renders oldest first, one row per snapshot, metric names
    as columns."""
    path = tmp_path / "store.jsonl"
    for second, ratio in (("01", 0.14), ("02", 0.20), ("03", 0.30)):
        _record(path, f"2026-11-15T00:00:{second}Z", {"debt-ratio-pct": ratio})
    table = snap.trend_table(snap.load_rows(path), last=2)
    body = "\n".join(table)
    assert "| 2026-11-15T00:00:02Z |" in body and "| 2026-11-15T00:00:03Z |" in body
    assert "| 2026-11-15T00:00:01Z |" not in body, "the --last window drops older rows"
    assert body.index("00:00:02Z") < body.index("00:00:03Z"), "oldest of the window first"
    assert "| debt-ratio-pct |" in table[2], "metric fields are the columns"


def test_trend_renders_absent_metrics_and_grade_letters(tmp_path: Path) -> None:
    """Why: absence must stay visible as '-' and the letter grade must
    pass through untouched - a re-graded A-E banding belongs to the
    reliability engine, not to the store's renderer."""
    path = tmp_path / "store.jsonl"
    _record(
        path,
        "2026-11-15T00:00:00Z",
        {"reliability-grade": "B", "debt-ratio-pct": 0.14},
    )
    body = "\n".join(snap.run_trend(path, last=10))
    assert "| B |" in body
    assert "| 0.14 | - | - |" in body, "the metrics the scan skipped render as dashes"


def test_trend_missing_store_is_a_first_run_hint(tmp_path: Path) -> None:
    """Why: a trend is meaningful before the first row exists - the
    absence renders the enable hint instead of failing, mirroring
    quality_history's first-run wording style."""
    body = "\n".join(snap.run_trend(tmp_path / "absent.jsonl", last=10))
    assert "No snapshots yet" in body
    assert "dedicated branch" in body


def test_trend_project_filter_scopes_the_table(tmp_path: Path) -> None:
    """Why: a monorepo records one row per project per scan; a filtered
    trend must show only that project's rows, or one project's history
    hides behind another's."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14}, project="alpha")
    _record(path, "2026-11-15T00:00:01Z", {"debt-ratio-pct": 2.5}, project="beta")
    body = "\n".join(snap.run_trend(path, last=10, project="alpha"))
    assert "### Metric snapshots: alpha" in body
    assert "| alpha |" in body and "| beta |" not in body


def test_diff_arrows_both_directions(tmp_path: Path) -> None:
    """Why: the verdict is the whole point of the diff - for a
    lower-is-better metric a rising number is worse and a falling one is
    better, and the arrow must say which way the number moved."""
    worse = tmp_path / "worse.jsonl"
    _record(worse, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14})
    _record(worse, "2026-11-15T00:00:01Z", {"debt-ratio-pct": 0.30})
    body = "\n".join(snap.run_diff(worse))
    assert "| debt-ratio-pct | 0.14 | 0.3 | +0.16 | worse (^) |" in body

    better = tmp_path / "better.jsonl"
    _record(better, "2026-11-15T00:00:00Z", {"complexity-density": 260})
    _record(better, "2026-11-15T00:00:01Z", {"complexity-density": 247})
    body = "\n".join(snap.run_diff(better))
    assert "| complexity-density | 260 | 247 | -13 | better (v) |" in body


def test_higher_is_better_direction_judges_both_ways() -> None:
    """Why: every shipped metric is lower-is-better, so the
    higher-is-better branch of the judge would otherwise rot untested -
    it is part of the declared direction vocabulary and must stay
    honest for the first metric that needs it."""
    direction = snap.Direction.HIGHER_IS_BETTER
    assert snap._judge(direction, 10.0, 12.0) == "better (^)"
    assert snap._judge(direction, 12.0, 10.0) == "worse (v)"
    assert snap._judge(snap.Direction.LOWER_IS_BETTER, 10.0, 10.0) == "same"


def test_diff_absent_metric_renders_na_and_withholds_judgment(tmp_path: Path) -> None:
    """Why: a scan that did not produce a metric cannot be judged on it;
    'n/a' keeps the honest absence instead of inventing a delta from a
    zero-fill."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14})
    _record(path, "2026-11-15T00:00:01Z", {"debt-ratio-pct": 0.20, "max-fan-out": 22})
    body = "\n".join(snap.run_diff(path))
    assert "| max-fan-out | n/a | 22 | n/a | n/a |" in body
    absent_row = next(line for line in body.splitlines() if line.startswith("| max-fan-out "))
    assert "n/a" in absent_row
    assert "better" not in absent_row and "worse" not in absent_row


def test_diff_grade_row_is_rendered_but_never_judged(tmp_path: Path) -> None:
    """Why: the grade is categorical - DIRECTIONS gives it no arrow, so
    the diff shows the letters and no better/worse word for them."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:00Z", {"reliability-grade": "A"})
    _record(path, "2026-11-15T00:00:01Z", {"reliability-grade": "B"})
    body = "\n".join(snap.run_diff(path))
    assert "| reliability-grade | A | B | - | - |" in body


def test_diff_fails_closed_without_a_store_or_two_snapshots(tmp_path: Path) -> None:
    """Why: a diff needs two snapshots - absence is not a diffable
    state, so unlike the trend the diff exits 2 on a missing store and
    on a project with fewer than two rows."""
    with pytest.raises(snap.QualitySnapshotError, match="needs two snapshots"):
        snap.run_diff(tmp_path / "absent.jsonl")
    assert snap.main(["diff", "--file", str(tmp_path / "absent.jsonl")]) == 2

    lonely = tmp_path / "lonely.jsonl"
    _record(lonely, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14})
    with pytest.raises(snap.QualitySnapshotError, match="holds 1"):
        snap.run_diff(lonely)


def test_diff_rejects_mismatched_projects(tmp_path: Path) -> None:
    """Why: comparing alpha's debt against beta's debt is a category
    error the store must refuse loudly, not a table of nonsense."""
    path = tmp_path / "store.jsonl"
    _record(path, "2026-11-15T00:00:00Z", {"debt-ratio-pct": 0.14}, project="alpha")
    _record(path, "2026-11-15T00:00:01Z", {"debt-ratio-pct": 2.5}, project="beta")
    with pytest.raises(snap.QualitySnapshotError, match="same project"):
        snap.run_diff(path, from_sel="2026-11-15T00:00:00Z", to_sel="2026-11-15T00:00:01Z")
    with pytest.raises(snap.QualitySnapshotError, match="same project"):
        snap.run_diff(path)  # the unfiltered default straddles projects here


def test_diff_selectors_accept_index_and_timestamp(tmp_path: Path) -> None:
    """Why: a reviewer must be able to name the two sides - by position
    for a quick look, by timestamp for a pinned comparison - and an
    unresolvable selector fails closed instead of guessing."""
    path = tmp_path / "store.jsonl"
    for second, ratio in (("01", 0.14), ("02", 0.20), ("03", 0.30)):
        _record(path, f"2026-11-15T00:00:{second}Z", {"debt-ratio-pct": ratio})
    by_index = "\n".join(snap.run_diff(path, from_sel="1", to_sel="3"))
    assert "+0.16" in by_index, "index 1 is the oldest snapshot, 3 the newest"
    by_ts = "\n".join(snap.run_diff(path, from_sel="2026-11-15T00:00:02Z"))
    assert "+0.1" in by_ts, "an omitted --to side defaults to the latest snapshot"
    with pytest.raises(snap.QualitySnapshotError, match="out of range"):
        snap.run_diff(path, from_sel="9", to_sel="3")
    with pytest.raises(snap.QualitySnapshotError, match="no snapshot matches"):
        snap.run_diff(path, from_sel="1999-01-01T00:00:00Z", to_sel="3")


def test_cli_record_roundtrip_with_ts_override(tmp_path: Path) -> None:
    """Why: the workflow calls the CLI, not the library - the recorded
    row must round-trip through argparse, and --ts pins the timestamp so
    tests and replays stay deterministic."""
    path = tmp_path / "store.jsonl"
    code = snap.main(
        [
            "record",
            "--file",
            str(path),
            "--sha",
            "a1b2c3d",
            "--ref",
            "main",
            "--project",
            "alpha",
            "--metrics",
            '{"debt-ratio-pct": 0.14, "reliability-grade": "A"}',
            "--ts",
            "2026-11-15T00:00:00Z",
        ]
    )
    assert code == 0
    assert snap.main(["trend", "--file", str(path), "--last", "5"]) == 0
    assert snap.main(["diff", "--file", str(path), "--project", "alpha"]) == 2, (
        "one snapshot is not yet a diffable state"
    )
    row = snap.load_rows(path)[0]
    assert row["ts"] == "2026-11-15T00:00:00Z"
    assert row["metrics"] == {"debt-ratio-pct": 0.14, "reliability-grade": "A"}


def test_cli_unknown_metric_exits_two(tmp_path: Path) -> None:
    """Why: the fail-closed mapping lives in main - an unknown key must
    reach the caller as exit 2 with the reason on stderr, never as a
    quiet row or a traceback."""
    code = snap.main(
        [
            "record",
            "--file",
            str(tmp_path / "store.jsonl"),
            "--sha",
            "a1b2c3d",
            "--project",
            "alpha",
            "--metrics",
            '{"no-such-metric": 1}',
        ]
    )
    assert code == 2
    assert not (tmp_path / "store.jsonl").exists()
