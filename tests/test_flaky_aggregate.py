"""Contract tests for the per-suite hunt aggregation (v2.11.2).

The P0 audit's flaky-hunt discovery row: "a skipped test suite [must
not look] equivalent to a fully completed hunt". These tests pin the
aggregate's contract: every suite named in one table (hunted with
counts, skipped with its reason, failed-hunt loudly), the merged root
report unions the suites' flaky sets under the worst status, the exit
ladder (0 clean, 1 new flakes or failed suite, 0 with nothing claimed),
and per-suite classification is the real flaky_detect run, not a
paraphrase.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flaky_aggregate as fa  # noqa: E402


def write_junit(directory: Path, cases: dict[str, str]) -> Path:
    """One JUnit file; cases maps test name to pass/fail/skip."""
    shapes = {"passed": "", "failed": "<failure>boom</failure>", "skipped": "<skipped/>"}
    body = "".join(
        f'<testcase name="{name}" classname="pkg">{shapes[verdict]}</testcase>'
        for name, verdict in cases.items()
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "run-1.xml"
    target.write_text(f'<testsuite name="pkg">{body}</testsuite>', encoding="utf-8")
    return target


class Namespace:
    """The argparse surface flaky_aggregate.classify_suite consumes."""

    def __init__(self, tmp_path: Path, baseline: Path | None = None, fail: bool = False):
        self.iterations = 2
        self.baseline = str(baseline) if baseline else None
        self.fail_on_flaky = fail
        self.results_root = str(tmp_path)
        self.report = str(tmp_path / "flaky-report.json")
        self.summary = str(tmp_path / "flaky-summary.md")


class TestSuiteClassification:
    def test_hunted_suite_reports_counts(self, tmp_path):
        suite_dir = tmp_path / "root" / "python"
        # iteration 1: a passes; iteration 2: a fails and b appears -
        # a is flaky, b is stable, the suite is new-flakes
        write_junit(suite_dir, {"a": "passed"})
        junit2 = suite_dir / "run-2.xml"
        junit2.write_text(
            '<testsuite name="pkg">'
            '<testcase name="a" classname="pkg"><failure>boom</failure></testcase>'
            '<testcase name="b" classname="pkg"></testcase></testsuite>',
            encoding="utf-8",
        )
        suite, report = fa.classify_suite(suite_dir, Namespace(tmp_path))
        assert suite.status == "new-flakes"
        assert suite.project == "root" and suite.suite == "python"
        assert suite.tests_total == 2 and suite.flaky == 1 and suite.new == 1
        assert report is not None and report["status"] == "new-flakes"
        assert (suite_dir / "summary.md").is_file()

    def test_skipped_suite_carries_the_reason(self, tmp_path):
        suite_dir = tmp_path / "packages/web" / "node"
        suite_dir.mkdir(parents=True)
        (suite_dir / "status.txt").write_text(
            "skipped: no vitest or jest config or dependency in package.json",
            encoding="utf-8",
        )
        suite, report = fa.classify_suite(suite_dir, Namespace(tmp_path))
        assert suite.status == "skipped"
        assert report is None
        assert "no vitest or jest config" in suite.cell

    def test_failed_suite_fails_closed_with_detail(self, tmp_path):
        suite_dir = tmp_path / "lib" / "go"
        suite_dir.mkdir(parents=True)  # no run files: flaky_detect exits 2
        suite, report = fa.classify_suite(suite_dir, Namespace(tmp_path))
        assert suite.status == "failed-hunt"
        assert report is None
        assert "produced nothing" in suite.detail

    def test_clean_suite_under_baseline(self, tmp_path):
        suite_dir = tmp_path / "root" / "python"
        write_junit(suite_dir, {"a": "passed"})
        (suite_dir / "run-2.xml").write_text(
            '<testsuite name="pkg"><testcase name="a" classname="pkg"></testcase></testsuite>',
            encoding="utf-8",
        )
        suite, report = fa.classify_suite(suite_dir, Namespace(tmp_path))
        assert suite.status == "clean"
        assert suite.new == 0


class TestMerge:
    def test_merge_unions_and_takes_the_worst_status(self):
        clean = {
            "version": 1,
            "status": "clean",
            "tests_total": 4,
            "flaky": [],
            "always_failed": [],
            "quarantined": [],
            "new_flaky": [],
            "healed": [],
        }
        flaky = {
            "version": 1,
            "status": "new-flakes",
            "tests_total": 2,
            "flaky": [{"key": "pkg::a", "outcomes": ["passed", "failed"]}],
            "always_failed": ["pkg::dead"],
            "quarantined": ["pkg::known"],
            "new_flaky": ["pkg::a"],
            "healed": ["pkg::fixed"],
        }
        merged = fa.merge_report([clean, flaky], 3)
        assert merged["status"] == "new-flakes"
        assert merged["tests_total"] == 6
        assert merged["iterations"] == 3
        assert merged["flaky"] == [{"key": "pkg::a", "outcomes": ["passed", "failed"]}]
        assert merged["new_flaky"] == ["pkg::a"]
        assert merged["always_failed"] == ["pkg::dead"]
        assert merged["quarantined"] == ["pkg::known"]
        assert merged["healed"] == ["pkg::fixed"]

    def test_merge_dedupes_across_projects(self):
        def report(status, keys):
            return {
                "version": 1,
                "status": status,
                "tests_total": 1,
                "flaky": [{"key": k, "outcomes": ["passed", "failed"]} for k in keys],
                "always_failed": [],
                "quarantined": [],
                "new_flaky": list(keys),
                "healed": [],
            }

        # the same test name hunted in two projects lands once
        merged = fa.merge_report([report("new-flakes", ["pkg::a"]), report("clean", [])], 2)
        assert merged["new_flaky"] == ["pkg::a"]

    def test_worst_status_order(self):
        assert fa._worst(["clean", "quarantined-only"]) == "quarantined-only"
        assert fa._worst(["skipped", "clean"]) == "clean"
        assert fa._worst([]) == "clean"


class TestMain:
    def _write_two_suites(self, tmp_path: Path) -> None:
        # suite 1: python at root, flaky across iterations
        write_junit(tmp_path / "root" / "python", {"a": "passed"})
        (tmp_path / "root" / "python" / "run-2.xml").write_text(
            '<testsuite name="pkg">'
            '<testcase name="a" classname="pkg"><failure>boom</failure></testcase>'
            "</testsuite>",
            encoding="utf-8",
        )
        # suite 2: node in a nested project, skipped by the workflow
        skip = tmp_path / "packages-web" / "node"
        skip.mkdir(parents=True)
        (skip / "status.txt").write_text("no vitest or jest config", encoding="utf-8")

    def test_report_only_mode_exits_zero_and_names_every_suite(self, tmp_path):
        self._write_two_suites(tmp_path)
        args = Namespace(tmp_path)  # no baseline, no fail flag
        rc = fa.main(
            [
                "--results-root",
                str(tmp_path),
                "--iterations",
                "2",
                "--report",
                args.report,
                "--summary",
                args.summary,
            ]
        )
        assert rc == 0, "report-only mode: new flakes are reported, not enforced"
        document = Path(args.summary).read_text(encoding="utf-8")
        assert "`root` | python | new-flakes - 1 test(s), 1 flaky, 1 new" in document
        assert "`packages-web` | node | skipped: no vitest or jest config" in document
        assert "### root (python)" in document  # per-suite detail survives
        merged = json.loads(Path(args.report).read_text(encoding="utf-8"))
        assert merged["status"] == "new-flakes"
        assert merged["tests_total"] == 1

    def test_enforcement_mode_fails_on_new_flakes(self, tmp_path):
        self._write_two_suites(tmp_path)
        rc = fa.main(
            [
                "--results-root",
                str(tmp_path),
                "--iterations",
                "2",
                "--fail-on-flaky",
                "--report",
                str(tmp_path / "flaky-report.json"),
                "--summary",
                str(tmp_path / "flaky-summary.md"),
            ]
        )
        assert rc == 1

    def test_failed_suite_fails_the_aggregate_even_in_report_only_mode(self, tmp_path):
        # a suite that claims a runner but produced nothing: exit 2 inside
        empty = tmp_path / "lib" / "go"
        empty.mkdir(parents=True)
        rc = fa.main(
            [
                "--results-root",
                str(tmp_path),
                "--iterations",
                "2",
                "--report",
                str(tmp_path / "flaky-report.json"),
                "--summary",
                str(tmp_path / "flaky-summary.md"),
            ]
        )
        assert rc == 1, "a hunt that cannot be executed loudly is never a silent pass"

    def test_no_suites_is_a_quiet_pass(self, tmp_path):
        (tmp_path / "not-a-results-dir").mkdir()
        rc = fa.main(
            [
                "--results-root",
                str(tmp_path),
                "--iterations",
                "2",
                "--report",
                str(tmp_path / "flaky-report.json"),
                "--summary",
                str(tmp_path / "flaky-summary.md"),
            ]
        )
        assert rc == 0
        assert not Path(tmp_path / "flaky-report.json").exists()
        assert not Path(tmp_path / "flaky-summary.md").exists()


class TestWorkflowIntegration:
    """The workflow hands the aggregate the real flags and consumes its
    outputs at fixed paths."""

    def test_classify_step_wiring(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/flaky.yml").read_text(
            encoding="utf-8"
        )
        assert "flaky_aggregate.py $args" in text
        assert "--results-root flaky-results --iterations $ITERATIONS" in text
        assert "--baseline .flaky-baseline.json --fail-on-flaky" in text
        assert "hashFiles('flaky-report.json') != ''" in text, (
            "the proposal gate follows the merged report, not root manifests"
        )
        assert "cat flaky-summary.md" in text
