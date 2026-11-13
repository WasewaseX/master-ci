"""Contract tests for the flake hunt's quarantine loop.

Covers the documented contract of ``flaky_quarantine.py``: proposals are
append-only merges (existing ``first_seen`` preserved verbatim, healed
flakes never removed, new keys stamped today), report-only repos are
refused without artifacts, inconsistent evidence fails closed, the bytes
are deterministic, and ``verify`` independently rejects removal,
fabrication, rewritten history and undated entries - every rejection on
exit 2, never a silent pass.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flaky_detect as fd  # noqa: E402
import flaky_quarantine as fq  # noqa: E402

TODAY = datetime.now(timezone.utc).date().isoformat()
BASELINE = {"version": 1, "tests": {"pkg::known": {"first_seen": "2026-01-01"}}}


def write_report(
    path: Path,
    new: list[str],
    flaky: list[dict] | None = None,
    version: int = 1,
) -> Path:
    payload = {
        "version": version,
        "status": "new-flakes" if new else "quarantined-only",
        "iterations": 3,
        "tests_total": 10,
        "flaky": flaky
        if flaky is not None
        else [{"key": key, "outcomes": ["passed", "failed", "passed"]} for key in new],
        "new_flaky": new,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_baseline(path: Path, tests: dict[str, dict] | None = None) -> Path:
    payload = dict(BASELINE)
    if tests is not None:
        payload["tests"] = tests
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def propose(tmp_path: Path, new: list[str], baseline: bool = True, **kwargs) -> tuple[Path, int]:
    report = write_report(tmp_path / "flaky-report.json", new, **kwargs)
    if baseline:
        write_baseline(tmp_path / ".flaky-baseline.json")
    out = tmp_path / "out"
    rc = fq.main(
        [
            "propose",
            "--report",
            str(report),
            "--baseline",
            str(tmp_path / ".flaky-baseline.json"),
            "--out",
            str(out),
        ]
    )
    return out, rc


class TestPropose:
    def test_no_new_flakes_writes_nothing(self, tmp_path):
        out, rc = propose(tmp_path, [])
        assert rc == 0
        assert not out.exists() or not any(out.iterdir())

    def test_missing_report_fails_closed(self, tmp_path):
        with pytest.raises(fd.HuntError, match="does not exist"):
            fq.propose(tmp_path / "nope.json", tmp_path / "b.json", tmp_path / "out", "")

    def test_malformed_report_fails_closed(self, tmp_path):
        report = tmp_path / "flaky-report.json"
        report.write_text("{not json", encoding="utf-8")
        with pytest.raises(fd.HuntError, match="not valid JSON"):
            fq.read_report(report)

    def test_wrong_version_report_fails_closed(self, tmp_path):
        report = write_report(tmp_path / "flaky-report.json", [], version=99)
        with pytest.raises(fd.HuntError, match="must be version 1"):
            fq.read_report(report)

    def test_new_flaky_of_wrong_type_fails_closed(self, tmp_path):
        report = tmp_path / "flaky-report.json"
        report.write_text('{"version": 1, "new_flaky": "pkg::a", "flaky": []}', encoding="utf-8")
        with pytest.raises(fd.HuntError, match="list of test keys"):
            fq.read_report(report)

    def test_report_only_mode_is_refused_without_artifacts(self, tmp_path, capsys):
        out, rc = propose(tmp_path, ["pkg::a"], baseline=False)
        assert rc == 0
        assert "human commit" in capsys.readouterr().out
        assert not out.exists() or not any(out.iterdir())

    def test_unreadable_baseline_fails_closed(self, tmp_path):
        report = write_report(tmp_path / "flaky-report.json", ["pkg::a"])
        baseline = tmp_path / ".flaky-baseline.json"
        baseline.write_text("{broken", encoding="utf-8")
        with pytest.raises(fd.HuntError, match="unreadable floor"):
            fq.propose(report, baseline, tmp_path / "out", "")

    def test_wrong_version_baseline_fails_closed(self, tmp_path):
        report = write_report(tmp_path / "flaky-report.json", ["pkg::a"])
        write_baseline(tmp_path / ".flaky-baseline.json")
        baseline = tmp_path / ".flaky-baseline.json"
        baseline.write_text(json.dumps({"version": 2, "tests": {}}) + "\n", encoding="utf-8")
        with pytest.raises(fd.HuntError, match="unreadable floor"):
            fq.propose(report, baseline, tmp_path / "out", "")

    def test_happy_path_merges_append_only(self, tmp_path):
        out, rc = propose(tmp_path, ["pkg::a", "pkg::b"])
        assert rc == 0
        merged = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        assert merged["version"] == 1
        assert merged["tests"]["pkg::known"] == {"first_seen": "2026-01-01"}
        assert merged["tests"]["pkg::a"] == {"first_seen": TODAY}
        assert merged["tests"]["pkg::b"] == {"first_seen": TODAY}

    def test_healed_flake_is_never_removed(self, tmp_path):
        # pkg::gone was quarantined before and is healthy now: removal is
        # a reviewed commit, not a side effect of this proposal.
        report = write_report(tmp_path / "flaky-report.json", ["pkg::a"])
        write_baseline(
            tmp_path / ".flaky-baseline.json",
            tests={
                "pkg::known": {"first_seen": "2026-01-01"},
                "pkg::gone": {"first_seen": "2025-12-25"},
            },
        )
        out = tmp_path / "out"
        rc = fq.main(
            [
                "propose",
                "--report",
                str(report),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        merged = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        assert "pkg::gone" in merged["tests"]

    def test_inconsistent_evidence_fails_closed(self, tmp_path):
        report = write_report(
            tmp_path / "flaky-report.json",
            ["pkg::ghost"],
            flaky=[{"key": "pkg::other", "outcomes": ["passed", "failed"]}],
        )
        write_baseline(tmp_path / ".flaky-baseline.json")
        with pytest.raises(fd.HuntError, match="inconsistent evidence"):
            fq.propose(report, tmp_path / ".flaky-baseline.json", tmp_path / "out", "")

    def test_evidence_without_a_fail_fails_closed(self, tmp_path):
        report = write_report(
            tmp_path / "flaky-report.json",
            ["pkg::only-pass"],
            flaky=[{"key": "pkg::only-pass", "outcomes": ["passed", "skipped"]}],
        )
        write_baseline(tmp_path / ".flaky-baseline.json")
        with pytest.raises(fd.HuntError, match="inconsistent evidence"):
            fq.propose(report, tmp_path / ".flaky-baseline.json", tmp_path / "out", "")

    def test_already_quarantined_key_fails_closed(self, tmp_path):
        report = write_report(tmp_path / "flaky-report.json", ["pkg::known"])
        write_baseline(tmp_path / ".flaky-baseline.json")
        with pytest.raises(fd.HuntError, match="already quarantined"):
            fq.propose(report, tmp_path / ".flaky-baseline.json", tmp_path / "out", "")

    def test_bytes_are_deterministic(self, tmp_path):
        out_a, rc_a = propose(tmp_path, ["pkg::a"])
        second = tmp_path / "second"
        report = write_report(tmp_path / "flaky-report.json", ["pkg::a"])
        write_baseline(tmp_path / ".flaky-baseline.json")
        rc_b = fq.main(
            [
                "propose",
                "--report",
                str(report),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--out",
                str(second),
            ]
        )
        assert rc_a == rc_b == 0
        for name in (fq.BASELINE_NAME, fq.BODY_NAME):
            assert (out_a / name).read_bytes() == (second / name).read_bytes()

    def test_body_documents_the_rules(self, tmp_path):
        out, rc = propose(tmp_path, ["pkg::a"])
        assert rc == 0
        body = (out / fq.BODY_NAME).read_text(encoding="utf-8")
        assert "`pkg::a`" in body
        assert "passed -> failed -> passed" in body
        assert "Closing this PR rejects the quarantine" in body
        assert "--fail-on-flaky" in body

    def test_body_includes_run_url_when_given(self, tmp_path):
        report = write_report(tmp_path / "flaky-report.json", ["pkg::a"])
        write_baseline(tmp_path / ".flaky-baseline.json")
        out = tmp_path / "out"
        fq.main(
            [
                "propose",
                "--report",
                str(report),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--out",
                str(out),
                "--run-url",
                "https://example.test/runs/7",
            ]
        )
        assert "https://example.test/runs/7" in (out / fq.BODY_NAME).read_text(encoding="utf-8")


class TestVerify:
    def roundtrip(self, tmp_path, new=("pkg::a",)):
        out, rc = propose(tmp_path, list(new))
        assert rc == 0
        return (
            fq.main(
                [
                    "verify",
                    "--merged",
                    str(out / fq.BASELINE_NAME),
                    "--baseline",
                    str(tmp_path / ".flaky-baseline.json"),
                    "--report",
                    str(tmp_path / "flaky-report.json"),
                ]
            ),
            out,
        )

    def test_proposed_candidate_verifies(self, tmp_path):
        rc, _ = self.roundtrip(tmp_path)
        assert rc == 0

    def test_removed_key_fails_closed(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        assert rc == 0
        candidate = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        del candidate["tests"]["pkg::known"]
        (out / fq.BASELINE_NAME).write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        rc = fq.main(
            [
                "verify",
                "--merged",
                str(out / fq.BASELINE_NAME),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--report",
                str(tmp_path / "flaky-report.json"),
            ]
        )
        assert rc == 2

    def test_fabricated_key_fails_closed(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        assert rc == 0
        candidate = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        candidate["tests"]["pkg::intruder"] = {"first_seen": TODAY}
        (out / fq.BASELINE_NAME).write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        rc = fq.main(
            [
                "verify",
                "--merged",
                str(out / fq.BASELINE_NAME),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--report",
                str(tmp_path / "flaky-report.json"),
            ]
        )
        assert rc == 2

    def test_rewritten_first_seen_fails_closed(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        assert rc == 0
        candidate = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        candidate["tests"]["pkg::known"]["first_seen"] = "2020-01-01"
        (out / fq.BASELINE_NAME).write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        rc = fq.main(
            [
                "verify",
                "--merged",
                str(out / fq.BASELINE_NAME),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--report",
                str(tmp_path / "flaky-report.json"),
            ]
        )
        assert rc == 2

    def test_undated_new_entry_fails_closed(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        assert rc == 0
        candidate = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        candidate["tests"]["pkg::a"]["first_seen"] = "November"
        (out / fq.BASELINE_NAME).write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        rc = fq.main(
            [
                "verify",
                "--merged",
                str(out / fq.BASELINE_NAME),
                "--baseline",
                str(tmp_path / ".flaky-baseline.json"),
                "--report",
                str(tmp_path / "flaky-report.json"),
            ]
        )
        assert rc == 2

    def test_missing_baseline_fails_closed(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        assert rc == 0
        rc = fq.main(
            [
                "verify",
                "--merged",
                str(out / fq.BASELINE_NAME),
                "--baseline",
                str(tmp_path / "gone.json"),
                "--report",
                str(tmp_path / "flaky-report.json"),
            ]
        )
        assert rc == 2

    def test_removed_key_names_the_rule(self, tmp_path):
        rc, out = self.roundtrip(tmp_path)
        candidate = json.loads((out / fq.BASELINE_NAME).read_text(encoding="utf-8"))
        del candidate["tests"]["pkg::known"]
        (out / fq.BASELINE_NAME).write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with pytest.raises(fd.HuntError, match="removal is a reviewed commit"):
            fq.verify(
                out / fq.BASELINE_NAME,
                tmp_path / ".flaky-baseline.json",
                tmp_path / "flaky-report.json",
            )

    def test_verify_reuses_the_hunt_baseline_validator(self, tmp_path):
        # one validation standard: the candidate must satisfy the same
        # shape rules load_baseline enforces for the hunt itself
        assert fq.load_baseline is fd.load_baseline
