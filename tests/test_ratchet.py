"""Contract tests for the quality ratchet (v2.2.0).

Covers the documented contract: a committed baseline freezes per-check
finding counts, any increase is a regression (exit 1), paying debt is an
improvement, adopting a catalog that knows more checks is a conscious
re-baseline, and a baseline that cannot be interpreted fails closed
(exit 2) - never a silent pass. The ratchet counts what the scan reports
before --baseline fingerprint suppression, so accepted debt cannot hide
a regression.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import slop_ratchet as rt  # noqa: E402
from slop_model import Finding  # noqa: E402

MOD_PATH = SCRIPTS / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

DEAD_HELPER = {"app.py": "def orphan_helper():\n    return 42\n"}


def _finding(check: str, path: str = "a.py", line: int = 1) -> Finding:
    return Finding(path=path, line=line, check=check, message="m", weight=1)


def _tree(tmp_path: Path, files: dict | None = None) -> Path:
    for name, text in (files or DEAD_HELPER).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# metrics: what the ratchet freezes
# ---------------------------------------------------------------------------


def test_metrics_counts_per_check_sorted():
    findings = [_finding("b-check"), _finding("a-check"), _finding("b-check", line=2)]
    assert rt.metrics(findings) == {"a-check": 1, "b-check": 2}


def test_metrics_excludes_run_dependent_checks():
    findings = [_finding("unused-declaration"), _finding("low-new-coverage")]
    assert rt.metrics(findings) == {"unused-declaration": 1}


def test_write_baseline_deterministic_bytes(tmp_path):
    findings = [_finding("unused-declaration"), _finding("empty-test", line=2)]
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    counts_one = rt.write_baseline(first, findings)
    counts_two = rt.write_baseline(second, findings)
    assert first.read_bytes() == second.read_bytes()
    assert counts_one == counts_two == {"empty-test": 1, "unused-declaration": 1}


# ---------------------------------------------------------------------------
# compare: the ratchet semantics
# ---------------------------------------------------------------------------


def test_compare_increase_is_regression():
    result = rt.compare({"empty-test": 2}, {"empty-test": 1})
    assert result.regressions == {"empty-test": 1}
    assert not result.ok


def test_compare_decrease_is_improvement_and_stays_green():
    result = rt.compare({"empty-test": 0}, {"empty-test": 3})
    assert result.improvements == {"empty-test": 3}
    assert result.ok


def test_compare_equal_is_unchanged():
    result = rt.compare({"empty-test": 2, "ai-vocab": 1}, {"empty-test": 2, "ai-vocab": 1})
    assert result.unchanged == 2
    assert result.ok


def test_compare_check_unknown_to_baseline_is_regression():
    result = rt.compare({"ai-vocab": 2}, {})
    assert result.regressions == {"ai-vocab": 2}
    assert not result.ok


def test_compare_baseline_check_paid_to_zero_is_improvement():
    result = rt.compare({}, {"ai-vocab": 4})
    assert result.improvements == {"ai-vocab": 4}
    assert result.ok


# ---------------------------------------------------------------------------
# load_baseline: fail-closed on every surprise
# ---------------------------------------------------------------------------


def test_load_missing_baseline_raises(tmp_path):
    with pytest.raises(rt.RatchetError, match="cannot be read"):
        rt.load_baseline(tmp_path / "absent.json")


def test_load_baseline_directory_raises(tmp_path):
    with pytest.raises(rt.RatchetError, match="cannot be read"):
        rt.load_baseline(tmp_path)


def test_load_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="not valid JSON"):
        rt.load_baseline(bad)


def test_load_non_object_baseline_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="JSON object"):
        rt.load_baseline(bad)


def test_load_wrong_schema_version_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 99, "checks": {}}), encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="schema version"):
        rt.load_baseline(bad)


def test_load_checks_not_an_object_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "checks": []}), encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="checks"):
        rt.load_baseline(bad)


def test_load_non_integer_count_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "checks": {"ai-vocab": "many"}}), encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="non-negative integers"):
        rt.load_baseline(bad)


def test_load_negative_count_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "checks": {"ai-vocab": -1}}), encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="non-negative integers"):
        rt.load_baseline(bad)


def test_load_unknown_check_id_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "checks": {"retired-check-x": 3}}), encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="does not know"):
        rt.load_baseline(bad)


# ---------------------------------------------------------------------------
# render: the delta table
# ---------------------------------------------------------------------------


def test_render_regressions_before_improvements():
    result = rt.RatchetResult(
        regressions={"zzz-check": 2}, improvements={"aaa-check": 1}, unchanged=1
    )
    lines = rt.render({"zzz-check": 3, "aaa-check": 0}, {"zzz-check": 1, "aaa-check": 1}, result)
    table = [line for line in lines if line.startswith("| `")]
    assert table[0].startswith("| `zzz-check`")
    assert "+2" in table[0]
    assert table[1].startswith("| `aaa-check`")
    assert "-1" in table[1]


def test_render_caps_the_table():
    regressions = {f"check-{i:02d}": 1 for i in range(rt.MAX_DELTA_ROWS + 5)}
    result = rt.RatchetResult(regressions=regressions, improvements={}, unchanged=0)
    lines = rt.render(regressions, {}, result)
    more = [line for line in lines if "more" in line]
    assert more and str(5) in more[0]


# ---------------------------------------------------------------------------
# CLI: the exit-code contract end to end
# ---------------------------------------------------------------------------


def test_cli_write_quality_baseline_exits_zero(tmp_path):
    root = _tree(tmp_path)
    rc = mod.main([str(root), "--write-quality-baseline", str(tmp_path / "b.json")])
    assert rc == 0
    data = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["checks"] == {"unused-declaration": 1}


def test_cli_write_then_check_passes(tmp_path):
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "b.json")]) == 0
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "b.json")]) == 0


def test_cli_regression_exits_one(tmp_path):
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "b.json")]) == 0
    (root / "more.py").write_text("def another_orphan():\n    return 7\n", encoding="utf-8")
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "b.json")]) == 1


def test_cli_paid_debt_passes_with_improvement(tmp_path):
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "b.json")]) == 0
    (root / "app.py").unlink()
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "b.json")]) == 0


def test_cli_missing_baseline_fails_closed(tmp_path):
    root = _tree(tmp_path)
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "absent.json")]) == 2


def test_cli_corrupt_baseline_fails_closed(tmp_path):
    root = _tree(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("nope", encoding="utf-8")
    assert mod.main([str(root), "--quality-baseline", str(bad)]) == 2


def test_cli_ratchet_counts_before_fingerprint_suppression(tmp_path):
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-baseline", str(tmp_path / "fp.json")]) == 0
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "b.json")]) == 0
    (root / "more.py").write_text("def another_orphan():\n    return 7\n", encoding="utf-8")
    # The fingerprint baseline suppresses both declarations from the
    # report, but the ratchet still sees the second one appear.
    rc = mod.main(
        [
            str(root),
            "--baseline",
            str(tmp_path / "fp.json"),
            "--quality-baseline",
            str(tmp_path / "b.json"),
        ]
    )
    assert rc == 1
