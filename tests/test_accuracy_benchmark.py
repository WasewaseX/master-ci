"""Contract tests for the accuracy benchmark (v2.18, audit finding H).

The fixture benchmark proves declared outcomes; these tests prove the
accuracy instrument itself, and - more importantly - they ARE the
accuracy regression suite: the clean negative-control projects must
stay clean under the quality dimensions, so any analyzer change that
starts false-positiveing complete, hand-shaped code fails here with
the exact finding named.
"""

import json
from pathlib import Path

import pytest
from accuracy_benchmark import (
    CORPUS_DIR,
    CorpusError,
    build_report,
    load_known_fps,
    run_accuracy_benchmark,
)
from ai_slop_detector import scan
from slop_categories import CHECK_CATEGORIES
from slop_files import load_phrases

CONTROLS = ("clean_python", "clean_js", "clean_go", "clean_csharp", "clean_cpp")


def test_clean_controls_stay_clean_under_quality_dimensions():
    # The accuracy regression suite: zero quality-dimension findings on
    # complete, hand-shaped projects, except the corpus-declared known
    # false positives - visible, reasoned, and counted by the harness.
    # Anything NEW fails here with its check and location named.
    known = load_known_fps()
    phrases = load_phrases()
    for slug in CONTROLS:
        root = CORPUS_DIR / slug
        findings = [
            f
            for f in scan(root, phrases, provenance=False)
            if CHECK_CATEGORIES.get(f.check) != "ai-integrity"
        ]
        unexpected = [f for f in findings if (f.check, slug) not in known]
        assert unexpected == [], f"false positive(s) on {slug}: " + "; ".join(
            f"{f.check}@{f.path}:{f.line}" for f in unexpected
        )
        for finding in findings:
            assert known[(finding.check, slug)]  # every allowed FP has a reason


def test_known_fps_manifest_is_fail_closed():
    known = load_known_fps()
    assert ("debug-print", "clean_go") in known
    assert "advisory" in known[("debug-print", "clean_go")]
    # A malformed manifest is a configuration error, never a guessed report.
    import accuracy_benchmark as ab

    original = ab.CORPUS_MANIFEST
    try:
        bad = CORPUS_DIR / "corpus-bad-tmp.json"
        bad.write_text('{"schema": "wrong-v9"}', encoding="utf-8")
        ab.CORPUS_MANIFEST = bad
        with pytest.raises(CorpusError):
            load_known_fps()
        bad.unlink()
    finally:
        ab.CORPUS_MANIFEST = original


def test_full_run_exit_zero_and_report_shape(tmp_path):
    output, code = run_accuracy_benchmark(
        Path(__file__).resolve().parents[1] / "fixtures" / "bench",
        json_path=tmp_path / "report.json",
    )
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "accuracy-report-v1"
    assert report["violations"] == []
    # Every proven positive is a TP; the known FP stays visible.
    checks = report["checks"]
    assert checks["unused-declaration"]["tp"] >= 1
    assert checks["debug-print"]["known_fp"] == 1
    assert checks["debug-print"]["fp"] == 0
    # The honesty rule: the corpus proves accuracy per check, not per
    # project - exactly the columns the corpus has grown to five judged
    # rows (the round-10 tranche) may claim promotion evidence, and no
    # thinner column may. The pin is exact so silent drift fails here.
    PROMOTION_FOUR = {"csharp-async-void", "csharp-broad-catch", "rust-unwrap", "debug-print"}
    for check_id, cell in checks.items():
        if check_id in PROMOTION_FOUR:
            assert cell["promotion"] == "promotion-ready evidence", cell
        else:
            assert cell["promotion"].startswith("advisory-only"), cell
    # The table renders the verdict column and the known-FP section.
    assert "advisory-only" in output
    assert "Known false positives" in output


def test_report_records_dataset_and_engine_versions(tmp_path):
    import accuracy_benchmark as ab

    report = build_report({}, [], ab._git_describe())
    assert report["dataset_version"] == report["engine_version"]
    assert report["dataset_version"] not in ("", None)
    assert report["promotion_rule"]["min_precision"] == 0.8
