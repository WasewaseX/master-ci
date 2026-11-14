"""Contract tests for the test-redundancy oracle (v2.17, benchmark phase 2).

Covers the documented contract in the module docstring:
- exact duplicate bodies group into one advisory finding naming the
twins with their line numbers,
- docstrings and the function's own name are normalized out of the
comparison, while real body differences stay silent,
- class methods join the pool and async tests too,
- production paths never yield the finding: is_test_path governs
eligibility, the same scoping every other test-scoped check uses,
- malformed input degrades to silence, and docstring/pass/ellipsis-only
bodies are the empty-test check's territory, not redundancy,
- the check is registered advisory test-assurance.
"""

from pathlib import Path

import slop_test_redundancy as tr
from detector_module import detector as mod

DUPLICATE_BODY = "    rate = round(1.085, 2)\n    assert rate == 1.09\n"

TRIPLET = (
    '"""Rate checks (fixture-shaped)."""\n\n\n'
    "def test_rounds_to_two_places():\n" + DUPLICATE_BODY + "\n"
    "def test_rounds_to_two_places_again():\n" + DUPLICATE_BODY + "\n"
    "def test_rounds_to_two_places_once_more():\n" + DUPLICATE_BODY + "\n"
    "def test_line_sum_matches_total():\n    lines = [1.25, 2.5]\n    assert sum(lines) == 3.75\n"
)


def run(text: str, rel: str = "tests/test_rates.py"):
    return tr.check_test_redundancy(Path(rel), text, rel)


# --- the duplicate group -----------------------------------------------------


def test_exact_duplicates_group_with_line_numbered_evidence():
    """One finding per group; the twins are named with their lines, the rest not."""
    findings = run(TRIPLET)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check == "redundant-test"
    assert finding.advisory
    assert finding.line == 4  # the first twin's definition line
    for twin in (
        "test_rounds_to_two_places",
        "test_rounds_to_two_places_again",
        "test_rounds_to_two_places_once_more",
    ):
        assert twin in finding.message
    assert "test_line_sum_matches_total" not in finding.message
    assert finding.message.count("(line") == 3
    assert run(TRIPLET) == findings  # deterministic across runs


def test_docstrings_and_function_names_are_normalized_out():
    """Different names and docstrings do not save two identical bodies."""
    text = (
        "def test_alpha():\n"
        '    """Alpha checks the rounding rule."""\n' + DUPLICATE_BODY + "\n"
        "def test_beta():\n"
        '    """Beta checks something else entirely."""\n' + DUPLICATE_BODY
    )
    findings = run(text)
    assert len(findings) == 1
    assert "test_alpha (line 1)" in findings[0].message
    assert "test_beta (line 6)" in findings[0].message


def test_class_method_twins_join_the_pool():
    """pytest collects class methods; the oracle groups them the same way."""
    text = (
        "class TestRates:\n"
        "    def test_round(self):\n"
        "        rate = round(1.085, 2)\n"
        "        assert rate == 1.09\n"
        "\n"
        "    def test_round_twin(self):\n"
        "        rate = round(1.085, 2)\n"
        "        assert rate == 1.09\n"
        "\n"
        "    def test_other(self):\n"
        "        assert 1 + 1 == 2\n"
    )
    findings = run(text)
    assert len(findings) == 1
    assert "TestRates.test_round (line 2)" in findings[0].message
    assert "TestRates.test_round_twin (line 6)" in findings[0].message
    assert "test_other" not in findings[0].message


def test_async_test_twins_join_the_pool():
    """Async test functions are the same oracle surface."""
    text = (
        "async def test_fetch():\n"
        "    client = open_session()\n"
        "    assert client.ready\n"
        "\n"
        "async def test_fetch_twin():\n"
        "    client = open_session()\n"
        "    assert client.ready\n"
    )
    findings = run(text)
    assert len(findings) == 1
    assert "test_fetch" in findings[0].message and "test_fetch_twin" in findings[0].message


def test_two_independent_groups_report_two_findings():
    """Each duplicate group is its own finding, ordered by first definition."""
    body = "    assert pack(1) == 'one'\n"
    text = (
        "def test_pack_single():\n" + body + "\n"
        "def test_pack_single_copy():\n" + body + "\n"
        "def test_pack_pair():\n"
        "    assert pack(2) == 'pair'\n"
        "\n"
        "def test_pack_pair_copy():\n"
        "    assert pack(2) == 'pair'\n"
    )
    findings = run(text)
    assert len(findings) == 2
    assert findings[0].line < findings[1].line


# --- what must stay silent ---------------------------------------------------


def test_different_bodies_stay_silent():
    """A changed assertion or identifier is a real difference at this strictness."""
    text = (
        "def test_same_shape_one():\n"
        "    rate = round(1.085, 2)\n"
        "    assert rate == 1.09\n"
        "\n"
        "def test_same_shape_two():\n"
        "    rate = round(1.086, 2)\n"
        "    assert rate == 1.09\n"
        "\n"
        "def test_same_shape_three():\n"
        "    amount = round(1.085, 2)\n"
        "    assert amount == 1.09\n"
    )
    assert run(text) == []


def test_empty_and_ellipsis_bodies_are_not_redundancy():
    """Docstring-only, pass-only and ellipsis-only tests are empty-test's verdict."""
    text = (
        "def test_promised():\n"
        '    """Covered by the integration suite."""\n'
        "\n"
        "def test_promised_twin():\n"
        '    """Covered by the integration suite."""\n'
        "\n"
        "def test_skipped():\n"
        "    pass\n"
        "\n"
        "def test_skipped_twin():\n"
        "    ...\n"
    )
    assert run(text) == []


def test_production_paths_never_yield_the_finding():
    """The path oracle governs: an identical file outside a test path stays silent."""
    assert run(TRIPLET, rel="src/service.py") == []
    assert run(TRIPLET, rel="scripts/tool.py") == []


def test_scan_flags_test_paths_only(tmp_path):
    """End to end: the same duplicate corpus flags in tests/, not in src/."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(TRIPLET, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text(TRIPLET, encoding="utf-8")
    findings = mod.scan(tmp_path, mod.load_phrases())
    hits = [finding for finding in findings if finding.check == "redundant-test"]
    assert len(hits) == 1
    assert hits[0].path.endswith("test_service.py")


def test_malformed_and_non_python_input_stay_silent():
    """A syntax error degrades to silence; non-Python files are out of scope."""
    assert run("def test_(:\n") == []
    text = "function test_one() { return 1 }\nfunction test_one() { return 1 }\n"
    assert run(text, rel="tests/test_case.js") == []


def test_plain_and_setup_only_bodies_differ():
    """A setup statement versus an assert is not an exact duplicate."""
    text = (
        "def test_builds():\n"
        "    rates = load_rates()\n"
        "    assert rates\n"
        "\n"
        "def test_builds_twin():\n"
        "    rates = load_rates()\n"
        "    assert rates\n"
        "\n"
        "def test_loads():\n"
        "    load_rates()\n"
    )
    assert len(run(text)) == 1  # the twin pair; the bare setup call stands alone


# --- registration ------------------------------------------------------------


def test_redundant_test_registered_advisory_test_assurance():
    """The finding is advisory test-assurance with a remedy, never blocking."""
    assert mod.category_of("redundant-test") == "test-assurance"
    assert "redundant-test" in mod.REMEDIES
    assert mod.check_tier("redundant-test") == 0.5
    registered = mod.Finding(path="t.py", line=1, check="redundant-test", message="m", weight=1)
    assert registered.advisory
    assert "redundant-test" not in mod.BLOCKING_CHECKS
    assert "redundant-test" not in mod.STRUCTURAL_BUDGET_CHECKS


def test_module_docstring_states_the_conservative_scope():
    """The honesty contract is load-bearing: the docstring names the widening."""
    doc = (tr.__doc__ or "").lower()
    assert "near-duplicates" in doc
    assert "future widening" in doc
    assert "is_test_path" in doc
