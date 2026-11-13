"""Contract tests for the quality history (the ci-success trend table).

Covers the documented contract: one appended row per run with
deterministic bytes, verdicts are validated (an unknown verdict is exit
2, never a quiet row), the trend table renders the last N runs oldest
first with failures marked, malformed cached rows drop out instead of
breaking the table, a missing file is a first-run condition that renders
the enable hint (exit 0, never an error), and an unwritable history is
fail-closed (exit 2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import quality_history as qh  # noqa: E402


def _record(path: Path, run_id: int, verdict: str = "passed", **kw) -> dict:
    return qh.record(
        path,
        qh.HistoryRow(
            run_id=run_id,
            ref=kw.get("ref", "main"),
            sha=kw.get("sha", f"sha{run_id}"),
            verdict=verdict,
            coverage_min=kw.get("coverage_min", 80.0),
            failed_jobs=kw.get("failed_jobs", []),
            ts=kw.get("ts", f"2026-11-10T00:00:{run_id:02d}Z"),
        ),
    )


def test_record_appends_one_deterministic_row(tmp_path):
    path = tmp_path / "history.jsonl"
    row = _record(path, 41)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == row
    assert lines[0] == json.dumps(row, sort_keys=True, separators=(",", ":"))


def test_record_accumulates_in_run_order(tmp_path):
    path = tmp_path / "history.jsonl"
    _record(path, 41)
    _record(path, 42, verdict="failed", failed_jobs=["rust", "java"])
    rows = qh.load_rows(path)
    assert [r["run_id"] for r in rows] == [41, 42]
    assert rows[1]["failed_jobs"] == ["java", "rust"]  # sorted, not input order


def test_unknown_verdict_is_rejected_not_recorded(tmp_path):
    path = tmp_path / "history.jsonl"
    with pytest.raises(qh.QualityHistoryError, match="verdict"):
        _record(path, 41, verdict="green")
    assert not path.exists()


def test_unwritable_history_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory should be", encoding="utf-8")
    path = blocker / "history.jsonl"
    with pytest.raises(qh.QualityHistoryError, match="cannot append"):
        _record(path, 41)


def test_trend_renders_the_last_n_runs_oldest_first(tmp_path):
    path = tmp_path / "history.jsonl"
    _record(path, 41)
    _record(path, 42, verdict="failed", failed_jobs=["rust"])
    _record(path, 43)
    table = qh.run_trend(path, last=2)
    body = "\n".join(table)
    assert "| 42 |" in body and "| 43 |" in body
    assert "| 41 |" not in body, "the --last window drops older rows"
    assert "**failed**" in body, "a failed run is marked in the verdict cell"
    assert body.index("42") < body.index("43"), "oldest of the window first"


def test_trend_passing_row_has_no_failure_mark(tmp_path):
    path = tmp_path / "history.jsonl"
    _record(path, 41)
    body = "\n".join(qh.run_trend(path, last=10))
    assert "| passed |" in body
    assert "**" not in body


def test_malformed_cached_rows_drop_out_quietly(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"run_id": 41, "verdict": "passed", "ts": "t1"}),
                "{not json at all",
                "",
                json.dumps(["a", "list", "not", "a", "dict"]),
                json.dumps({"run_id": 42, "verdict": "failed", "ts": "t2"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = qh.load_rows(path)
    assert [r["run_id"] for r in rows] == [41, 42]
    assert qh.run_trend(path, last=10)  # the table still renders


def test_missing_history_is_a_first_run_not_an_error(tmp_path):
    body = "\n".join(qh.run_trend(tmp_path / "absent.jsonl", last=10))
    assert "No history yet" in body


def test_cli_roundtrip(tmp_path):
    path = tmp_path / "history.jsonl"
    assert (
        qh.main(
            [
                "record",
                "--file",
                str(path),
                "--run-id",
                "41",
                "--ref",
                "main",
                "--sha",
                "abc",
                "--verdict",
                "passed",
                "--coverage-min",
                "80",
                "--ts",
                "2026-11-10T00:00:00Z",
            ]
        )
        == 0
    )
    assert qh.main(["trend", "--file", str(path), "--last", "5"]) == 0


def test_cli_bad_verdict_exits_two(tmp_path):
    # argparse's own choices enforcement is the first line of defense;
    # either way the caller sees exit 2, never a quiet row
    with pytest.raises(SystemExit) as exc:
        qh.main(
            [
                "record",
                "--file",
                str(tmp_path / "h.jsonl"),
                "--run-id",
                "1",
                "--verdict",
                "nope",
            ]
        )
    assert exc.value.code == 2


def test_cli_unreadable_history_exits_two(tmp_path):
    blocked = tmp_path / "h.jsonl"
    blocked.write_text("x", encoding="utf-8")
    blocked.chmod(0o000)
    try:
        code = qh.main(["trend", "--file", str(blocked), "--last", "5"])
    finally:
        blocked.chmod(0o644)
    assert code == 2
