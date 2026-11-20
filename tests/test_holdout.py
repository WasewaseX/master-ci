"""Contract tests for the holdout split and real-repo corpus (A20).

The documented contracts, test-pinned:

- the split: a pure function of salt and case id - two calls agree,
    every case lands in exactly one split, and the ratio is honored
    within the noise a hash split guarantees;
- the real-repo manifest: validated fail-closed (schema, non-empty
    provenance, 40-hex commit, label verbs), and a fixture the manifest
    names but the corpus does not vendor is a recorded data gap - never
    a guessed pass;
- the labels: ``detected`` needs the check's finding, ``clean`` needs
    its absence, and a disagreeing label is a violation, not a
    relabeled pass;
- exit codes: 0 clean, 1 violations, 2 configuration error.
"""

import json
from pathlib import Path

import pytest  # noqa: E402
import sem_holdout as holdout  # noqa: E402

# --- the deterministic split -------------------------------------------------


def test_split_case_is_a_pure_function_of_salt_and_id():
    assert holdout.split_case("debug-print", 30) == holdout.split_case("debug-print", 30)
    assert holdout.split_case("debug-print", 30) in ("train", "holdout")


def test_two_calls_agree_and_no_case_is_in_both_splits():
    cases = [f"case-{index}" for index in range(500)]
    first = holdout.split_cases(cases, 30)
    second = holdout.split_cases(cases, 30)
    assert first == second
    train = {case for case, split in first.items() if split == "train"}
    hold = set(first) - train
    assert not (train & hold)
    assert train | hold == set(cases)


def test_ratio_is_honored_within_hash_noise():
    cases = [f"case-{index}" for index in range(2000)]
    splits = holdout.split_cases(cases, 30)
    holdout_share = sum(1 for split in splits.values() if split == "holdout") / len(cases)
    assert 0.25 < holdout_share < 0.35
    zero = holdout.split_cases(cases, 0)
    assert all(split == "train" for split in zero.values())


def test_salt_changes_the_cut():
    cases = [f"case-{index}" for index in range(200)]
    default = holdout.split_cases(cases, 30)
    other = holdout.split_cases(cases, 30, salt="other-salt")
    assert any(default[case] != other[case] for case in cases)


# --- the real-repo manifest --------------------------------------------------


def _manifest(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "corpus-real.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_repo(fixtures: list[dict[str, object]]) -> dict[str, object]:
    return {
        "repo": "example/warehouse",
        "upstream_url": "https://github.com/example/warehouse",
        "commit": "a" * 40,
        "fixtures": fixtures,
    }


def test_manifest_without_schema_or_repos_is_refused(tmp_path):
    with pytest.raises(holdout.CorpusError):
        holdout.load_real_corpus(_manifest(tmp_path, {"schema": "accuracy-v1", "repos": []}))
    with pytest.raises(holdout.CorpusError):
        holdout.load_real_corpus(_manifest(tmp_path, {"schema": "accuracy-real-v1"}))
    with pytest.raises(holdout.CorpusError):
        holdout.load_real_corpus(tmp_path / "missing.json")


def test_manifest_provenance_fields_are_fail_closed(tmp_path):
    for mutation in (
        {"repo": "", "upstream_url": "https://x", "commit": "a" * 40, "fixtures": []},
        {"repo": "r", "upstream_url": "", "commit": "a" * 40, "fixtures": []},
        {"repo": "r", "upstream_url": "https://x", "commit": "abc", "fixtures": []},
        {"repo": "r", "upstream_url": "https://x", "commit": "z" * 40, "fixtures": []},
        {"repo": "r", "upstream_url": "https://x", "commit": "a" * 40},
    ):
        with pytest.raises(holdout.CorpusError):
            holdout.load_real_corpus(
                _manifest(tmp_path, {"schema": "accuracy-real-v1", "repos": [mutation]})
            )


def test_manifest_labels_are_fail_closed(tmp_path):
    for labels in ({}, {"debug-print": "maybe"}, {"": "detected"}):
        repo = _valid_repo([{"path": "clean_py", "labels": labels}])
        with pytest.raises(holdout.CorpusError):
            holdout.load_real_corpus(
                _manifest(tmp_path, {"schema": "accuracy-real-v1", "repos": [repo]})
            )


def test_valid_manifest_loads(tmp_path):
    repo = _valid_repo([{"path": "clean_py", "labels": {"debug-print": "clean"}}])
    repos = holdout.load_real_corpus(
        _manifest(tmp_path, {"schema": "accuracy-real-v1", "repos": [repo]})
    )
    assert repos[0]["repo"] == "example/warehouse"


# --- judging the vendored rows -----------------------------------------------


def test_missing_vendored_fixture_is_a_gap_not_a_pass(tmp_path):
    repo = _valid_repo([{"path": "not-vendored", "labels": {"debug-print": "detected"}}])
    stats, violations, gaps = holdout.judge_real_rows([repo], tmp_path)
    assert not stats and not violations
    assert gaps == [
        {
            "case": "example/warehouse:not-vendored",
            "reason": "vendored fixture missing from the corpus",
        }
    ]


def test_detected_and_clean_labels_are_judged(tmp_path):
    vendored = tmp_path / "debug_py"
    vendored.mkdir()
    (vendored / "worker.py").write_text(
        "def work(items):\n    for item in items:\n        print('debug', item)\n    return items\n",
        encoding="utf-8",
    )
    clean = tmp_path / "clean_py"
    clean.mkdir()
    (clean / "calc.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    repo = _valid_repo(
        [
            {"path": "debug_py", "labels": {"debug-print": "detected"}},
            {"path": "clean_py", "labels": {"debug-print": "clean"}},
        ]
    )
    stats, violations, gaps = holdout.judge_real_rows([repo], tmp_path)
    assert not gaps
    detected = stats["example/warehouse:debug_py"]
    assert detected.tp == 1 and detected.fn == 0
    clean_stats = stats["example/warehouse:clean_py"]
    assert clean_stats.tn >= 1 and clean_stats.fp == 0
    assert not violations


def test_disagreeing_label_is_a_violation(tmp_path):
    clean = tmp_path / "clean_py"
    clean.mkdir()
    (clean / "calc.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    repo = _valid_repo([{"path": "clean_py", "labels": {"debug-print": "detected"}}])
    stats, violations, gaps = holdout.judge_real_rows([repo], tmp_path)
    assert stats["example/warehouse:clean_py"].fn == 1
    assert violations and "expected detected" in violations[0][2]
    assert not gaps


# --- the end-to-end run ------------------------------------------------------


def _repo_fixtures() -> Path:
    """The real corpus: load_benchmark is fail-closed over the full table."""
    return Path(__file__).resolve().parents[1] / "fixtures" / "bench"


def test_run_holdout_judges_and_splits(tmp_path):
    text, report, code = holdout.run_holdout(_repo_fixtures(), None, 30)
    assert code == 0
    assert report["schema"] == "holdout-report-v1"
    assert report["salt"] == holdout.SALT
    splits = report["splits"]
    assert "py-print-debug" in splits
    assert splits["py-print-debug"] == holdout.split_case("py-print-debug", 30)
    rows = report["fixture_rows"]["debug-print"]
    assert rows["tp"] >= 1 and rows["fn"] == 0 and rows["tn"] >= 1
    assert rows["split"] == splits["py-print-debug"]
    assert not report["gaps"]
    assert "Split manifest" in text and "py-print-debug" in text


def test_run_holdout_reports_real_rows_and_gaps(tmp_path):
    manifest = _manifest(
        tmp_path,
        {
            "schema": "accuracy-real-v1",
            "repos": [
                _valid_repo(
                    [
                        {"path": "gone", "labels": {"debug-print": "detected"}},
                    ]
                )
            ],
        },
    )
    text, report, code = holdout.run_holdout(_repo_fixtures(), manifest, 30)
    assert code == 0
    assert report["gaps"] == [
        {"case": "example/warehouse:gone", "reason": "vendored fixture missing from the corpus"}
    ]
    assert "Data gaps" in text
    assert "example/warehouse:gone: holdout" in text or "example/warehouse:gone: train" in text


def test_cli_exit_codes(tmp_path, capsys):
    assert holdout.main(["--fixtures-dir", str(_repo_fixtures())]) == 0
    assert holdout.main(["--fixtures-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "holdout:" in err
