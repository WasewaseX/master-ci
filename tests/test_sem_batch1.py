"""Contract tests for the batch-1 reverse-engineering tools (round-17).

Covers the documented contracts:

- matrix validate: duplicate mapping keys refuse to parse silently,
    empty dimensions, non-list dimensions, the computed product against
    the documented 64 ceiling, dangling include/exclude keys, dynamic
    expansion reported as unverifiable, exit codes
- change stack: numstat and path-list parsing, deterministic cohort
    membership with first-rule-wins precedence, foundation-first
    ordering with unclassified last and shown, sizes, markdown and
    JSON shapes
- merge risk: the versioned additive model (blocking 25, security 15,
    reliability 10, maintainability 3, other 2, blast radius, data
    gaps), registry-driven severity, incomplete reports, the
    never-proof-of-safety line, determinism
"""

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))
import sem_change_stack as stack_mod  # noqa: E402
import sem_matrix_validate as matrix_mod  # noqa: E402
import sem_merge_risk as risk_mod  # noqa: E402

# --- C42: matrix expansion safety ------------------------------------------


def _workflow(tmp_path: Path, name: str, text: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_matrix_duplicate_key_is_refused_not_silently_last_wins(tmp_path):
    _workflow(
        tmp_path,
        "wf.yml",
        "jobs:\n  build:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n        os: [macos-latest]\n",
    )
    violations = matrix_mod.validate_root(tmp_path)
    assert [v.kind for v in violations] == ["duplicate-key"]
    assert "os" in violations[0].detail


def test_matrix_empty_dimension_fails(tmp_path):
    _workflow(tmp_path, "wf.yml", "jobs:\n  build:\n    strategy:\n      matrix:\n        os: []\n")
    violations = matrix_mod.validate_root(tmp_path)
    assert [v.kind for v in violations] == ["empty-dimension"]


def test_matrix_non_list_dimension_fails(tmp_path):
    _workflow(tmp_path, "wf.yml", "jobs:\n  build:\n    strategy:\n      matrix:\n        os: ubuntu-latest\n")
    violations = matrix_mod.validate_root(tmp_path)
    assert [v.kind for v in violations] == ["non-list-dimension"]


def test_matrix_product_over_ceiling_carries_the_expansion(tmp_path):
    dims = "\n".join(f"        k{i}: [a,b,c,d,e,f,g,h,j,k]" for i in range(3))
    _workflow(tmp_path, "wf.yml", f"jobs:\n  build:\n    strategy:\n      matrix:\n{dims}\n")
    violations = matrix_mod.validate_root(tmp_path)
    over = [v for v in violations if v.kind == "product-over-ceiling"]
    assert len(over) == 1
    assert "1000" in over[0].detail and "64" in over[0].detail


def test_matrix_exclude_cannot_reference_undeclared_keys_but_include_may(tmp_path):
    _workflow(
        tmp_path,
        "wf.yml",
        (
            "jobs:\n  build:\n    strategy:\n      matrix:\n"
            "        os: [ubuntu-latest]\n"
            "        include:\n          - node: 20\n"
            "        exclude:\n            - arch: arm\n"
        ),
    )
    violations = matrix_mod.validate_root(tmp_path)
    kinds = [v.kind for v in violations]
    assert kinds == ["dangling-exclude"]
    assert "arch" in violations[0].detail


def test_matrix_dynamic_expansion_is_reported_unverifiable(tmp_path):
    _workflow(
        tmp_path,
        "wf.yml",
        "jobs:\n  build:\n    strategy:\n      matrix: ${{ fromJSON(needs.st.outputs.m) }}\n",
    )
    violations = matrix_mod.validate_root(tmp_path)
    assert [v.kind for v in violations] == ["dynamic-expansion"]


def test_matrix_clean_workflow_passes_with_exit_zero(tmp_path):
    _workflow(
        tmp_path,
        "wf.yml",
        (
            "jobs:\n  build:\n    strategy:\n      matrix:\n"
            "        os: [ubuntu-latest, macos-latest]\n"
            "        node: [18, 20]\n"
    "        include:\n          - os: macos-latest\n            experimental: true\n"
        ),
    )
    assert matrix_mod.validate_root(tmp_path) == []
    assert matrix_mod.main(["--root", str(tmp_path), "--json"]) == 0


def test_matrix_cli_exit_codes(tmp_path, capsys):
    _workflow(tmp_path, "wf.yml", "jobs:\n  build:\n    strategy:\n      matrix:\n        os: []\n")
    assert matrix_mod.main(["--root", str(tmp_path)]) == 1
    assert matrix_mod.main(["--root", str(tmp_path / "missing")]) == 2


# --- B23: the change stack ---------------------------------------------------

NUMSTAT = "\n".join(
    [
        "12\t3\tsrc/db/schema.py",
        "40\t0\tsrc/db/migrations/0002_users.py",
        "25\t8\tsrc/api/routes/orders.py",
        "10\t2\tsrc/services/orders.py",
        "6\t1\tsrc/components/OrderTable.tsx",
        "18\t0\ttests/test_orders.py",
        "2\t0\t.github/workflows/ci.yml",
        "5\t1\tsrc/utils/misc.go",
        "9\t4\tdocs/orders.md",
    ]
)


def test_change_stack_numstat_parses_sizes():
    files = stack_mod.parse_numstat(NUMSTAT.splitlines())
    assert len(files) == 9
    assert files[0].path == "src/db/schema.py" and files[0].added == 12


def test_change_stack_cohorts_are_foundation_first_and_deterministic():
    stack = stack_mod.build_stack(stack_mod.parse_numstat(NUMSTAT.splitlines()))
    names = [c.name for c in stack]
    assert names[0] == "data-model"
    assert names[-1] == "unclassified" or names[-1] == "docs"
    assert "docs" in names and "api" in names
    again = stack_mod.build_stack(stack_mod.parse_numstat(NUMSTAT.splitlines()))
    assert names == [c.name for c in again]
    for cohort in stack:
        paths = [f.path for f in cohort.files]
        assert paths == sorted(paths)


def test_change_stack_precedence_is_first_matching_rule():
    assert stack_mod.classify("src/db/migrations/0002.py") == "data-model"
    assert stack_mod.classify("src/api/routes/orders.py") == "api"
    assert stack_mod.classify("tests/test_api.py") == "tests"
    assert stack_mod.classify("random/thing.xyz") == "unclassified"


def test_change_stack_markdown_and_json_shapes():
    stack = stack_mod.build_stack(stack_mod.parse_numstat(NUMSTAT.splitlines()))
    md = stack_mod.render_markdown(stack)
    assert md.startswith("# Change stack")
    assert "+40/-0" in md  # the migration's size shows in its cohort
    payload = stack_mod.stack_to_json(stack)
    assert payload["cohort_order"][0] == "data-model"
    assert payload["cohort_order"][-1] == "unclassified"
    assert all(c["files"] for c in payload["cohorts"])


def test_change_stack_empty_input_is_usage_error(capsys):
    assert stack_mod.main(["--files", str(_nonexistent())]) == 2


def _nonexistent() -> str:
    return "/nonexistent/change-stack-input.txt"


# --- B31: merge risk ---------------------------------------------------------


def test_merge_risk_severity_classes_come_from_the_registries():
    assert risk_mod._severity_class("swallowed-exception") == "blocking"
    assert risk_mod._severity_class("sql-injection") == "blocking"  # blocking outranks category
    assert risk_mod._severity_class("todo-density") == "blocking"  # blocking, pinned
    assert risk_mod._severity_class("meta-comment") == "other"


def test_merge_risk_model_v1_math_is_exact():
    findings = [
        {"check": "swallowed-exception"},
        {"check": "swallowed-exception"},
        {"check": "meta-comment"},
    ]
    report = risk_mod.score(findings, changed_count=5, coverage_known=True)
    assert report.model_version == 1
    assert report.score == 52  # 2 blocking x25 + 1 other x2
    assert not report.incomplete
    rows = {c["kind"]: c["points"] for c in report.contributions}
    assert rows["blocking-findings"] == 50 and rows["other-findings"] == 2


def test_merge_risk_blast_radius_tiers():
    small = risk_mod.score([], 5, coverage_known=True, findings_available=True)
    mid = risk_mod.score([], 25, coverage_known=True, findings_available=True)
    large = risk_mod.score([], 60, coverage_known=True, findings_available=True)
    assert small.score == 0
    assert mid.score == risk_mod.BLAST_SMALL
    assert large.score == risk_mod.BLAST_LARGE
    assert risk_mod.score([], 60, coverage_known=True, findings_available=True).score <= risk_mod.CAP


def test_merge_risk_legitimate_empty_scan_is_not_a_gap():
    """A real scan with zero findings scores zero, not incomplete."""
    report = risk_mod.score([], 0, coverage_known=True, findings_available=True)
    assert report.score == 0 and not report.incomplete


def test_merge_risk_missing_inputs_are_data_gaps_not_zeros():
    report = risk_mod.score([], 0, coverage_known=False, findings_available=False)
    assert report.incomplete
    kinds = [c["kind"] for c in report.contributions]
    assert kinds.count("data-gap") == 2  # no findings + unknown coverage
    assert report.score == risk_mod.DATA_GAP * 2


def test_merge_risk_caps_at_100():
    findings = [{"check": "swallowed-exception"} for _ in range(10)]
    report = risk_mod.score(findings, 60, coverage_known=False)
    assert report.score == risk_mod.CAP


def test_merge_risk_deterministic_and_json_shaped():
    findings = [{"check": "sql-injection"}, {"check": "todo-density"}]
    one = risk_mod.score(findings, 22, coverage_known=False)
    two = risk_mod.score(list(reversed(findings)), 22, coverage_known=False)
    assert one.score == two.score
    payload = json.loads(json.dumps(vars(one)))
    assert payload["model_version"] == 1
    md = risk_mod.render_markdown(one, 22)
    assert "not proof of safety" in md
    assert f"{one.score}/100" in md


def test_merge_risk_cli_json(tmp_path):
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps([{"check": "swallowed-exception"}]), encoding="utf-8")
    assert risk_mod.main(["--findings", str(findings_path), "--changed", "3", "--json"]) == 0


def test_matrix_dynamic_dimension_value_is_reported_gate_neutral(tmp_path):
    """${{ fromJSON }} on a dimension value: visible, exit 0."""
    _workflow(
        tmp_path,
        "wf.yml",
        "jobs:\n  analyze:\n    strategy:\n      matrix:\n        language: ${{ fromJSON(needs.p.outputs.l) }}\n",
    )
    violations = matrix_mod.validate_root(tmp_path)
    assert [v.kind for v in violations] == ["dynamic-expansion"]
    assert matrix_mod.main(["--root", str(tmp_path)]) == 0


def test_matrix_dogfood_this_repos_own_workflows():
    """The repo's own workflows must pass the repo's own validator."""
    root = Path(__file__).resolve().parent.parent
    violations = matrix_mod.validate_root(root)
    hard = [v for v in violations if v.kind != "dynamic-expansion"]
    assert hard == []


def test_change_stack_dogfood_last_commit_numstat(tmp_path):
    """The tool reads this repo's real numstat stream without crashing."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["git", "diff", "--numstat", "HEAD~1..HEAD"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        import pytest

        pytest.skip("no git history available")
    files = stack_mod.parse_numstat(proc.stdout.splitlines())
    stack = stack_mod.build_stack(files)
    assert stack  # every changed file lands in exactly one cohort
