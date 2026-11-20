"""The CI-failure-diagnosis contracts (roadmap B34, round-18).

The signature table classifies what this repository's own gates emit:
ruff check, ruff format, mypy, pytest, yamllint, markdown-lint, the
file-too-large ceiling, the quality ratchet and the pin-lag watchdog.
The honesty rules are the contract: step-name matches are ``exact``,
log-line matches are ``textual``, no match is ``unclassified`` with
evidence preserved (never forced into a bucket), non-failed
conclusions are never failures, and a job that failed without any
signature match is still reported - as unknown, loudly.
"""

import json
import re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))

from sem_ci_diagnose import (  # noqa: E402
    diagnose_jobs,
    diagnose_log,
    main,
    render_markdown,
)
from sem_ci_learnings import Learning  # noqa: E402


def _job(name: str, conclusion: str, steps: list[dict], log_excerpt: str = "") -> dict:
    return {"name": name, "conclusion": conclusion, "steps": steps, "log_excerpt": log_excerpt}


def _step(name: str, conclusion: str) -> dict:
    return {"name": name, "conclusion": conclusion}


# --- log mode --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("src/a.py:3:1: F401 [*] `os` imported but unused", "ruff"),
        ("would reformat scripts/tool_versions.py", "ruff-format"),
        ("scripts/slop_scan.py:12: error: Cannot find implementation or library stub", "mypy"),
        ("FAILED tests/test_density.py::test_one - assert 1 == 2", "pytest"),
        ("ERROR tests/test_ratchet.py::test_two - AssertionError", "pytest"),
        ("file-too-large: scripts/ai_slop_detector.py exceeds the ceiling", "file-too-large"),
        ("quality ratchet: baseline regression detected", "quality-ratchet"),
        ("Report pin lag: quality-store pin is 3 runs behind", "pin-lag"),
    ],
)
def test_log_signatures_classify_each_gate(line, kind):
    assert diagnose_log(line).kinds == [kind]


def test_log_evidence_carries_line_numbers():
    diagnosis = diagnose_log("fine line\nFAILED tests/test_x.py::t - assert")
    cause = diagnosis.failures[0]
    assert cause.confidence == "textual"
    assert cause.evidence[0].startswith("line 2:")
    assert "FAILED tests/test_x.py" in cause.evidence[0]


def test_log_unmatched_lines_are_silence_not_guesses():
    assert diagnose_log("Step succeeded in 4.2s\nUploading artifact").failures == []


@pytest.mark.parametrize(
    "line",
    [
        "fatal: unable to access 'https://github.com/owner/repo.git/'",
        "Error: Process completed with exit code 1",
        "##[error]Node process exited with a failure",
        "Process completed with exit code 127",
    ],
)
def test_clear_failures_without_signature_stay_as_unclassified_evidence(line):
    diagnosis = diagnose_log(line)
    assert diagnosis.kinds == ["unclassified"]
    assert diagnosis.unclassified == 1
    assert line in diagnosis.failures[0].evidence[0]


def test_unknown_failures_group_into_one_unclassified_cause():
    text = "fatal: unable to access repository\nError: Process completed with exit code 1"
    diagnosis = diagnose_log(text)
    assert diagnosis.unclassified == 2
    assert len(diagnosis.failures) == 1
    assert len(diagnosis.failures[0].evidence) == 2


def test_zero_exit_code_is_progress_not_failure():
    assert diagnose_log("Process completed with exit code 0").failures == []


def test_progress_lines_stay_silent_next_to_generic_failures():
    text = "\n".join(
        [
            "Step succeeded in 4.2s",
            "Uploading artifact",
            "Cache restored from key",
            "Process completed with exit code 0",
        ]
    )
    assert diagnose_log(text).failures == []


def test_log_remediation_is_attached():
    diagnosis = diagnose_log("would reformat scripts/a.py")
    assert "ruff format" in diagnosis.failures[0].remediation


def test_linter_tool_name_alone_is_not_a_failure():
    assert diagnose_log("markdown-lint: 42 files checked").failures == []
    assert diagnose_log("markdown-lint: 3 errors found").kinds == ["markdown-lint"]
    assert diagnose_log("yamllint: all clean").failures == []
    assert diagnose_log("yamllint error in deploy.yml").kinds == ["yamllint"]


def test_log_multiple_failures_deduplicate_into_one_cause():
    text = "\n".join(["FAILED tests/test_a.py::x - assert", "FAILED tests/test_b.py::y - assert"])
    diagnosis = diagnose_log(text)
    assert diagnosis.kinds == ["pytest"]
    assert len(diagnosis.failures[0].evidence) == 2


# --- Actions log wrappers -----------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "2026-11-20T10:00:00Z FAILED tests/test_x.py::test_one - AssertionError",
        "2026-11-20T10:00:00.0000000Z FAILED tests/test_x.py::test_one - AssertionError",
        "build\tLint YAML\t2026-11-20T10:00:00.0000000Z FAILED tests/test_x.py::test_one",
    ],
)
def test_actions_log_prefixes_no_longer_hide_pytest_failures(line):
    assert diagnose_log(line).kinds == ["pytest"]


def test_prefixed_clear_failure_is_still_preserved_as_unclassified():
    line = "2026-11-20T10:00:00Z fatal: unable to access repository"
    diagnosis = diagnose_log(line)
    assert diagnosis.kinds == ["unclassified"]
    assert "fatal: unable to access repository" in diagnosis.failures[0].evidence[0]


def test_tab_without_timestamp_does_not_swallow_the_message():
    # a tab inside real output must not eat the rest of the line
    assert diagnose_log("make\techo FAILED but not a test").failures == []


# --- jobs-json mode ----------------------------------------------------------------


def test_failed_step_matched_by_name_is_exact():
    payload = json.dumps(
        {
            "jobs": [
                _job("quality", "failure", [_step("Ruff format check", "failure")]),
            ]
        }
    )
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.kinds == ["ruff-format"]
    assert diagnosis.failures[0].confidence == "exact"


def test_lint_yaml_step_name_is_classified_by_the_workflow_spelling():
    # the repository's own step is named "Lint YAML" (master-ci.yml)
    payload = json.dumps({"jobs": [_job("quality", "failure", [_step("Lint YAML", "failure")])]})
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.kinds == ["yamllint"]
    assert diagnosis.failures[0].confidence == "exact"
    assert "yamllint ." in diagnosis.failures[0].remediation


def test_successful_and_skipped_steps_are_never_failures():
    payload = json.dumps(
        {
            "jobs": [
                _job(
                    "quality",
                    "success",
                    [_step("ruff check", "success"), _step("mypy", "skipped")],
                )
            ]
        }
    )
    assert diagnose_jobs(payload).failures == []


def test_job_with_failed_conclusion_but_no_failed_step_is_still_reported():
    payload = json.dumps({"jobs": [_job("mystery", "failure", [])]})
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.kinds == ["unclassified"]
    assert diagnosis.unclassified == 1
    assert "mystery" in diagnosis.failures[0].evidence[0]


def test_unknown_failed_step_name_is_unclassified_with_evidence():
    payload = json.dumps({"jobs": [_job("j", "failure", [_step("Deploy widgets", "failure")])]})
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.failures[0].kind == "unclassified"
    assert "Deploy widgets" in diagnosis.failures[0].evidence[0]


def test_failed_step_falls_back_to_log_excerpt_text():
    payload = json.dumps(
        {
            "jobs": [
                _job(
                    "j",
                    "failure",
                    [_step("Run something opaque", "failure")],
                    log_excerpt="FAILED tests/test_z.py::t - AssertionError",
                )
            ]
        }
    )
    diagnosis = diagnose_jobs(payload)
    assert "pytest" in diagnosis.kinds


def test_non_failed_jobs_are_ignored_even_when_their_steps_are_red():
    payload = json.dumps(
        {
            "jobs": [
                _job("cancelled-run", "cancelled", [_step("ruff check", "failure")]),
                _job("skipped-run", "skipped", [_step("mypy", "failure")]),
            ]
        }
    )
    assert diagnose_jobs(payload).failures == []


def test_single_job_object_without_jobs_wrapper_is_accepted():
    payload = json.dumps(_job("j", "failure", [_step("mypy", "failure")]))
    assert diagnose_jobs(payload).kinds == ["mypy"]


def test_malformed_jobs_json_raises_jsondecodeerror():
    with pytest.raises(json.JSONDecodeError):
        diagnose_jobs("{ broken")


# --- merge parity (greptile round 2) -------------------------------------------------


def _lesson(kind: str, pattern: str, remediation: str) -> Learning:
    return Learning(
        kind=kind,
        pattern=pattern,
        remediation=remediation,
        source="greptile review",
        compiled=re.compile(pattern),
    )


def test_lessons_of_one_kind_with_different_fixes_stay_separate():
    restart = _lesson("epic-flake", r"epic .* failed to start", "restart the runner, then rerun")
    purge = _lesson("epic-flake", r"epic .* cache corrupt", "purge the shard cache")
    text = "epic deploy failed to start\nepic shard cache corrupt"
    diagnosis = diagnose_log(text, (restart, purge))
    learned = [c for c in diagnosis.failures if c.confidence == "learned"]
    assert len(learned) == 2
    assert all(c.kind == "epic-flake" for c in learned)
    assert {c.remediation for c in learned} == {
        "restart the runner, then rerun",
        "purge the shard cache",
    }
    assert len(learned[0].evidence) == 1 and len(learned[1].evidence) == 1


def test_lessons_sharing_one_fix_still_group():
    one = _lesson("epic-flake", r"epic a failed", "rerun")
    two = _lesson("epic-flake", r"epic b failed", "rerun")
    diagnosis = diagnose_log("epic a failed\nepic b failed", (one, two))
    learned = [c for c in diagnosis.failures if c.confidence == "learned"]
    assert len(learned) == 1
    assert len(learned[0].evidence) == 2


def test_jobs_merge_keeps_lessons_with_different_fixes_separate():
    # two jobs, each excerpt hitting a different lesson of the same
    # kind: merging the job reports keeps both fixes, not the first
    restart = _lesson("epic-flake", r"epic .* failed to start", "restart the runner, then rerun")
    purge = _lesson("epic-flake", r"epic .* cache corrupt", "purge the shard cache")
    payload = json.dumps(
        {
            "jobs": [
                _job("job-a", "failure", [], log_excerpt="epic deploy failed to start"),
                _job("job-b", "failure", [], log_excerpt="epic shard cache corrupt"),
            ]
        }
    )
    diagnosis = diagnose_jobs(payload, (restart, purge))
    learned = [c for c in diagnosis.failures if c.confidence == "learned"]
    assert len(learned) == 2
    assert {c.remediation for c in learned} == {
        "restart the runner, then rerun",
        "purge the shard cache",
    }


def test_jobs_mode_counts_every_unknown_line_not_one_per_job():
    # greptile's example: two failed jobs with one fatal line each
    # report two unknown lines, the same as log mode - not one
    payload = json.dumps(
        {
            "jobs": [
                _job("net-a", "failure", [], log_excerpt="fatal: unable to access repository"),
                _job("net-b", "failure", [], log_excerpt="fatal: unable to access repository"),
            ]
        }
    )
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.unclassified == 2
    assert len(diagnosis.failures) == 1
    assert len(diagnosis.failures[0].evidence) == 2


def test_unknown_counts_survive_mixed_jobs():
    payload = json.dumps(
        {
            "jobs": [
                _job(
                    "net-a",
                    "failure",
                    [],
                    log_excerpt="fatal: unable to access repository\nfatal: not a git repository",
                ),
                _job("net-b", "failure", [], log_excerpt="fatal: authentication failed"),
            ]
        }
    )
    assert diagnose_jobs(payload).unclassified == 3


def test_jobs_and_log_modes_agree_on_unknown_counts():
    # the merge-parity invariant: the same unknown lines through jobs
    # JSON and through the raw log must produce the same count
    lines = ["fatal: unable to access repository", "Error: Process completed with exit code 1"]
    log_diagnosis = diagnose_log("\n".join(lines))
    payload = json.dumps(
        {
            "jobs": [
                _job("a", "failure", [], log_excerpt=lines[0]),
                _job("b", "failure", [], log_excerpt=lines[1]),
            ]
        }
    )
    jobs_diagnosis = diagnose_jobs(payload)
    assert log_diagnosis.unclassified == 2
    assert jobs_diagnosis.unclassified == log_diagnosis.unclassified
    assert jobs_diagnosis.kinds == log_diagnosis.kinds == ["unclassified"]


def test_merging_unknown_into_classified_job_causes_keeps_the_count():
    # an unmatched failed step plus unknown excerpt lines on one job:
    # the step row and the line rows all count
    payload = json.dumps(
        {
            "jobs": [
                _job(
                    "deploy",
                    "failure",
                    [_step("Deploy widgets", "failure")],
                    log_excerpt="fatal: unable to access repository",
                )
            ]
        }
    )
    diagnosis = diagnose_jobs(payload)
    assert diagnosis.unclassified == 2
    assert len(diagnosis.failures[0].evidence) == 2


# --- render and CLI ------------------------------------------------------------------


def test_render_lists_kind_confidence_evidence_and_remediation():
    diagnosis = diagnose_log("would reformat scripts/a.py")
    markdown = render_markdown(diagnosis)
    assert "ruff-format" in markdown
    assert "textual" in markdown
    assert "remediation" in markdown


def test_render_empty_diagnosis_is_explicit():
    assert "No failure matched" in render_markdown(diagnose_log("all green"))


def test_cli_log_mode_exits_zero(tmp_path, capsys):
    log = tmp_path / "job.log"
    log.write_text("FAILED tests/test_a.py::t - assert\n", encoding="utf-8")
    rc = main(["--log", str(log)])
    assert rc == 0
    assert "pytest" in capsys.readouterr().out


def test_cli_jobs_json_mode_prints_json(tmp_path, capsys):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": [_job("j", "failure", [_step("mypy", "failure")])]}))
    rc = main(["--jobs-json", str(path), "--json"])
    record = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert record["kinds"] == ["mypy"]
    assert record["unclassified"] == 0


def test_cli_requires_exactly_one_input():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["--log", "-", "--jobs-json", "-"])
    assert exc.value.code == 2


def test_cli_missing_input_file_exits_two(tmp_path, capsys):
    rc = main(["--log", str(tmp_path / "absent.log")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_cli_learnings_store_classifies_with_learned_confidence(tmp_path, capsys):
    store = tmp_path / "learnings.json"
    store.write_text(
        json.dumps(
            {
                "version": 1,
                "learnings": [
                    {
                        "kind": "epic-flake",
                        "pattern": r"epic .* failed to start",
                        "remediation": "restart the epic runner, then rerun",
                        "source": "greptile review, round-18",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "job.log"
    log.write_text("epic deploy failed to start this morning\n", encoding="utf-8")
    rc = main(["--log", str(log), "--learnings", str(store)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "epic-flake" in out
    assert "learned" in out
    assert "restart the epic runner" in out


def test_cli_unlearned_failure_stays_unclassified(tmp_path, capsys):
    store = tmp_path / "empty.json"
    store.write_text(json.dumps({"version": 1, "learnings": []}), encoding="utf-8")
    log = tmp_path / "job.log"
    log.write_text("fatal: unable to access repository\n", encoding="utf-8")
    rc = main(["--log", str(log), "--learnings", str(store)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "unclassified" in out
    assert "fatal: unable to access repository" in out


def test_cli_bad_learnings_store_refuses(tmp_path, capsys):
    store = tmp_path / "bad.json"
    store.write_text('{"version": 99, "learnings": []}', encoding="utf-8")
    log = tmp_path / "job.log"
    log.write_text("anything\n", encoding="utf-8")
    rc = main(["--log", str(log), "--learnings", str(store)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "refused" in captured.err
