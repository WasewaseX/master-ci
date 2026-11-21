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


# --- the writer is held to the loader's contracts (greptile round 2) -------------


def test_recording_without_source_refuses_and_leaves_the_store_unchanged(tmp_path):
    # a valid kind/pattern/remediation with no source must not write an
    # empty source the loader rejects on the next load
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    before = store.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="missing source"):
        record_learning(store, "other-flake", "other .* failed", "rerun", "")
    assert store.read_text(encoding="utf-8") == before


def test_recording_without_pattern_refuses_and_leaves_the_store_unchanged(tmp_path):
    # the trap: an empty pattern compiles, so the compile check alone
    # never caught it - the loader's required-field rule runs first now
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    before = store.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="missing pattern"):
        record_learning(store, "other-flake", "", "rerun", "review")
    assert store.read_text(encoding="utf-8") == before


def test_recording_without_kind_or_remediation_refuses(tmp_path):
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    before = store.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="missing kind"):
        record_learning(store, "", "epic .* failed", "rerun", "review")
    with pytest.raises(ValueError, match="missing remediation"):
        record_learning(store, "epic-flake", "epic .* failed", "", "review")
    assert store.read_text(encoding="utf-8") == before


def test_every_written_store_loads_back(tmp_path):
    # the writer must eat its own output: each record re-parses the
    # rendered document through the loader before the bytes land
    store = tmp_path / "s.json"
    record_learning(store, "zz-flake", "z .* failed", "fix z", "r1")
    record_learning(store, "aa-flake", "a .* failed", "fix a", "r2")
    lessons = load_learnings(store)
    assert [lesson.kind for lesson in lessons] == ["aa-flake", "zz-flake"]


def test_cli_record_without_source_refuses_and_keeps_the_store(tmp_path, capsys):
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    before = store.read_text(encoding="utf-8")
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
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "refused" in captured.err
    assert "source" in captured.err
    assert store.read_text(encoding="utf-8") == before


def test_cli_record_without_pattern_refuses_and_keeps_the_store(tmp_path, capsys):
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    before = store.read_text(encoding="utf-8")
    rc = main(
        [
            "--store",
            str(store),
            "--record",
            "--kind",
            "epic-flake",
            "--remediation",
            "rerun",
            "--source",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "refused" in captured.err
    assert "pattern" in captured.err
    assert store.read_text(encoding="utf-8") == before


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


# --- the store's bytes obey the repo's own editorconfig contract -----------
#
# Hosted hygiene held this round to its own gate: the writer rendered the
# committed store with indent=1, editorconfig-checker red-flagged 16
# odd-padding lines, and the master/happy-path hygiene jobs went red on a
# file the repo itself generated. The gate below is parsed from the real
# .editorconfig so it tracks the config instead of a hardcoded copy.


def _editorconfig_rules(name: str) -> dict[str, str]:
    """Effective .editorconfig rules for one filename, last-section-wins."""
    config = _HERE.parent / ".editorconfig"
    assert config.is_file(), "the repo's .editorconfig must stay committed"
    rules: dict[str, str] = {}
    matched = False
    for raw in config.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            glob = line[1:-1]
            matched = _glob_matches(glob, name)
            continue
        if matched and "=" in line:
            key, _, value = line.partition("=")
            rules[key.strip()] = value.strip()
    return rules


def _glob_matches(glob: str, name: str) -> bool:
    """The subset of fnmatch the config uses: literal, *, {a,b}."""
    import fnmatch

    return any(fnmatch.fnmatch(name, part) for part in glob.split(","))


def _assert_obey_editorconfig(text: str, name: str) -> None:
    rules = _editorconfig_rules(name)
    indent = int(rules.get("indent_size", "2"))
    lines = text.split("\n")
    assert lines[-1] == "", f"{name}: final newline required"
    if rules.get("trim_trailing_whitespace") == "true":
        for number, line in enumerate(lines[:-1], 1):
            assert line == line.rstrip(), f"{name}:{number}: trailing whitespace"
    if rules.get("indent_style") == "space":
        for number, line in enumerate(lines[:-1], 1):
            padding = len(line) - len(line.lstrip(" "))
            assert padding % indent == 0, (
                f"{name}:{number}: left padding {padding} is not a multiple of {indent}"
            )


def test_recorded_store_obey_the_editorconfig_contract(tmp_path):
    # the writer's own output - not just the committed copy - must satisfy
    # the repo's formatting contract: this store is committed, so any
    # render the hygiene gate would red-flag never reaches the bytes
    store = tmp_path / "s.json"
    record_learning(store, "epic-flake", "epic .* failed", "rerun", "review")
    text = store.read_text(encoding="utf-8")
    _assert_obey_editorconfig(text, "master-ci-learnings.json")
    assert "\r" not in text, "charset lf forbids carriage returns"
    # the contract holds across a re-record too (the committed path)
    record_learning(store, "aa-flake", "a .* failed", "rerun", "review")
    _assert_obey_editorconfig(store.read_text(encoding="utf-8"), "master-ci-learnings.json")


def test_committed_store_obey_the_editorconfig_contract():
    # the committed store itself: fail-closed load proves it parses, the
    # byte check proves the repo's own gate would pass it
    committed = _HERE.parent / ".github" / "master-ci-learnings.json"
    text = committed.read_text(encoding="utf-8")
    lessons = load_learnings(committed)
    assert lessons, "the committed store carries the repo's real lessons"
    _assert_obey_editorconfig(text, "master-ci-learnings.json")
