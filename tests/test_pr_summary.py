"""The PR-summary contracts (roadmap B21, round-18).

The SHA stamp is the feature: a summary must name the commit it
describes (a missing SHA is refused at build time), staleness is
answerable against any SHA, findings group by the live registry's
dimensions (never a second taxonomy), blocking findings are named with
their locations, missing inputs are data gaps - never silent zeros -
and the render is byte-deterministic for identical inputs.
"""

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))

from sem_pr_summary import (  # noqa: E402
    SUMMARY_SCHEMA_VERSION,
    build_summary,
    is_stale,
    main,
    parse_findings,
    render_markdown,
)

SHA = "8298ac44761f674503e226925b1aec6a684f654b"


def _finding(check: str, path: str, line: int) -> dict:
    return {"check": check, "path": path, "line": line, "message": "m", "weight": 1}


# --- build ---------------------------------------------------------------------


def test_missing_head_sha_is_refused():
    with pytest.raises(ValueError, match="head_sha is required"):
        build_summary([], "")


def test_whitespace_head_sha_is_refused():
    with pytest.raises(ValueError, match="head_sha is required"):
        build_summary([], "   ")


def test_blocking_and_advisory_split_comes_from_the_registry():
    summary = build_summary(
        [_finding("chatbot-residue", "a.py", 1), _finding("ai-prose", "b.py", 2)],
        SHA,
        2,
    )
    assert summary.blocking_count == 1  # chatbot-residue is registry-blocking
    assert summary.advisory_count == 1  # ai-prose is registry-advisory


def test_dimensions_come_from_the_live_category_registry():
    summary = build_summary([_finding("empty-catch", "a.py", 3)], SHA, 1)
    assert summary.dimensions["reliability"] == 1


def test_unknown_check_ids_land_in_ai_integrity_not_a_new_dimension():
    summary = build_summary([_finding("no-such-check", "a.py", 3)], SHA, 1)
    assert summary.dimensions.get("ai-integrity") == 1


def test_blocking_rows_are_sorted_and_capped():
    rows = [
        _finding("chatbot-residue", "z.py", 1),
        _finding("debugger-left", "a.py", 9),
        _finding("bare-except", "a.py", 2),
    ]
    summary = build_summary(rows, SHA, 3)
    assert [(r["path"], r["line"]) for r in summary.blocking] == [
        ("a.py", 2),
        ("a.py", 9),
        ("z.py", 1),
    ]


def test_findings_input_absent_is_a_data_gap_not_a_zero():
    summary = build_summary([], SHA, 0, findings_available=False)
    assert summary.findings_available is False
    assert "no findings input" in summary.data_gaps


def test_unknown_coverage_is_a_data_gap():
    summary = build_summary([], SHA, 0)
    assert "new-code coverage unknown" in summary.data_gaps
    known = build_summary([], SHA, 0, coverage_known=True)
    assert "new-code coverage unknown" not in known.data_gaps


def test_top_checks_sorted_by_count_then_name():
    rows = [_finding("ai-prose", "a.py", i) for i in range(3)] + [_finding("ai-vocab", "b.py", 1)]
    summary = build_summary(rows, SHA, 4)
    assert summary.top_checks == [("ai-prose", 3), ("ai-vocab", 1)]


# --- staleness -----------------------------------------------------------------


def test_staleness_matches_exact_sha_only():
    summary = build_summary([], SHA, 0)
    assert is_stale(summary, SHA) is False
    assert is_stale(summary, "e" * 40) is True


def test_staleness_ignores_surrounding_whitespace():
    summary = build_summary([], SHA, 0)
    assert is_stale(summary, f"  {SHA}  ") is False


# --- render ---------------------------------------------------------------------


def test_render_stamps_the_head_sha_and_the_schema_version():
    markdown = render_markdown(build_summary([], SHA, 7))
    assert SHA in markdown
    assert f"schema v{SUMMARY_SCHEMA_VERSION}" in markdown
    assert "7 changed files" in markdown


def test_render_names_blocking_locations():
    markdown = render_markdown(build_summary([_finding("bare-except", "x.py", 12)], SHA, 1))
    assert "| bare-except | x.py:12 |" in markdown


def test_render_carries_the_not_a_review_line():
    markdown = render_markdown(build_summary([], SHA, 0))
    assert "not a review" in markdown


def test_render_lists_data_gaps():
    markdown = render_markdown(build_summary([], SHA, 0))
    assert "- new-code coverage unknown" in markdown


def test_render_is_byte_deterministic():
    rows = [_finding("ai-prose", "a.py", 1), _finding("bare-except", "b.py", 2)]
    one = render_markdown(build_summary(rows, SHA, 2))
    two = render_markdown(build_summary([dict(r) for r in rows], SHA, 2))
    assert one == two


def test_parse_findings_accepts_bare_list_and_wrapped():
    assert parse_findings(json.dumps([_finding("ai-prose", "a.py", 1)]))
    assert parse_findings(json.dumps({"findings": [_finding("ai-prose", "a.py", 1)]}))
    assert parse_findings("{}") == []


# --- CLI ------------------------------------------------------------------------


def test_cli_renders_markdown_from_a_file(capsys, tmp_path):
    payload = tmp_path / "findings.json"
    payload.write_text(json.dumps([_finding("bare-except", "x.py", 12)]), encoding="utf-8")
    rc = main(["--findings", str(payload), "--head-sha", SHA])
    out = capsys.readouterr().out
    assert rc == 0
    assert SHA in out
    assert "bare-except" in out


def test_cli_json_output_is_the_summary_record(capsys):
    import io

    sys.stdin = io.StringIO(json.dumps([_finding("ai-prose", "a.py", 1)]))
    try:
        rc = main(["--findings", "-", "--head-sha", SHA, "--json"])
    finally:
        sys.stdin = sys.__stdin__
    record = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert record["head_sha"] == SHA
    assert record["schema_version"] == SUMMARY_SCHEMA_VERSION


def test_cli_check_stale_fresh_exits_zero(capsys):
    import io

    sys.stdin = io.StringIO("")
    try:
        rc = main(["--findings", "-", "--head-sha", SHA, "--check-stale", SHA])
    finally:
        sys.stdin = sys.__stdin__
    assert rc == 0
    assert "fresh" in capsys.readouterr().out


def test_cli_check_stale_stale_exits_one(capsys):
    import io

    sys.stdin = io.StringIO("")
    try:
        rc = main(["--findings", "-", "--head-sha", SHA, "--check-stale", "a" * 40])
    finally:
        sys.stdin = sys.__stdin__
    assert rc == 1
    assert "stale" in capsys.readouterr().out


def test_cli_without_head_sha_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["--findings", "-"])
    assert exc.value.code == 2
