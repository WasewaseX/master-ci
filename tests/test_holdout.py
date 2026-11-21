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
    relabeled pass; a label naming a check the registry does not know
    is refused at load - a typo is never a vacuous clean row;
- the counts: one case per fixture slug, the case the split manifest
    names, split labels read from that same manifest (never re-hashed
    under a default ratio), so training and holdout evidence never mix;
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
    for labels in ({}, {"debug-print": "maybe"}, {"": "detected"}, {"debug-prnit": "clean"}):
        repo = _valid_repo([{"path": "clean_py", "labels": labels}])
        with pytest.raises(holdout.CorpusError):
            holdout.load_real_corpus(
                _manifest(tmp_path, {"schema": "accuracy-real-v1", "repos": [repo]})
            )


def test_unknown_check_ids_are_refused_not_judged(tmp_path):
    """A typo check id is refused at load, never a vacuous clean row.

    ``debug-prnit`` can never disagree with a scan - no finding carries
    that id - so judging it always scores a correct clean result. The
    registry gate turns the typo into a configuration error.
    """
    repo = _valid_repo([{"path": "clean_py", "labels": {"debug-prnit": "clean"}}])
    with pytest.raises(holdout.CorpusError) as exc:
        holdout.load_real_corpus(
            _manifest(tmp_path, {"schema": "accuracy-real-v1", "repos": [repo]})
        )
    assert "not a live check id" in str(exc.value)


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
    rows = report["fixture_rows"]
    assert "py-print-debug" in rows, "cases are fixture slugs, the ids the manifest names"
    assert "debug-print" not in rows, "check names are not cases"
    case_rows = rows["py-print-debug"]
    assert case_rows["tp"] >= 1 and case_rows["fn"] == 0
    assert case_rows["split"] == splits["py-print-debug"]
    assert not report["gaps"]
    assert "Split manifest" in text and "py-print-debug" in text


def test_every_judged_row_lands_in_exactly_one_slug_case():
    """One slug, one case: no slug's counts absorb another slug's results.

    Keying by check merges fixtures that name the same rule - two
    fixtures in different splits become one case whose split is a hash
    of the check's name - and the evidence stops measuring either half.
    """
    from fixture_benchmark import load_benchmark

    fixtures_dir = _repo_fixtures()
    rows = load_benchmark(fixtures_dir)
    _, report, _ = holdout.run_holdout(fixtures_dir, None, 30)
    case_rows = report["fixture_rows"]
    assert set(case_rows) == {row.slug for row in rows}
    assert all(
        counts["tp"] + counts["fp"] + counts["fn"] + counts["tn"] == 1
        for counts in case_rows.values()
    )
    assert sum(c["tp"] + c["fp"] + c["fn"] + c["tn"] for c in case_rows.values()) == len(rows)


def test_row_labels_come_from_the_reported_manifest_not_a_rehash():
    """`--ratio 0` labels every row train, `--ratio 100` every row holdout.

    The rows' split labels come from the already-computed manifest the
    report ships; re-hashing each case under the default 30 percent
    would contradict that manifest on the extreme ratios.
    """
    fixtures_dir = _repo_fixtures()
    for ratio in (0, 100):
        _, report, _ = holdout.run_holdout(fixtures_dir, None, ratio)
        expected = "train" if ratio == 0 else "holdout"
        assert all(split == expected for split in report["splits"].values()), ratio
        assert all(counts["split"] == expected for counts in report["fixture_rows"].values()), ratio
        assert all(
            counts["split"] == report["splits"][case]
            for case, counts in report["fixture_rows"].items()
        ), ratio


def test_train_and_holdout_evidence_stay_separated():
    """Both halves are measured: neither split absorbs the other's rows.

    The default ratio's manifest puts cases in both splits, and the
    per-case counts must follow that assignment - a combined count
    keyed by check would empty one half entirely.
    """
    fixtures_dir = _repo_fixtures()
    _, report, _ = holdout.run_holdout(fixtures_dir, None, 30)
    splits = report["splits"]
    assert set(splits.values()) == {"train", "holdout"}, "both halves exist at ratio 30"
    for split in ("train", "holdout"):
        cases = [case for case, row_split in splits.items() if row_split == split]
        judged = [
            case
            for case in cases
            if case in report["fixture_rows"]
            and (
                report["fixture_rows"][case]["tp"]
                + report["fixture_rows"][case]["fp"]
                + report["fixture_rows"][case]["fn"]
                + report["fixture_rows"][case]["tn"]
            )
            > 0
        ]
        assert judged, f"the {split} half keeps its own judged evidence"


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
