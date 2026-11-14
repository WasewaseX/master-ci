"""Contract tests for the quality ratchet, schema version 2.

Covers the documented contract: a committed baseline freezes per-project
per-check finding counts, any increase is a regression (exit 1), paying
debt is an improvement, adopting a catalog that knows more checks is a
conscious re-baseline, and a baseline that cannot be interpreted fails
closed (exit 2) - never a silent pass. Version 2 adds the identity layer
and the roadmap P2 acceptance case: swapping one old finding for a new
one under the same rule reports one resolved and one new identity at a
flat count, with the count verdict still OK (churn is reported, never
gating). A version 1 floor predates the identity layer and is refused by
name. The ratchet counts what the scan reports before --baseline
fingerprint suppression, so accepted debt cannot hide a regression.
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
from slop_identities import identity_string, is_well_formed  # noqa: E402
from slop_model import Finding  # noqa: E402

MOD_PATH = SCRIPTS / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

DEAD_HELPER = {"app.py": "def orphan_helper():\n    return 42\n"}

SWAP_CHECK = "bloat-long-file"


def _finding(check: str, path: str = "sample.py", line: int = 1, message: str = "m") -> Finding:
    return Finding(path=path, line=line, check=check, message=message, weight=1)


def _tree(tmp_path: Path, files: dict | None = None) -> Path:
    for name, text in (files or DEAD_HELPER).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _monorepo(tmp_path: Path) -> Path:
    """A scan root whose svc/ segment holds a node manifest - a real nested project."""
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "package.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _floor_file(tmp_path: Path, payload: dict) -> Path:
    """A hand-written baseline file, for loader validation tests."""
    location = tmp_path / "floor.json"
    location.write_text(json.dumps(payload), encoding="utf-8")
    return location


# ---------------------------------------------------------------------------
# metrics / scan_state: what the ratchet freezes
# ---------------------------------------------------------------------------


def test_metrics_counts_per_project_per_check_sorted(tmp_path):
    """The floor is per-project per-check, both levels sorted deterministically."""
    _monorepo(tmp_path)
    findings = [
        _finding("b-check", "svc/one.py"),
        _finding("a-check", "svc/one.py"),
        _finding("b-check", "svc/two.py", line=2),
        _finding("b-check", "solo.py"),
    ]
    assert rt.metrics(findings, tmp_path) == {
        ".": {"b-check": 1},
        "svc": {"a-check": 1, "b-check": 2},
    }


def test_metrics_excludes_run_dependent_checks(tmp_path):
    """low-new-coverage depends on other jobs' artifacts; freezing it would compare environments."""
    findings = [_finding("unused-declaration"), _finding("low-new-coverage", path="notes.py")]
    assert rt.metrics(findings, tmp_path) == {".": {"unused-declaration": 1}}


def test_scan_state_identities_are_sorted_and_deduplicated(tmp_path):
    """Identity strings are the sorted, deduplicated serialization of identity().

    Two findings differing only by line share one identity - same rule,
    same file, same normalized message - while the count still says two.
    """
    _monorepo(tmp_path)
    same = _finding("dup-check", "svc/one.py", message="same text")
    other = _finding("other-check", "svc/two.py")
    twin = _finding("dup-check", "svc/one.py", line=9, message="same text")
    state = rt.scan_state([same, twin, other], tmp_path)
    assert state.counts == {"svc": {"dup-check": 2, "other-check": 1}}
    assert state.identities == {
        "svc": tuple(sorted({identity_string(same, "svc"), identity_string(other, "svc")}))
    }


# ---------------------------------------------------------------------------
# write_baseline: the v2 floor and its ledger
# ---------------------------------------------------------------------------


def test_write_baseline_deterministic_bytes(tmp_path):
    """Two recordings of one scan produce identical v2 bytes: indent 2, sorted keys, newline."""
    findings = [
        _finding("unused-declaration", "debt.py"),
        _finding("empty-test", "more.py", line=2),
    ]
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    counts_one = rt.write_baseline(first, findings)
    counts_two = rt.write_baseline(second, findings)
    assert first.read_bytes() == second.read_bytes()
    assert counts_one == counts_two == {".": {"empty-test": 1, "unused-declaration": 1}}
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["projects"]["."] == {
        "checks": {"empty-test": 1, "unused-declaration": 1},
        "identities": sorted([identity_string(f, ".") for f in findings]),
        "resolved_history": [],
    }


def test_write_baseline_ledger_tracks_resolution_then_releases_returns(tmp_path):
    """Re-recording carries resolved identities forward; a reported return leaves the ledger.

    This ledger is what lets the NEXT comparison name returned debt
    reintroduced instead of new.
    """
    one = _finding("empty-test", "one.py")
    two = _finding("empty-test", "two.py", line=2)
    floor = tmp_path / "floor.json"
    rt.write_baseline(floor, [one, two])
    rt.write_baseline(floor, [two])  # one.py fixed; the floor is consciously re-recorded
    recorded = json.loads(floor.read_text(encoding="utf-8"))["projects"]["."]
    assert recorded["resolved_history"] == [identity_string(one, ".")]
    rt.write_baseline(floor, [one, two])  # the debt returns and is acknowledged
    recorded = json.loads(floor.read_text(encoding="utf-8"))["projects"]["."]
    assert recorded["resolved_history"] == []


def test_write_baseline_over_unreadable_predecessor_starts_an_empty_ledger(tmp_path):
    """The write path is the escape hatch: it must succeed over garbage, ledger empty."""
    floor = tmp_path / "floor.json"
    floor.write_text("{not json", encoding="utf-8")
    counts = rt.write_baseline(floor, [_finding("empty-test", "one.py")])
    assert counts == {".": {"empty-test": 1}}
    payload = json.loads(floor.read_text(encoding="utf-8"))
    assert payload["projects"]["."]["resolved_history"] == []


# ---------------------------------------------------------------------------
# compare: the ratchet semantics, both dimensions
# ---------------------------------------------------------------------------


def test_compare_increase_is_regression():
    """Any per-project per-check increase is a regression the gate blocks."""
    result = rt.compare({".": {"empty-test": 2}}, rt.Baseline({".": {"empty-test": 1}}))
    assert result.regressions == {(".", "empty-test"): 1}
    assert not result.ok


def test_compare_decrease_is_improvement_and_stays_green():
    """Paying debt below the floor is celebrated and never blocks."""
    result = rt.compare({".": {"empty-test": 0}}, rt.Baseline({".": {"empty-test": 3}}))
    assert result.improvements == {(".", "empty-test"): 3}
    assert result.ok


def test_compare_equal_is_unchanged():
    """Stable floors are neither regression nor improvement."""
    current = {".": {"empty-test": 2, "ai-vocab": 1}}
    result = rt.compare(current, rt.Baseline(current))
    assert result.unchanged == 2
    assert result.ok


def test_compare_check_unknown_to_the_baseline_is_regression():
    """A check the baseline never met is a conscious re-baseline, not a silent widening."""
    result = rt.compare({"svc": {"ai-vocab": 2}}, rt.Baseline())
    assert result.regressions == {("svc", "ai-vocab"): 2}
    assert not result.ok


def test_compare_baseline_check_paid_to_zero_is_improvement():
    """A check the scan no longer reports is debt gone."""
    result = rt.compare({}, rt.Baseline({".": {"ai-vocab": 4}}))
    assert result.improvements == {(".", "ai-vocab"): 4}
    assert result.ok


def test_compare_reports_the_full_identity_verdict():
    """A ScanState current side yields new, resolved, unchanged and reintroduced buckets."""
    kept = _finding("empty-test", "one.py")
    gone = _finding("empty-test", "two.py", line=2)
    fresh = _finding("empty-test", "three.py", line=3)
    floor = rt.Baseline(
        {".": {"empty-test": 2}}, {".": (identity_string(kept, "."), identity_string(gone, "."))}
    )
    result = rt.compare(rt.scan_state([kept, fresh]), floor)
    verdict = result.identities["."]
    assert verdict.new == {identity_string(fresh, ".")}
    assert verdict.resolved == {identity_string(gone, ".")}
    assert verdict.unchanged == {identity_string(kept, ".")}
    assert not verdict.reintroduced
    assert result.ok


def test_compare_counts_only_current_skips_the_identity_verdict():
    """A plain count mapping cannot know current identities; the verdict is count-only.

    Pretending otherwise would report every baseline identity as
    resolved - this is the degraded shape the pre-integration call
    site passes.
    """
    floor = rt.Baseline({".": {"empty-test": 1}}, {".": (".|empty-test|one.py|fingerprint",)})
    result = rt.compare({".": {"empty-test": 1}}, floor)
    assert result.ok
    assert result.identities == {}


def test_roadmap_acceptance_swap_reports_churn_at_flat_counts(tmp_path):
    """The P2 acceptance test: swap one old finding for a new one under the same rule.

    Removing one old finding and introducing another in the same project
    leaves every per-check total unchanged, so the count verdict is OK -
    and the identity verdict must still report exactly one resolved and
    one new finding, the churn the count-only ratchet was blind to.
    """
    _monorepo(tmp_path)
    older = _finding(SWAP_CHECK, "svc/api/older.py", line=10, message="file spans 900 lines")
    stays = _finding(SWAP_CHECK, "svc/api/stays.py", line=20, message="file spans 800 lines")
    floor = tmp_path / "floor.json"
    rt.write_baseline(floor, [older, stays], tmp_path)
    fresh = _finding(SWAP_CHECK, "svc/api/fresh.py", line=30, message="file spans 700 lines")
    result = rt.compare(rt.scan_state([stays, fresh], tmp_path), rt.load_baseline(floor))
    assert result.ok
    assert result.regressions == {} and result.improvements == {}
    verdict = result.identities["svc"]
    assert verdict.new == {identity_string(fresh, "svc")}
    assert verdict.resolved == {identity_string(older, "svc")}
    assert verdict.unchanged == {identity_string(stays, "svc")}
    assert not verdict.reintroduced


def test_reintroduced_identity_fires_at_flat_counts(tmp_path):
    """Ledgered debt that returns under a flat count is reintroduced, not new.

    One ledgered finding swaps back in while the tracked one resolves:
    the per-check total stays at its floor, so the run is green, and
    only the identity verdict flags that the debt is returned.
    """
    one = _finding("empty-test", "one.py")
    two = _finding("empty-test", "two.py", line=2)
    floor = tmp_path / "floor.json"
    rt.write_baseline(floor, [one, two])
    rt.write_baseline(floor, [two])  # one.py resolved; the ledger remembers it
    result = rt.compare(rt.scan_state([one]), rt.load_baseline(floor))
    verdict = result.identities["."]
    assert verdict.reintroduced == {identity_string(one, ".")}
    assert not verdict.new  # returned debt is not new debt
    assert verdict.resolved == {identity_string(two, ".")}
    assert result.ok  # the count stayed at 1: only the identity verdict flags the swap


def test_returned_debt_is_regression_and_reintroduced(tmp_path):
    """Debt simply coming back breaks the count floor AND is named returned, not new."""
    one = _finding("empty-test", "one.py")
    two = _finding("empty-test", "two.py", line=2)
    floor = tmp_path / "floor.json"
    rt.write_baseline(floor, [one, two])
    rt.write_baseline(floor, [two])
    result = rt.compare(rt.scan_state([one, two]), rt.load_baseline(floor))
    assert not result.ok
    assert result.regressions == {(".", "empty-test"): 1}
    assert result.identities["."].reintroduced == {identity_string(one, ".")}


# ---------------------------------------------------------------------------
# load_baseline: fail-closed on every surprise
# ---------------------------------------------------------------------------


def test_load_unreadable_baseline_raises(tmp_path):
    """A missing file and a directory are both unreadable floors - fail closed."""
    with pytest.raises(rt.RatchetError, match="cannot be read"):
        rt.load_baseline(tmp_path / "absent.json")
    with pytest.raises(rt.RatchetError, match="cannot be read"):
        rt.load_baseline(tmp_path)


def test_load_unparseable_baseline_raises(tmp_path):
    """Broken JSON and non-object JSON are equally uninterpretable."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="not valid JSON"):
        rt.load_baseline(broken)
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(rt.RatchetError, match="JSON object"):
        rt.load_baseline(array)


def test_load_version_1_baseline_refuses_by_name(tmp_path):
    """A v1 floor predates the identity layer; the refusal names version 1 and version 2."""
    floor = _floor_file(tmp_path, {"version": 1, "projects": {".": {"checks": {}}}})
    with pytest.raises(rt.RatchetError) as refused:
        rt.load_baseline(floor)
    message = str(refused.value)
    assert "version 1" in message and "version 2" in message
    assert "--write-quality-baseline" in message


def test_load_wrong_schema_version_raises(tmp_path):
    """Any foreign schema version is refused the same way; the floor is never guessed at."""
    floor = _floor_file(tmp_path, {"version": 99, "projects": {".": {"checks": {}}}})
    with pytest.raises(rt.RatchetError, match="schema version"):
        rt.load_baseline(floor)


def test_load_malformed_projects_object_raises(tmp_path):
    """Projects must be an object of per-project objects, each with a checks object."""
    projects_list = _floor_file(tmp_path, {"version": 2, "projects": []})
    with pytest.raises(rt.RatchetError, match="projects"):
        rt.load_baseline(projects_list)
    checks_missing = _floor_file(tmp_path, {"version": 2, "projects": {".": {"identities": []}}})
    with pytest.raises(rt.RatchetError, match="checks"):
        rt.load_baseline(checks_missing)


def test_load_non_integer_or_negative_count_raises(tmp_path):
    """Counts are non-negative integers; anything else is not a floor."""
    bad = _floor_file(tmp_path, {"version": 2, "projects": {".": {"checks": {"ai-vocab": "many"}}}})
    with pytest.raises(rt.RatchetError, match="non-negative integers"):
        rt.load_baseline(bad)
    negative = _floor_file(
        tmp_path, {"version": 2, "projects": {".": {"checks": {"ai-vocab": -1}}}}
    )
    with pytest.raises(rt.RatchetError, match="non-negative integers"):
        rt.load_baseline(negative)


def test_load_unknown_check_ids_raise(tmp_path):
    """Unknown check ids mean the catalog changed - and the contract reaches into identities."""
    in_checks = _floor_file(
        tmp_path, {"version": 2, "projects": {".": {"checks": {"retired-check-x": 3}}}}
    )
    with pytest.raises(rt.RatchetError, match="does not know"):
        rt.load_baseline(in_checks)
    in_identity = _floor_file(
        tmp_path,
        {
            "version": 2,
            "projects": {".": {"checks": {}, "identities": [".|retired-check-x|debt.py|0f"]}},
        },
    )
    with pytest.raises(rt.RatchetError, match="does not know"):
        rt.load_baseline(in_identity)


def test_load_malformed_identities_raise(tmp_path):
    """Identities are strings that split into four fields; anything else fails closed."""
    non_strings = _floor_file(
        tmp_path, {"version": 2, "projects": {".": {"checks": {}, "identities": [3]}}}
    )
    with pytest.raises(rt.RatchetError, match="list of strings"):
        rt.load_baseline(non_strings)
    truncated = _floor_file(
        tmp_path, {"version": 2, "projects": {".": {"checks": {}, "identities": ["one-field"]}}}
    )
    with pytest.raises(rt.RatchetError, match="identity fields"):
        rt.load_baseline(truncated)


# ---------------------------------------------------------------------------
# render: the delta tables
# ---------------------------------------------------------------------------


def test_render_regressions_before_improvements():
    """Regression rows lead the count table, improvements follow - the fix-first ordering."""
    result = rt.RatchetResult(
        regressions={("svc", "zzz-check"): 2}, improvements={(".", "aaa-check"): 1}, unchanged=1
    )
    counts = {"svc": {"zzz-check": 3}, ".": {"aaa-check": 0}}
    floors = rt.Baseline({"svc": {"zzz-check": 1}, ".": {"aaa-check": 1}})
    table = [
        line for line in rt.render(counts, floors, result) if line.startswith("|") and "`" in line
    ]
    assert table[0].startswith("| svc | `zzz-check`")
    assert "+2" in table[0]
    assert table[1].startswith("| . | `aaa-check`")
    assert "-1" in table[1]


def test_render_caps_the_count_table():
    """The table caps at MAX_DELTA_ROWS and says how many rows it hid."""
    checks = {f"check-{index:02d}": 1 for index in range(rt.MAX_DELTA_ROWS + 5)}
    result = rt.RatchetResult(
        regressions={(".", check): 1 for check in checks}, improvements={}, unchanged=0
    )
    lines = rt.render({".": checks}, rt.Baseline(), result)
    more = [line for line in lines if "more" in line]
    assert more and "5" in more[0]


def test_render_identity_table_reports_the_churn():
    """The identity table carries the Project/Check columns and one row per check."""
    kept = identity_string(_finding("empty-test", "one.py"), ".")
    gone = identity_string(_finding("empty-test", "two.py", line=2), ".")
    floor = rt.Baseline({".": {"empty-test": 2}}, {".": (kept, gone)})
    state = rt.scan_state(
        [_finding("empty-test", "one.py"), _finding("empty-test", "three.py", line=3)]
    )
    result = rt.compare(state, floor)
    lines = rt.render(state, floor, result)
    assert "| Project | Check | New | Resolved | Unchanged | Reintroduced |" in lines
    assert "| . | `empty-test` | 1 | 1 | 1 | 0 |" in lines


def test_render_counts_only_comparison_names_the_gap():
    """Counts-only wiring says the ledger was not evaluated - a silent gap would look clean."""
    floor = rt.Baseline({".": {"empty-test": 1}}, {".": (".|empty-test|one.py|fingerprint",)})
    result = rt.compare({".": {"empty-test": 1}}, floor)
    lines = rt.render({".": {"empty-test": 1}}, floor, result)
    assert any("not evaluated" in line for line in lines)


# ---------------------------------------------------------------------------
# CLI: the exit-code contract end to end, through the unmodified slop_cli wiring
# ---------------------------------------------------------------------------


def test_cli_write_quality_baseline_exits_zero(tmp_path):
    """The write path records a v2 floor with the root project's counts and identities."""
    root = _tree(tmp_path)
    rc = mod.main([str(root), "--write-quality-baseline", str(tmp_path / "floor.json")])
    assert rc == 0
    data = json.loads((tmp_path / "floor.json").read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["projects"]["."]["checks"] == {"unused-declaration": 1}
    identities = data["projects"]["."]["identities"]
    assert len(identities) == 1 and is_well_formed(identities[0])


def test_cli_write_then_check_passes(tmp_path):
    """A committed floor passes its own scan: the write/check round trip is stable."""
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "floor.json")]) == 0
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "floor.json")]) == 0


def test_cli_regression_exits_one(tmp_path):
    """A new finding above the recorded floor fails the run with exit 1."""
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "floor.json")]) == 0
    (root / "more.py").write_text("def another_orphan():\n    return 7\n", encoding="utf-8")
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "floor.json")]) == 1


def test_cli_paid_debt_passes_with_improvement(tmp_path):
    """Removing the recorded debt entirely keeps the run green - improvements compound."""
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "floor.json")]) == 0
    (root / "app.py").unlink()
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "floor.json")]) == 0


def test_cli_missing_baseline_fails_closed(tmp_path):
    """A requested floor that cannot be read is exit 2, never a silent pass."""
    root = _tree(tmp_path)
    assert mod.main([str(root), "--quality-baseline", str(tmp_path / "absent.json")]) == 2


def test_cli_corrupt_baseline_fails_closed(tmp_path):
    """Garbage instead of a floor is exit 2 - the gate never guesses."""
    root = _tree(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("nope", encoding="utf-8")
    assert mod.main([str(root), "--quality-baseline", str(bad)]) == 2


def test_cli_ratchet_counts_before_fingerprint_suppression(tmp_path):
    """Accepted debt cannot hide a regression: the ratchet reads pre-suppression findings.

    The fingerprint baseline suppresses both declarations from the
    report; the ratchet still sees the second one appear and fails.
    """
    root = _tree(tmp_path)
    assert mod.main([str(root), "--write-baseline", str(tmp_path / "fp.json")]) == 0
    assert mod.main([str(root), "--write-quality-baseline", str(tmp_path / "floor.json")]) == 0
    (root / "more.py").write_text("def another_orphan():\n    return 7\n", encoding="utf-8")
    rc = mod.main(
        [
            str(root),
            "--baseline",
            str(tmp_path / "fp.json"),
            "--quality-baseline",
            str(tmp_path / "floor.json"),
        ]
    )
    assert rc == 1
