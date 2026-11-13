"""Contract tests for the flake hunt's classification and gates.

Covers the documented contract: per-test classification across
iterations (flaky, always-failed, absent), fail-closed loading (missing
or gapped run files, unreadable result files, conflicting verdicts in
one file, zero tests), the quarantine baseline (grandfathered flakes,
new flakes gated by --fail-on-flaky, healed flakes celebrated,
deterministic baseline bytes), and the exit-code ladder 0/1/2.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flaky_detect as fd  # noqa: E402


def write_junit(path: Path, cases: dict[str, str]) -> Path:
    """One JUnit file; cases maps test name to pass/fail/skip."""
    shapes = {"passed": "", "failed": "<failure>boom</failure>", "skipped": "<skipped/>"}
    body = "".join(
        f'<testcase name="{name}" classname="pkg">{shapes[verdict]}</testcase>'
        for name, verdict in cases.items()
    )
    path.write_text(f'<testsuite name="pkg">{body}</testsuite>', encoding="utf-8")
    return path


def write_runs(tmp_path: Path, iterations: dict[int, dict[str, str]]) -> Path:
    results = tmp_path / "flaky-results"
    results.mkdir(exist_ok=True)
    for number, cases in iterations.items():
        write_junit(results / f"run-{number}.xml", cases)
    return results


class TestLoadRuns:
    def test_missing_directory_fails_closed(self, tmp_path):
        with pytest.raises(fd.HuntError, match="does not exist"):
            fd.load_runs(tmp_path / "nowhere", None)

    def test_no_result_files_fails_closed(self, tmp_path):
        results = tmp_path / "flaky-results"
        results.mkdir()
        with pytest.raises(fd.HuntError, match="produced nothing"):
            fd.load_runs(results, None)

    def test_non_contiguous_runs_fail_closed(self, tmp_path):
        write_runs(tmp_path, {1: {"a": "passed"}, 3: {"a": "passed"}})
        with pytest.raises(fd.HuntError, match="missing iteration"):
            fd.load_runs(tmp_path / "flaky-results", None)

    def test_iterations_mismatch_fails_closed(self, tmp_path):
        write_runs(tmp_path, {1: {"a": "passed"}, 2: {"a": "passed"}})
        with pytest.raises(fd.HuntError, match="cover 2 run"):
            fd.load_runs(tmp_path / "flaky-results", 3)

    def test_unreadable_result_file_fails_closed_with_filename(self, tmp_path):
        results = write_runs(tmp_path, {1: {"a": "passed"}, 2: {"a": "passed"}})
        (results / "run-2.xml").write_text("<testsuite><testcase>", encoding="utf-8")
        with pytest.raises(fd.HuntError, match="run-2.xml"):
            fd.load_runs(results, None)

    def test_conflicting_verdicts_in_one_run_fail_closed(self, tmp_path):
        results = tmp_path / "flaky-results"
        results.mkdir()
        xml = (
            '<testsuite name="pkg">'
            '<testcase name="a" classname="pkg"/>'
            '<testcase name="a" classname="pkg"><failure>boom</failure></testcase>'
            "</testsuite>"
        )
        (results / "run-1.xml").write_text(xml, encoding="utf-8")
        (results / "run-2.xml").write_text(xml, encoding="utf-8")
        with pytest.raises(fd.HuntError, match="both"):
            fd.load_runs(results, None)

    def test_unrelated_files_are_ignored(self, tmp_path):
        results = write_runs(tmp_path, {1: {"a": "passed"}, 2: {"a": "passed"}})
        (results / "README.md").write_text("noise", encoding="utf-8")
        runs, top = fd.load_runs(results, None)
        assert top == 2 and all(len(items) == 1 for items in runs.values())


class TestClassify:
    def test_flaky_needs_both_verdicts(self, tmp_path):
        runs, _ = fd.load_runs(
            write_runs(
                tmp_path,
                {1: {"a": "passed"}, 2: {"a": "failed"}, 3: {"a": "passed"}},
            ),
            None,
        )
        verdicts = fd.classify(runs)
        assert verdicts == [fd.TestVerdict("pkg::a", ("passed", "failed", "passed"))]
        assert verdicts[0].is_flaky and not verdicts[0].always_fails

    def test_always_failed_and_absent_are_distinct(self, tmp_path):
        runs, _ = fd.load_runs(
            write_runs(
                tmp_path,
                {1: {"a": "failed", "b": "passed"}, 2: {"a": "failed"}},
            ),
            None,
        )
        verdicts = fd.classify(runs)
        by_key = {v.key: v for v in verdicts}
        assert by_key["pkg::a"].always_fails and not by_key["pkg::a"].is_flaky
        assert by_key["pkg::b"].runs == ("passed", "absent") and not by_key["pkg::b"].is_flaky

    def test_skipped_is_neither_pass_nor_fail(self, tmp_path):
        runs, _ = fd.load_runs(
            write_runs(tmp_path, {1: {"a": "skipped"}, 2: {"a": "skipped"}}), None
        )
        verdicts = fd.classify(runs)
        assert not verdicts[0].is_flaky and not verdicts[0].always_fails


class TestBaseline:
    def test_load_rejects_wrong_version(self, tmp_path):
        path = tmp_path / "base.json"
        path.write_text('{"version": 99, "tests": {}}', encoding="utf-8")
        with pytest.raises(fd.HuntError, match="version"):
            fd.load_baseline(path)

    def test_load_rejects_malformed_entries(self, tmp_path):
        path = tmp_path / "base.json"
        path.write_text('{"version": 1, "tests": {"t": "now"}}', encoding="utf-8")
        with pytest.raises(fd.HuntError, match="first_seen"):
            fd.load_baseline(path)

    def test_load_rejects_invalid_json_and_missing_file(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{", encoding="utf-8")
        with pytest.raises(fd.HuntError, match="not valid JSON"):
            fd.load_baseline(bad)
        with pytest.raises(fd.HuntError, match="does not exist"):
            fd.load_baseline(tmp_path / "gone.json")

    def test_write_baseline_is_deterministic_and_hygiene_clean(self, tmp_path):
        path = tmp_path / "base.json"
        fd.write_baseline(path, ["pkg::b", "pkg::a"])
        first = path.read_bytes()
        fd.write_baseline(path, ["pkg::a", "pkg::b"])
        assert path.read_bytes() == first
        text = first.decode("utf-8")
        assert text.endswith("\n")
        assert '"pkg::a"' in text and '  "tests"' in text
        assert json.loads(text)["version"] == 1

    def test_compare_splits_quarantine_new_and_healed(self, tmp_path):
        verdicts = [
            fd.TestVerdict("pkg::known", ("passed", "failed")),
            fd.TestVerdict("pkg::fresh", ("failed", "passed")),
            fd.TestVerdict("pkg::fixed", ("passed", "passed")),
            fd.TestVerdict("pkg::stable", ("passed", "passed")),
        ]
        baseline = {
            "pkg::known": {"first_seen": "2026-01-01"},
            "pkg::fixed": {"first_seen": "2026-01-01"},
        }
        delta = fd.compare(verdicts, baseline)
        assert delta.quarantined == ("pkg::known",)
        assert delta.new == ("pkg::fresh",)
        assert delta.healed == ("pkg::fixed",)


class TestMain:
    def _hunt(self, tmp_path, iterations, *extra):
        results = write_runs(tmp_path, iterations)
        report = tmp_path / "report.json"
        code = fd.main(["--results-dir", str(results), "--report", str(report), *extra])
        return code, report

    def test_quarantined_flakes_exit_zero(self, tmp_path):
        baseline = tmp_path / "base.json"
        fd.write_baseline(baseline, ["pkg::a"])
        code, report = self._hunt(
            tmp_path,
            {1: {"a": "passed"}, 2: {"a": "failed"}},
            "--baseline",
            str(baseline),
        )
        assert code == 0
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["flaky"] == [{"key": "pkg::a", "outcomes": ["passed", "failed"]}]
        assert payload["new_flaky"] == [] and payload["quarantined"] == ["pkg::a"]

    def test_new_flake_gates_only_under_fail_on_flaky(self, tmp_path):
        runs = {1: {"a": "passed"}, 2: {"a": "failed"}}
        code, _ = self._hunt(tmp_path, runs)
        assert code == 0
        baseline = tmp_path / "base.json"
        fd.write_baseline(baseline, [])
        code, _ = self._hunt(tmp_path, runs, "--fail-on-flaky", "--baseline", str(baseline))
        assert code == 1

    def test_write_baseline_starts_a_hunt_without_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, _ = self._hunt(tmp_path, {1: {"a": "passed"}, 2: {"a": "failed"}}, "--write-baseline")
        assert code == 0
        recorded = json.loads(Path(fd.DEFAULT_BASELINE).read_text(encoding="utf-8"))
        assert set(recorded["tests"]) == {"pkg::a"}
        assert recorded["version"] == 1

    def test_fail_closed_paths_exit_two(self, tmp_path):
        baseline = tmp_path / "base.json"
        baseline.write_text("{}", encoding="utf-8")
        code, _ = self._hunt(tmp_path, {1: {"a": "passed"}}, "--baseline", str(baseline))
        assert code == 2

    def test_zero_tests_fails_closed(self, tmp_path):
        results = tmp_path / "flaky-results"
        results.mkdir()
        empty = '<testsuite name="pkg"/>'
        for i in (1, 2):
            (results / f"run-{i}.xml").write_text(empty, encoding="utf-8")
        code = fd.main(["--results-dir", str(results), "--report", str(tmp_path / "r.json")])
        assert code == 2

    def test_cli_exit_codes_reach_the_shell(self, tmp_path):
        import subprocess

        results = write_runs(tmp_path, {1: {"a": "passed"}, 2: {"a": "failed"}})
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "flaky_detect.py"),
                "--results-dir",
                str(results),
                "--fail-on-flaky",
                "--report",
                str(tmp_path / "r.json"),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1 and "new flaky test" in proc.stderr


class TestReporting:
    def test_render_marks_new_and_lists_healed(self):
        verdicts = [
            fd.TestVerdict("pkg::fresh", ("passed", "failed")),
            fd.TestVerdict("pkg::known", ("failed", "passed")),
            fd.TestVerdict("pkg::always", ("failed", "failed")),
        ]
        delta = fd.BaselineDelta(
            quarantined=("pkg::known",), new=("pkg::fresh",), healed=("pkg::gone",)
        )
        lines = fd.render(verdicts, delta, 2)
        text = "\n".join(lines)
        assert "NEW" in text and "quarantined" in text
        assert "`pkg::gone`" in text
        assert "Always failing" in text

    def test_clean_hunt_has_no_table(self):
        lines = fd.render([], fd.BaselineDelta((), (), ()), 3)
        assert "No test changed its verdict" in "\n".join(lines)

    def test_report_carries_the_machine_facts(self):
        verdicts = [fd.TestVerdict("pkg::a", ("passed", "failed"))]
        report = fd.build_report(verdicts, fd.BaselineDelta(("pkg::a",), (), ()), 2)
        assert report["iterations"] == 2
        assert report["tests_total"] == 1
        assert report["quarantined"] == ["pkg::a"]
        assert report["new_flaky"] == [] and report["healed"] == []
