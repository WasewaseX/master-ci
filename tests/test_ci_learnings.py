"""The reviewer-learnings contracts (roadmap B28 slice, round-18).

A learning is one review lesson the signature table did not know yet:
a failure-signature regex, the remediation that actually fixed it and
the review that taught it. The store is fail-closed (a wrong version,
a missing field or an uncompilable pattern refuses the load),
recording is deterministic (deduplicated on kind+pattern, sorted,
byte-stable rewrites, a re-record updates the lesson in place), and
diagnosis applies lessons with ``learned`` confidence so yesterday's
unknown failure becomes today's classified one - the Greptile loop,
without the guessing.
"""

import json
import re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))

from sem_ci_diagnose import diagnose_log  # noqa: E402
from sem_ci_learnings import Learning, load_learnings, main, record_learning  # noqa: E402


def _store(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"version": 1, "learnings": rows}), encoding="utf-8")
    return path


def _row(kind: str = "epic-flake", pattern: str = r"epic .* failed", **over: str) -> dict:
    row = {
        "kind": kind,
        "pattern": pattern,
        "remediation": "restart the epic runner, then rerun",
        "source": "greptile review, round-18",
    }
    row.update(over)
    return row


# --- fail-closed loading --------------------------------------------------------


def test_store_round_trips_through_record_and_load(tmp_path):
    record_learning(tmp_path / "s.json", "epic-flake", "epic .* failed", "rerun", "review")
    lessons = load_learnings(tmp_path / "s.json")
    assert len(lessons) == 1
    assert lessons[0].kind == "epic-flake"
    assert lessons[0].pattern == "epic .* failed"
    assert lessons[0].source == "review"


def test_wrong_version_refuses(tmp_path):
    path = _store(tmp_path / "s.json", [])
    path.write_text(json.dumps({"version": 99, "learnings": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected version"):
        load_learnings(path)


def test_missing_field_refuses(tmp_path):
    row = _row()
    del row["source"]
    with pytest.raises(ValueError, match="missing source"):
        load_learnings(_store(tmp_path / "s.json", [row]))


def test_empty_remediation_refuses(tmp_path):
    with pytest.raises(ValueError, match="non-empty kind and remediation"):
        load_learnings(_store(tmp_path / "s.json", [_row(remediation="   ")]))


def test_uncompilable_pattern_refuses(tmp_path):
    with pytest.raises(ValueError, match="does not compile"):
        load_learnings(_store(tmp_path / "s.json", [_row(pattern="([unclosed")]))


def test_non_object_entry_refuses(tmp_path):
    with pytest.raises(ValueError, match="must be an object"):
        load_learnings(_store(tmp_path / "s.json", ["not an object"]))  # type: ignore[list-item]


def test_learnings_list_must_be_a_list(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 1, "learnings": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        load_learnings(path)


# --- deterministic recording ------------------------------------------------------


def test_recording_is_deduplicated_on_kind_and_pattern(tmp_path):
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review one")
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review two")
    lessons = load_learnings(store)
    assert len(lessons) == 1
    assert lessons[0].source == "review two"  # the newer lesson wins


def test_store_is_sorted_and_byte_stable(tmp_path):
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    record_learning(one, "zz-kind", "b failed", "fix b", "r1")
    record_learning(one, "aa-kind", "a failed", "fix a", "r2")
    record_learning(two, "aa-kind", "a failed", "fix a", "r2")
    record_learning(two, "zz-kind", "b failed", "fix b", "r1")
    lessons = load_learnings(one)
    assert [lesson.kind for lesson in lessons] == ["aa-kind", "zz-kind"]
    assert one.read_text(encoding="utf-8") == two.read_text(encoding="utf-8")


def test_recording_an_uncompilable_lesson_refuses(tmp_path):
    store = tmp_path / "s.json"
    with pytest.raises(ValueError, match="does not compile"):
        record_learning(store, "bad", "([unclosed", "fix", "review")
    assert not store.exists()


def test_empty_store_is_valid(tmp_path):
    assert load_learnings(_store(tmp_path / "s.json", [])) == ()


# --- diagnosis integration ---------------------------------------------------------


def test_learned_failure_classifies_with_learned_confidence():
    learning = Learning(
        kind="epic-flake",
        pattern=r"epic .* failed to start",
        remediation="restart the epic runner, then rerun",
        source="greptile review, round-18",
        compiled=re.compile(r"epic .* failed to start"),
    )
    diagnosis = diagnose_log("epic deploy failed to start this morning", (learning,))
    assert diagnosis.kinds == ["epic-flake"]
    assert diagnosis.failures[0].confidence == "learned"
    assert diagnosis.failures[0].remediation == "restart the epic runner, then rerun"
    assert diagnosis.unclassified == 0


def test_signature_table_wins_over_learnings():
    learning = Learning(
        kind="my-ruff",
        pattern=r"F401",
        remediation="my own fix",
        source="made up",
        compiled=re.compile(r"F401"),
    )
    diagnosis = diagnose_log("src/a.py:3:1: F401 [*] `os` imported but unused", (learning,))
    assert diagnosis.kinds == ["ruff"]
    assert diagnosis.failures[0].confidence == "textual"


def test_unlearned_failure_stays_unclassified_with_evidence():
    diagnosis = diagnose_log("fatal: unable to access repository", ())
    assert diagnosis.kinds == ["unclassified"]
    assert "fatal: unable to access repository" in diagnosis.failures[0].evidence[0]


def test_learned_hits_group_into_one_cause():
    learning = Learning(
        kind="epic-flake",
        pattern=r"epic .* failed",
        remediation="rerun",
        source="review",
        compiled=re.compile(r"epic .* failed"),
    )
    text = "epic a failed\nepic b failed\nprogress line"
    diagnosis = diagnose_log(text, (learning,))
    assert diagnosis.kinds == ["epic-flake"]
    assert len(diagnosis.failures) == 1
    assert len(diagnosis.failures[0].evidence) == 2


# --- CLI ------------------------------------------------------------------------


def test_cli_record_then_show(tmp_path, capsys):
    store = tmp_path / "s.json"
    rc = main(
        [
            "--store",
            str(store),
            "--record",
            "--kind",
            "epic-flake",
            "--pattern",
            "epic .* failed",
            "--remediation",
            "rerun",
            "--source",
            "greptile review, round-18",
        ]
    )
    assert rc == 0
    rc = main(["--store", str(store), "--show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "epic-flake" in out
    assert "greptile review, round-18" in out


def test_cli_record_refuses_an_uncompilable_pattern(tmp_path, capsys):
    rc = main(
        [
            "--store",
            str(tmp_path / "s.json"),
            "--record",
            "--kind",
            "bad",
            "--pattern",
            "([unclosed",
            "--remediation",
            "fix",
            "--source",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "refused" in captured.err


def test_cli_without_mode_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["--store", "whatever.json"])
    assert exc.value.code == 2
