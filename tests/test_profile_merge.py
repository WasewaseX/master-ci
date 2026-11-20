"""The profile-inheritance contracts (roadmap A4, round-18).

Merge precedence is the feature: severities and budgets are child-wins
per key (the most specific profile that names a key decides), scope
exclusions are the union across the whole chain (a child can never
re-include what a parent silenced), an empty chain is the shipped
default, and every document is loaded fail-closed before any merge.
The merged output is re-validated defensively - a constructed spec
that dodges the loader cannot dodge the registries.
"""

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))

from slop_profile_merge import (  # noqa: E402
    _validate_merged,
    load_profile_chain,
    main,
    merge_profiles,
)
from slop_profiles import DEFAULT_PROFILE, ProfileSpec  # noqa: E402


def _write_profile(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- merge precedence --------------------------------------------------------


def test_empty_chain_is_the_default_profile():
    assert merge_profiles([]) is DEFAULT_PROFILE


def test_child_severity_overrides_base():
    base = ProfileSpec(name="base", severities={"ai-vocab": "off"})
    child = ProfileSpec(name="child", severities={"ai-vocab": "advisory"})
    merged = merge_profiles([base, child])
    assert merged.severities["ai-vocab"] == "advisory"


def test_child_off_beats_parent_advisory():
    base = ProfileSpec(name="base", severities={"ai-vocab": "advisory"})
    child = ProfileSpec(name="child", severities={"ai-vocab": "off"})
    assert merge_profiles([base, child]).severities["ai-vocab"] == "off"


def test_unnamed_keys_inherit_the_nearest_ancestor():
    base = ProfileSpec(name="base", severities={"ai-vocab": "off", "ai-prose": "off"})
    child = ProfileSpec(name="child", severities={"ai-vocab": "advisory"})
    merged = merge_profiles([base, child])
    assert merged.severities["ai-vocab"] == "advisory"  # child wins
    assert merged.severities["ai-prose"] == "off"  # inherited


def test_scope_excludes_are_the_union_a_child_cannot_reinclude():
    base = ProfileSpec(name="base", scope_excludes={"*": ("generated/**",)})
    child = ProfileSpec(name="child", scope_excludes={"*": ("vendor/**",)})
    merged = merge_profiles([base, child])
    assert merged.scope_excludes["*"] == ("generated/**", "vendor/**")


def test_scope_union_deduplicates_but_keeps_order():
    base = ProfileSpec(name="base", scope_excludes={"*": ("a/**", "b/**")})
    child = ProfileSpec(name="child", scope_excludes={"*": ("b/**", "c/**")})
    assert merge_profiles([base, child]).scope_excludes["*"] == ("a/**", "b/**", "c/**")


def test_child_budget_overrides_base():
    base = ProfileSpec(name="base", budgets={"complexity-density": 400.0})
    child = ProfileSpec(name="child", budgets={"complexity-density": 350.0})
    assert merge_profiles([base, child]).budgets["complexity-density"] == 350.0


def test_three_level_chain_full_precedence():
    base = ProfileSpec(
        name="org",
        severities={"ai-vocab": "off"},
        scope_excludes={"*": ("gen/**",)},
        budgets={"complexity-density": 400.0},
    )
    project = ProfileSpec(
        name="proj", severities={"ai-prose": "off"}, budgets={"duplication-density": 8.0}
    )
    local = ProfileSpec(name="local", severities={"ai-vocab": "advisory"})
    merged = merge_profiles([base, project, local])
    assert merged.severities["ai-vocab"] == "advisory"  # local overrides
    assert merged.severities["ai-prose"] == "off"  # from project
    assert merged.scope_excludes["*"] == ("gen/**",)  # union, base only
    assert merged.budgets["complexity-density"] == 400.0
    assert merged.budgets["duplication-density"] == 8.0
    assert merged.name == "chain: org -> proj -> local"


# --- defensive re-validation --------------------------------------------------


def test_merged_output_is_revalidated_against_the_registry():
    forged = ProfileSpec(name="bad", severities={"not-a-check": "off"})
    with pytest.raises(ValueError, match="unknown check id"):
        merge_profiles([forged])


def test_illegal_merged_severity_raises():
    forged = ProfileSpec(name="bad", severities={"ai-vocab": "blocking"})
    with pytest.raises(ValueError, match="illegal severity"):
        _validate_merged(forged)


def test_unknown_merged_budget_id_raises():
    forged = ProfileSpec(name="bad", budgets={"not-a-budget": 10.0})
    with pytest.raises(ValueError, match="unknown budget id"):
        merge_profiles([forged])


def test_nonpositive_merged_budget_raises():
    forged = ProfileSpec(name="bad", budgets={"complexity-density": 0})
    with pytest.raises(ValueError, match="must be positive"):
        merge_profiles([forged])


def test_merged_never_escalates_above_advisory_by_construction():
    base = ProfileSpec(name="base", severities={"ai-vocab": "advisory"})
    child = ProfileSpec(name="child", severities={"ai-vocab": "off"})
    # both directions of override stay inside the downgrade-only ladder
    assert merge_profiles([base, child]).severities["ai-vocab"] == "off"
    assert merge_profiles([child, base]).severities["ai-vocab"] == "advisory"


def test_merged_scope_patterns_get_the_loader_checks():
    # a constructed spec cannot smuggle an uncompilable glob past the merge
    forged = ProfileSpec(name="bad", scope_excludes={"*": ("[abc]",)})
    with pytest.raises(ValueError, match="not a supported pattern"):
        merge_profiles([forged])


def test_merged_scope_pattern_rejects_a_negation():
    forged = ProfileSpec(name="bad", scope_excludes={"*": ("!generated/**",)})
    with pytest.raises(ValueError, match="include-only"):
        merge_profiles([forged])


def test_merged_scope_pattern_rejects_an_empty_glob():
    forged = ProfileSpec(name="bad", scope_excludes={"*": ("   ",)})
    with pytest.raises(ValueError, match="non-empty"):
        merge_profiles([forged])


def test_merged_scope_pattern_error_names_the_key():
    forged = ProfileSpec(name="bad", scope_excludes={"*": ("[abc]",)})
    with pytest.raises(ValueError, match=r"merged profile: scope '\*'") as exc:
        merge_profiles([forged])
    assert "[abc]" in str(exc.value)


# --- fail-closed chain loading -------------------------------------------------


def test_load_profile_chain_merges_two_documents(tmp_path):
    _write_profile(tmp_path, "base", {"name": "base", "severities": {"ai-vocab": "off"}})
    _write_profile(tmp_path, "proj", {"name": "proj", "budgets": {"complexity-density": 380}})
    merged = load_profile_chain([tmp_path / "base.json", tmp_path / "proj.json"])
    assert merged.severities["ai-vocab"] == "off"
    assert merged.budgets["complexity-density"] == 380.0


def test_missing_chain_document_raises(tmp_path):
    with pytest.raises(OSError):
        load_profile_chain([tmp_path / "absent.json"])


def test_malformed_chain_document_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_profile_chain([bad])


def test_unknown_key_in_chain_document_raises(tmp_path):
    _write_profile(tmp_path, "bad", {"name": "bad", "no_such_lever": 1})
    with pytest.raises(ValueError, match="unknown key"):
        load_profile_chain([tmp_path / "bad.json"])


# --- CLI -----------------------------------------------------------------------


def test_cli_merges_and_prints_json(tmp_path, capsys):
    _write_profile(tmp_path, "base", {"name": "base", "severities": {"ai-vocab": "off"}})
    _write_profile(tmp_path, "proj", {"name": "proj", "scope_excludes": {"*": ["gen/**"]}})
    rc = main(["--chain", f"{tmp_path / 'base.json'}", f"{tmp_path / 'proj.json'}", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["name"] == "chain: base -> proj"
    assert out["severities"] == {"ai-vocab": "off"}
    assert out["scope_excludes"] == {"*": ["gen/**"]}


def test_cli_comma_separated_chain(tmp_path, capsys):
    _write_profile(tmp_path, "a", {"name": "a"})
    _write_profile(tmp_path, "b", {"name": "b"})
    rc = main(["--chain", f"{tmp_path / 'a.json'},{tmp_path / 'b.json'}"])
    assert rc == 0
    assert "chain: a -> b" in capsys.readouterr().out


def test_cli_rejects_a_bad_chain_with_exit_two(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    rc = main(["--chain", str(bad)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "rejected" in captured.err


def test_cli_without_chain_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
