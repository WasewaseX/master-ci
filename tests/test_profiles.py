"""Contract tests for analyzer profiles (v2.17, P2).

Covers the profile contract documented in scripts/slop_profiles.py:
- DEFAULT_PROFILE is a proven no-op on a real findings list,
- severity moves are downgrade-only: blocking->advisory works,
advisory->blocking is rejected at load time, "off" suppresses,
- scope exclusions silence findings with a visible Suppression reason,
keyed by check id or "*", matched on the finding path or the caller's
rel_path,
- load_profile fails closed: malformed JSON/YAML, unknown check ids,
illegal severities, unknown top-level keys, bad budget ids and
non-positive budgets all raise ValueError naming the offender,
- a budget override flows end-to-end through density_findings and
flips a verdict at a lower threshold,
- the identity contract: DEFAULT_PROFILE leaves the shipped constants
(complexity density 300, duplication density 5.0, coupling fan-out
30, hotspot risk 1200) in charge, so a silent retune fails loudly.
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from slop_coupling import COUPLING_FAN_OUT_BUDGET  # noqa: E402
from slop_density import (  # noqa: E402
    COMPLEXITY_DENSITY_BUDGET,
    DUP_DENSITY_BUDGET,
    DensityFacts,
    density_findings,
)
from slop_hotspot import HOTSPOT_RISK_BUDGET  # noqa: E402
from slop_model import Finding  # noqa: E402
from slop_profiles import (  # noqa: E402
    DEFAULT_PROFILE,
    ProfileSpec,
    apply_profile,
    budget_override,
    load_profile,
    shipped_budget,
)
from slop_reporting import blocking_score  # noqa: E402


def mixed_findings() -> list[Finding]:
    """A small mixed report: two blocking defects, two advisory budgets."""
    return [
        Finding(path="src/api.py", line=3, check="bare-except", message="m", weight=2),
        Finding(path="src/other.py", line=8, check="god-function", message="m", weight=2),
        Finding(path="tests/gen_x.py", line=1, check="ai-vocab", message="m", weight=1),
        Finding(path="src/verbose.py", line=1, check="bloat-long-file", message="m", weight=2),
    ]


def write_profile(tmp_path: Path, payload: Mapping[str, object]) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(dict(payload)), encoding="utf-8")
    return path


# --- application: the default profile and the downgrade-only moves -------


def test_default_profile_is_a_no_op_on_real_findings() -> None:
    findings = mixed_findings()
    kept, suppressed = apply_profile(findings, DEFAULT_PROFILE, ".")
    assert kept == findings
    assert suppressed == []
    assert [f.advisory for f in kept] == [False, True, False, True]


def test_blocking_to_advisory_downgrade_keeps_the_check_visible() -> None:
    profile = ProfileSpec(
        name="relaxed", description="noise down", severities={"bare-except": "advisory"}
    )
    findings = mixed_findings()
    kept, suppressed = apply_profile(findings, profile, ".")
    assert suppressed == []
    downgraded = [f for f in kept if f.check == "bare-except"][0]
    assert downgraded.advisory is True
    assert downgraded.weight == 2  # the check still reports, at advisory grade
    assert findings[0].advisory is False  # the input report is never mutated
    assert blocking_score(kept) < blocking_score(findings)


def test_off_severity_suppresses_with_a_visible_reason() -> None:
    profile = ProfileSpec(name="quiet", description="noise off", severities={"ai-vocab": "off"})
    kept, suppressed = apply_profile(mixed_findings(), profile, ".")
    assert [f.check for f in kept] == ["bare-except", "god-function", "bloat-long-file"]
    assert len(suppressed) == 1
    assert suppressed[0].check == "ai-vocab"
    assert suppressed[0].path == "tests/gen_x.py"
    assert suppressed[0].reason == "profile 'quiet': check severity off"


def test_scope_exclusion_suppresses_with_the_pattern_as_reason() -> None:
    profile = ProfileSpec(
        name="tests", description="test trees", scope_excludes={"ai-vocab": ("tests/**",)}
    )
    kept, suppressed = apply_profile(mixed_findings(), profile, ".")
    assert not [f for f in kept if f.check == "ai-vocab"]
    assert len(kept) == 3
    assert len(suppressed) == 1
    assert suppressed[0].check == "ai-vocab"
    assert suppressed[0].path == "tests/gen_x.py"
    assert suppressed[0].reason == "profile 'tests': scope 'tests/**'"


def test_star_scope_key_matches_every_check() -> None:
    profile = ProfileSpec(
        name="trees", description="whole trees", scope_excludes={"*": ("tests/**",)}
    )
    kept, suppressed = apply_profile(mixed_findings(), profile, ".")
    assert [f.path for f in kept] == ["src/api.py", "src/other.py", "src/verbose.py"]
    assert [s.check for s in suppressed] == ["ai-vocab"]


def test_scope_matches_the_caller_rel_path_for_repo_anchored_findings() -> None:
    """Repo-level findings anchor at '.', so the caller's rel_path scopes them."""
    profile = ProfileSpec(
        name="generated", description="generated trees", scope_excludes={"*": ("generated/**",)}
    )
    repo_finding = Finding(path=".", line=1, check="complexity-density", message="m", weight=2)
    kept, suppressed = apply_profile([repo_finding], profile, "generated/pb2.py")
    assert kept == []
    assert len(suppressed) == 1
    assert suppressed[0].path == "."


# --- load_profile: fail closed on every malformed or unknown lever -------


def test_unknown_check_id_fails_closed_naming_the_registry(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "severities": {"not-a-check": "advisory"}})
    with pytest.raises(ValueError) as excinfo:
        load_profile(path)
    assert "not-a-check" in str(excinfo.value)
    assert "CHECK_CATEGORIES" in str(excinfo.value)


def test_malformed_json_profile_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"name": "x", ', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_profile(path)


def test_malformed_yaml_profile_raises_value_error(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed\n  severities: {", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_profile(path)


def test_illegal_severity_value_raises(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "severities": {"ai-vocab": "error"}})
    with pytest.raises(ValueError, match="illegal severity") as excinfo:
        load_profile(path)
    assert "'ai-vocab'" in str(excinfo.value)
    assert "advisory" in str(excinfo.value) and "off" in str(excinfo.value)


def test_advisory_to_blocking_escalation_is_rejected(tmp_path: Path) -> None:
    """The forbidden direction: a profile cannot re-grade advisory as blocking."""
    path = write_profile(tmp_path, {"name": "x", "severities": {"god-function": "blocking"}})
    with pytest.raises(ValueError, match="downgrade-only"):
        load_profile(path)


def test_unknown_top_level_key_fails_closed(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "severity": {"ai-vocab": "off"}})
    with pytest.raises(ValueError, match="unknown key"):
        load_profile(path)


def test_budget_for_a_check_without_budget_semantics_fails_closed(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "budgets": {"ai-vocab": 5}})
    with pytest.raises(ValueError) as excinfo:
        load_profile(path)
    assert "'ai-vocab'" in str(excinfo.value)
    assert "high-coupling" in str(excinfo.value)  # the valid budget id set is named


def test_non_positive_budget_fails_closed(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "budgets": {"complexity-density": 0}})
    with pytest.raises(ValueError, match="positive"):
        load_profile(path)


def test_unsupported_scope_glob_fails_closed(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"name": "x", "scope_excludes": {"ai-vocab": ["[unclosed"]}})
    with pytest.raises(ValueError, match="not a supported pattern"):
        load_profile(path)


def test_yaml_profile_loads_when_pyyaml_is_present(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "profile.yaml"
    path.write_text(
        "name: yaml-profile\ndescription: docs\nseverities:\n  ai-vocab: advisory\n",
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile.name == "yaml-profile"
    assert profile.severities == {"ai-vocab": "advisory"}


# --- budgets: the end-to-end override and the shipped identity -----------


def over_budget_facts() -> DensityFacts:
    """250/1000 complexity and 4% duplication: clean at shipped budgets."""
    return DensityFacts(
        files=4,
        exec_lines=1000,
        complexity=250,
        duplicated=40,
        per_lang={"py": (1000, 250)},
        worst_files=(("dense.py", 250),),
        worst_line=None,
    )


def test_density_budget_override_flips_a_verdict_end_to_end() -> None:
    facts = over_budget_facts()
    assert density_findings(facts) == []  # 250 < 300 and 4% < 5.0% at shipped budgets
    profile = ProfileSpec(
        name="tight",
        description="tighter bars",
        budgets={"complexity-density": 200.0, "duplication-density": 3.0},
    )
    budgets = {
        check: value
        for check in ("complexity-density", "duplication-density")
        if (value := budget_override(profile, check)) is not None
    }
    flipped = density_findings(facts, budgets)
    assert [f.check for f in flipped] == ["complexity-density", "duplication-density"]
    assert "(budget 200.0)" in flipped[0].message
    assert "(budget 3.0%)" in flipped[1].message


def test_density_default_budget_mapping_is_a_proven_no_op() -> None:
    facts = DensityFacts(
        files=4,
        exec_lines=1000,
        complexity=400,
        duplicated=60,
        per_lang={"py": (1000, 400)},
        worst_files=(("dense.py", 400),),
        worst_line=None,
    )
    shipped = density_findings(facts)
    assert [f.check for f in shipped] == ["complexity-density", "duplication-density"]
    assert f"(budget {COMPLEXITY_DENSITY_BUDGET})" in shipped[0].message
    assert f"(budget {DUP_DENSITY_BUDGET}%)" in shipped[1].message
    assert shipped == density_findings(facts, {})  # an empty override mapping changes nothing


def test_default_profile_identity_matches_shipped_constants() -> None:
    assert DEFAULT_PROFILE.name == "default"
    assert DEFAULT_PROFILE.severities == {}
    assert DEFAULT_PROFILE.scope_excludes == {}
    assert DEFAULT_PROFILE.budgets == {}
    for check in ("complexity-density", "duplication-density", "high-coupling", "hotspot-risk"):
        assert budget_override(DEFAULT_PROFILE, check) is None  # the shipped constant rules
    assert shipped_budget("complexity-density") == COMPLEXITY_DENSITY_BUDGET == 300
    assert shipped_budget("duplication-density") == DUP_DENSITY_BUDGET == 5.0
    assert shipped_budget("high-coupling") == COUPLING_FAN_OUT_BUDGET == 30
    assert shipped_budget("hotspot-risk") == HOTSPOT_RISK_BUDGET == 1200


# --- CLI and scan wiring (integration) --------------------------------------


def test_cli_rejects_an_unhonorable_profile_fail_closed(tmp_path, capsys):
    """--profile with a profile the loader refuses exits 2 before scanning."""
    import slop_cli

    bad = tmp_path / "profile.json"
    bad.write_text(
        json.dumps({"name": "typo", "severities": {"no-such-check": "advisory"}}),
        encoding="utf-8",
    )
    assert slop_cli.main([str(tmp_path), "--profile", str(bad)]) == 2
    assert "profile rejected" in capsys.readouterr().err


def test_scan_passes_profile_budgets_through_to_the_density_verdict(tmp_path):
    """scan(budgets=...) is the pass-through the CLI feeds; None is a no-op."""
    from ai_slop_detector import scan

    assignments = "\n".join(f"VALUE{i} = {i}" for i in range(130))
    body = "def pick(a, b):\n    if a > b:\n        return a\n    return b\n"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name in ("m0.py", "m1.py"):
        (pkg / name).write_text(assignments + "\n" + body, encoding="utf-8")
    from slop_files import load_phrases

    phrases = load_phrases()
    base = scan(tmp_path, phrases, provenance=False)
    assert not [f for f in base if f.check == "complexity-density"]
    tight = scan(tmp_path, phrases, provenance=False, budgets={"complexity-density": 1})
    assert [f for f in tight if f.check == "complexity-density"]


# --- v2.18, audit finding B: every accepted budget is functional ---------

HUB_IMPORTS = "\n".join(f"import leaf{i}" for i in range(7))


def _hub_repo(tmp_path: Path) -> None:
    """Seven leaves plus a hub: 8 graph nodes, hub fan-out 7.

    Under the shipped budget 30 the hub is silent; under a profile's 3
    it must report - the profile budget the v2.17 engines ignored.
    """
    for i in range(7):
        (tmp_path / f"leaf{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    (tmp_path / "hub.py").write_text(HUB_IMPORTS + "\n\nVALUE = 0\n", encoding="utf-8")


def test_scan_passes_profile_budgets_through_to_the_coupling_verdict(tmp_path):
    """The high-coupling budget is no longer accepted-then-ignored."""
    from ai_slop_detector import scan
    from slop_files import load_phrases

    _hub_repo(tmp_path)
    phrases = load_phrases()
    base = scan(tmp_path, phrases, provenance=False)
    assert not [f for f in base if f.check == "high-coupling"]
    tight = scan(tmp_path, phrases, provenance=False, budgets={"high-coupling": 3})
    hits = [f for f in tight if f.check == "high-coupling"]
    assert hits and "budget 3" in hits[0].message


def test_profile_budgets_flow_through_the_loaded_profile_document(tmp_path):
    """End to end: a real profile JSON flips the coupling verdict."""
    from ai_slop_detector import scan
    from slop_files import load_phrases
    from slop_profiles import load_profile

    _hub_repo(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"name": "tight", "budgets": {"high-coupling": 3}}', encoding="utf-8")
    profile = load_profile(profile_path)
    phrases = load_phrases()
    tight = scan(tmp_path, phrases, provenance=False, budgets=dict(profile.budgets))
    hits = [f for f in tight if f.check == "high-coupling"]
    assert hits and "budget 3" in hits[0].message


def test_hotspot_engine_honors_the_profile_budget(tmp_path):
    """The hotspot-risk budget is no longer accepted-then-ignored."""
    from slop_coupling import measure_coupling
    from slop_density import measure
    from slop_hotspot import check_hotspot_metrics

    assignments = "\n".join(f"VALUE{i} = {i}" for i in range(130))
    body = "def pick(a, b):\n    if a > b:\n        return a\n    return b\n"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name in ("m0.py", "m1.py"):
        (pkg / name).write_text(assignments + "\n" + body, encoding="utf-8")
    files = sorted(tmp_path.rglob("*.py"))
    facts = measure(tmp_path, files, [])
    coupling = measure_coupling(tmp_path, files, [])
    churn = {"pkg/m0.py": 1}
    base = check_hotspot_metrics(facts, coupling, churn, {})
    assert base == []  # risk 2 sits far under the shipped 1200
    tight = check_hotspot_metrics(facts, coupling, churn, {}, budgets={"hotspot-risk": 1})
    assert tight and "budget 1" in tight[0].message
    assert tight[0].message != ""


def test_none_and_empty_budgets_keep_shipped_verdicts_byte_identical(tmp_path):
    """Finding B's no-behavior-change guarantee for profile-less callers."""
    from ai_slop_detector import scan
    from slop_coupling import check_coupling_metrics, measure_coupling
    from slop_files import load_phrases

    _hub_repo(tmp_path)
    files = sorted(tmp_path.rglob("*.py"))
    coupling = measure_coupling(tmp_path, files, [])
    phrases = load_phrases()
    no_arg = check_coupling_metrics(tmp_path, files, [], facts=coupling)
    none_budgets = check_coupling_metrics(tmp_path, files, [], facts=coupling, budgets=None)
    empty_budgets = check_coupling_metrics(tmp_path, files, [], facts=coupling, budgets={})
    assert [f.message for f in no_arg] == [f.message for f in none_budgets]
    assert [f.message for f in no_arg] == [f.message for f in empty_budgets]
    # The shipped budget number, not a profile's, renders in the message.
    assert not [f for f in scan(tmp_path, phrases, provenance=False) if f.check == "high-coupling"]
