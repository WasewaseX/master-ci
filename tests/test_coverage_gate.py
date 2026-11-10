"""Contract tests for the coverage gates (roadmap item 4).

Covers the documented contract: the three report parsers (coverage.json, Cobertura XML, Go coverprofile), fail-closed semantics (a requested gate that cannot be evaluated is exit 2, never a silent pass), the overall floor gating every report, the new-code floor gating the merge-base diff's added lines, and low-new-coverage findings landing advisory in the test-assurance dimension.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import coverage_gate as cg  # noqa: E402
import slop_cli  # noqa: E402
from slop_categories import category_of  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mod.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "mod.py").write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    return tmp_path


def _python_report(path: Path, percent: float = 40.0) -> Path:
    path.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": percent},
                "files": {
                    "mod.py": {
                        "summary": {"percent_covered": percent},
                        "executed_lines": [1, 2],
                        "missing_lines": [4, 5],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


COBERTURA = (
    '<coverage line-rate="0.5">'
    '<classes><class filename="mod.py">'
    '<lines><line number="1" hits="1"/><line number="4" hits="0"/></lines>'
    "</class></classes></coverage>"
)

GO_COVER = "mode: set\nmod.go:1.2,2.3 2 1\nmod.go:4.2,5.3 2 0\n"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_python_json_parser_totals_and_lines(tmp_path):
    report = cg.parse_report(_python_report(tmp_path / "coverage.json", 87.5))
    assert report.source == "python-json"
    assert report.total_percent == 87.5
    entry = report.files["mod.py"]
    assert entry.covered == {1, 2}
    assert entry.missed == {4, 5}


def test_cobertura_parser_line_rate_and_hits(tmp_path):
    path = tmp_path / "coverage.xml"
    path.write_text(COBERTURA, encoding="utf-8")
    report = cg.parse_report(path)
    assert report.source == "cobertura"
    assert report.total_percent == 50.0
    entry = report.files["mod.py"]
    assert entry.covered == {1}
    assert entry.missed == {4}


def test_go_cover_parser_statement_share_and_spans(tmp_path):
    path = tmp_path / "cover.out"
    path.write_text(GO_COVER, encoding="utf-8")
    report = cg.parse_report(path)
    assert report.source == "go-cover"
    assert report.total_percent == 50.0
    entry = report.files["mod.go"]
    assert entry.covered == {1, 2}
    assert entry.missed == {4, 5}
    assert entry.percent == 50.0


def test_parse_report_dispatches_on_content_not_name(tmp_path):
    weird = tmp_path / "anything.txt"
    weird.write_text(COBERTURA, encoding="utf-8")
    assert cg.parse_report(weird).source == "cobertura"


@pytest.mark.parametrize(
    "body",
    ["not json at all", '{"totals": {}}'],
    ids=["garbage", "no-files"],
)
def test_corrupt_python_report_raises(tmp_path, body):
    path = tmp_path / "coverage.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(cg.CoverageGateError):
        cg.parse_report(path)


def test_corrupt_cobertura_raises(tmp_path):
    path = tmp_path / "coverage.xml"
    path.write_text("<coverage><unclosed>", encoding="utf-8")
    with pytest.raises(cg.CoverageGateError):
        cg.parse_report(path)


def test_empty_go_profile_raises(tmp_path):
    path = tmp_path / "cover.out"
    path.write_text("mode: set\n", encoding="utf-8")
    with pytest.raises(cg.CoverageGateError):
        cg.parse_report(path)


# ---------------------------------------------------------------------------
# Discovery and lookup
# ---------------------------------------------------------------------------


def test_discover_finds_standard_names(tmp_path):
    _python_report(tmp_path / "coverage.json")
    (tmp_path / "cover.out").write_text(GO_COVER, encoding="utf-8")
    reports = cg.discover_reports(tmp_path, [])
    assert [r.source for r in reports] == ["python-json", "go-cover"]


def test_discover_prefers_explicit_paths(tmp_path):
    _python_report(tmp_path / "custom.json")
    reports = cg.discover_reports(tmp_path, [str(tmp_path / "custom.json")])
    assert len(reports) == 1
    assert reports[0].path.endswith("custom.json")


def test_entry_for_suffix_matches_go_module_paths(tmp_path):
    report = cg.CoverageReport(
        "go-cover",
        "cover.out",
        50.0,
        {"example.com/repo/mod.go": cg.FileCoverage("example.com/repo/mod.go", 50.0, {1}, set())},
    )
    assert report.entry_for("mod.go") is not None
    assert report.entry_for("other.go") is None


# ---------------------------------------------------------------------------
# Diff parsing and the new-code view
# ---------------------------------------------------------------------------


def test_hunk_added_lines_walk_context_and_skips():
    rows = ["+a", "+b", "-gone", " ctx", "\\ No newline at end of file", "+c"]
    assert cg._hunk_added_lines(rows, 10, 3) == {10, 11, 13}


def test_git_changed_lines_reads_real_diff(git_repo):
    # base file is 2 lines; the head commit appends 8 lines, so git's
    # --unified=0 hunk is @@ -2,0 +3,8 @@ -> added lines 3..10
    changed = cg.git_changed_lines(git_repo, "HEAD~1")
    assert changed == {"mod.py": {3, 4, 5, 6, 7, 8, 9, 10}}


def test_new_code_view_counts_intersection_only():
    report = cg.CoverageReport(
        "python-json",
        "coverage.json",
        50.0,
        {"mod.py": cg.FileCoverage("mod.py", 50.0, {4, 5}, set())},
    )
    view = cg.new_code_view([report], {"mod.py": {4, 5, 6}})
    assert view.changed_total == 3
    assert view.covered_total == 2
    assert view.per_file["mod.py"][0] == 2
    assert view.per_file["mod.py"][2] == 4  # first added line


def test_new_code_view_file_absent_from_report_counts_uncovered():
    report = cg.CoverageReport("cobertura", "coverage.xml", 0.0, {})
    view = cg.new_code_view([report], {"new.py": {1, 2, 3}})
    assert view.covered_total == 0
    assert view.total_percent == 0.0


def test_new_code_view_skips_non_coverage_subjects():
    """Test files and non-code files are not coverage subjects for any
    mainstream tool; they stay out of the denominator. Code files with
    no entry still count as uncovered - untested code cannot hide."""
    report = cg.CoverageReport("cobertura", "coverage.xml", 0.0, {})
    view = cg.new_code_view(
        [report],
        {
            "src/mod.py": {1, 2, 3},
            "src/mod.test.js": {1, 2},
            "tests/test_mod.py": {1, 2},
            "pkg/lib_test.go": {1, 2},
            "package.json": {1, 2, 3, 4},
            "ci.yml": {1},
        },
    )
    assert set(view.per_file) == {"src/mod.py"}
    assert view.changed_total == 3
    assert view.total_percent == 0.0


# ---------------------------------------------------------------------------
# Gate orchestration
# ---------------------------------------------------------------------------


def test_no_gate_requested_is_noop(tmp_path):
    result = cg.run_gate(
        tmp_path, min_coverage=0, min_new_coverage=0, diff_base="", report_paths=[]
    )
    assert result.exit_code == 0
    assert result.sections == []
    assert result.findings == []


def test_gate_without_report_fails_closed(tmp_path):
    with pytest.raises(cg.CoverageGateError, match="no report found"):
        cg.run_gate(tmp_path, min_coverage=30, min_new_coverage=0, diff_base="", report_paths=[])


def test_new_code_gate_without_base_fails_closed(tmp_path):
    _python_report(tmp_path / "coverage.json")
    with pytest.raises(cg.CoverageGateError, match="diff-base"):
        cg.run_gate(tmp_path, min_coverage=0, min_new_coverage=30, diff_base="", report_paths=[])


def test_overall_floor_pass_and_fail(tmp_path):
    _python_report(tmp_path / "coverage.json", 40.0)
    ok = cg.run_gate(tmp_path, min_coverage=40, min_new_coverage=0, diff_base="", report_paths=[])
    assert ok.exit_code == 0
    bad = cg.run_gate(tmp_path, min_coverage=41, min_new_coverage=0, diff_base="", report_paths=[])
    assert bad.exit_code == 1
    assert any("below floor" in line for line in bad.sections)


def test_every_report_must_meet_the_floor(tmp_path):
    _python_report(tmp_path / "coverage.json", 90.0)
    (tmp_path / "cover.out").write_text(GO_COVER, encoding="utf-8")
    result = cg.run_gate(
        tmp_path, min_coverage=80, min_new_coverage=0, diff_base="", report_paths=[]
    )
    assert result.exit_code == 1


def test_new_code_gate_findings_and_exit(git_repo):
    _python_report(git_repo / "coverage.json", 100.0)
    result = cg.run_gate(
        git_repo,
        min_coverage=0,
        min_new_coverage=60,
        diff_base="HEAD~1",
        report_paths=[],
    )
    assert result.exit_code == 1
    path, line, message = result.findings[0]
    assert path == "mod.py"
    assert line == 3
    assert "0%" in message and "60% floor" in message and "8 added" in message


def test_new_code_gate_passes_when_floor_met(git_repo):
    # added lines 3..10; 4..9 are covered -> 75% against a 60% floor
    report_path = git_repo / "coverage.json"
    report_path.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 100.0},
                "files": {
                    "mod.py": {
                        "summary": {"percent_covered": 100.0},
                        "executed_lines": [1, 2, 4, 5, 6, 7, 8, 9],
                        "missing_lines": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result = cg.run_gate(
        git_repo, min_coverage=0, min_new_coverage=60, diff_base="HEAD~1", report_paths=[]
    )
    assert result.exit_code == 0
    assert result.findings == []


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _run_cli(root: Path, *args: str) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        try:
            code = slop_cli.main([str(root), "--max-score", "100", *args])
        except SystemExit as exc:  # fail-closed gate config errors exit via SystemExit
            code = int(exc.code or 0)
    return code, buffer.getvalue()


def test_cli_exit_two_when_gate_requested_without_report(tmp_path):
    code, _ = _run_cli(tmp_path, "--min-coverage", "30")
    assert code == 2


def test_cli_low_new_coverage_is_advisory_and_categorized(git_repo):
    _python_report(git_repo / "coverage.json", 100.0)
    code, out = _run_cli(git_repo, "--min-new-coverage", "60", "--diff-base", "HEAD~1")
    assert code == 1
    assert "test-assurance=A(0)" not in out
    assert "test-assurance=A(" in out


def test_cli_report_carries_coverage_section(git_repo):
    _python_report(git_repo / "coverage.json", 100.0)
    out_path = git_repo / "r.md"
    _run_cli(git_repo, "--min-coverage", "50", "--report-file", str(out_path))
    body = out_path.read_text(encoding="utf-8")
    assert "### Test assurance: measured coverage" in body
    assert "python-json" in body


def test_cli_without_gates_unchanged(tmp_path):
    code, out = _run_cli(tmp_path)
    assert code == 0
    assert "Test assurance: measured" not in out


def test_low_new_coverage_check_is_advisory_test_assurance():
    from slop_model import Finding

    finding = Finding(path="mod.py", line=4, check="low-new-coverage", message="m", weight=1)
    assert finding.advisory
    assert category_of("low-new-coverage") == "test-assurance"


# ---------------------------------------------------------------------------
# JaCoCo - the JVM dialect rides the same gate (cargo-llvm-cov cobertura,
# Go coverprofile, Python json, JaCoCo report XML: four shapes, one floor)
# ---------------------------------------------------------------------------

JACOCO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="demo">
    <sessioninfo id="x" start="1" dump="2"/>
    <package name="com/example/app">
        <class name="com/example/app/Foo" sourcefilename="Foo.java">
            <counter type="INSTRUCTION" missed="0" covered="10"/>
        </class>
        <sourcefile name="Foo.java">
            <line nr="1" mi="0" ci="5" mb="0" cb="0"/>
            <line nr="2" mi="4" ci="0" mb="0" cb="0"/>
            <line nr="3" mi="0" ci="7" mb="0" cb="0"/>
        </sourcefile>
        <sourcefile name="Bar.kt">
            <line nr="1" mi="0" ci="9" mb="0" cb="0"/>
        </sourcefile>
    </package>
    <counter type="INSTRUCTION" missed="4" covered="16"/>
    <counter type="LINE" missed="1" covered="3"/>
    <counter type="CLASS" missed="0" covered="2"/>
</report>
"""


def _write_jacoco(tmp_path: Path, text: str = JACOCO_XML) -> Path:
    report = tmp_path / "jacoco.xml"
    report.write_text(text, encoding="utf-8")
    return report


def test_jacoco_report_parses_line_sets_and_totals(tmp_path):
    parsed = cg.parse_report(_write_jacoco(tmp_path))
    assert parsed.source == "jacoco"
    # the report-level LINE counter is the headline: 3 covered of 4
    assert parsed.total_percent == pytest.approx(75.0)
    foo = parsed.files["com/example/app/Foo.java"]
    assert foo.covered == {1, 3}
    assert foo.missed == {2}
    # dotted package folds to the diff's own path shape
    assert "com/example/app/Bar.kt" in parsed.files


def test_jacoco_and_cobertura_dispatch_on_root_tag(tmp_path):
    cobertura = tmp_path / "coverage.xml"
    cobertura.write_text(
        '<?xml version="1.0"?><coverage line-rate="0.5">'
        '<class filename="mod.py"><line number="1" hits="1"/></class>'
        "</coverage>",
        encoding="utf-8",
    )
    assert cg.parse_report(cobertura).source == "cobertura"
    assert cg.parse_report(_write_jacoco(tmp_path)).source == "jacoco"


def test_jacoco_line_counter_wins_over_instruction(tmp_path):
    # LINE says 50%, INSTRUCTION says 25% - the gate promises line
    # semantics, so the LINE counter is the headline number.
    text = JACOCO_XML.replace(
        '<counter type="LINE" missed="1" covered="3"/>',
        '<counter type="LINE" missed="2" covered="2"/>',
    )
    parsed = cg.parse_report(_write_jacoco(tmp_path, text))
    assert parsed.total_percent == pytest.approx(50.0)


def test_jacoco_instruction_counter_is_the_fallback(tmp_path):
    text = JACOCO_XML.replace('  <counter type="LINE" missed="1" covered="3"/>\n', "")
    parsed = cg.parse_report(_write_jacoco(tmp_path, text))
    assert parsed.source == "jacoco"
    assert parsed.total_percent == pytest.approx(100.0 * 16 / 20)


def test_jacoco_without_any_counter_fails_closed(tmp_path):
    text = JACOCO_XML.replace('  <counter type="LINE" missed="1" covered="3"/>\n', "")
    text = text.replace('<counter type="INSTRUCTION" missed="4" covered="16"/>', "")
    with pytest.raises(cg.CoverageGateError, match="counter"):
        cg.parse_report(_write_jacoco(tmp_path, text))


def test_jacoco_with_no_sourcefiles_fails_closed(tmp_path):
    text = JACOCO_XML.replace("<package", "<x:package").replace("</package>", "</x:package>")
    with pytest.raises(cg.CoverageGateError, match="sourcefile"):
        cg.parse_report(_write_jacoco(tmp_path, text))


def test_jacoco_slice_joins_the_overall_floor_gate(tmp_path):
    # a full stack's worth of reports: one dialect below the floor fails
    # the whole gate no matter which tool produced it
    _python_report(tmp_path / "coverage.json", 100.0)
    cg.parse_report(_write_jacoco(tmp_path))
    reports = cg.discover_reports(tmp_path, [tmp_path / "coverage.json", tmp_path / "jacoco.xml"])
    assert [r.source for r in reports] == ["python-json", "jacoco"]
    lines, failing = cg._overall_section(reports, 70.0)
    assert failing == 0
    lines, failing = cg._overall_section(reports, 80.0)
    assert failing == 1  # the jacoco slice sits at 75%
    assert any("jacoco" in line and "below floor" in line for line in lines)


def test_jacoco_new_code_intersection_counts_uncovered_added_lines(tmp_path):
    report = cg.parse_report(_write_jacoco(tmp_path))
    changed = {"com/example/app/Foo.java": {2}}
    view = cg.new_code_view([report], changed)
    assert view.changed_total == 1
    assert view.covered_total == 0  # line 2 is missed: added-but-unexercised
