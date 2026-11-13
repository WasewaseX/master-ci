"""Contract tests for the hotspot risk ranking (P1 metric layer).

Covers the documented contract:
- the formula: (churn + 3 * defects) * ccn * (1 + fan_in / 10),
- zero-complexity files never rank; missing history or coupling is zero,
- the floor: a snippet corpus gets no hotspot verdict,
- the budget: one advisory finding over budget, silent at or under it,
- determinism: ties resolve alphabetically and renders stay bounded,
- churn_map against a real temp git repo: counts, fix subjects,
    subdirectory prefix stripping, and the silent ({}, {}) contract on
    any git failure.

Behavior tests only: the registry wiring is covered by the integrator.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_coupling import CouplingFacts  # noqa: E402
from slop_density import MIN_CORPUS_LINES, DensityFacts  # noqa: E402
from slop_hotspot import (  # noqa: E402
    HOTSPOT_RISK_BUDGET,
    check_hotspot_metrics,
    churn_map,
    measure_hotspot,
)


def plain_facts(worst_files=(), exec_lines=1000):
    """A minimal measured corpus for direct hotspot calls."""
    return DensityFacts(
        files=len(worst_files),
        exec_lines=exec_lines,
        complexity=sum(ccn for _, ccn in worst_files),
        duplicated=0,
        per_lang={},
        worst_files=tuple(worst_files),
        worst_line=None,
    )


def coupling_with(fan_in):
    """Coupling facts carrying just a fan-in map, the hotspot's only read."""
    return CouplingFacts(
        nodes=len(fan_in),
        fan_out={},
        fan_in=dict(fan_in),
        instability={},
        worst_fan_out=(),
        hubs=(),
    )


# --- the formula ------------------------------------------------------------


def test_formula_math_ranks_the_multiplied_file():
    facts = plain_facts((("src/heavy.py", 10), ("src/light.py", 2)))
    coupling = coupling_with({"src/heavy.py": 5})
    hot = measure_hotspot(facts, coupling, {"src/heavy.py": 4}, {"src/heavy.py": 1})
    # (4 + 3 * 1) * 10 * (1 + 5 / 10) = 105.0; light.py has no history at all.
    assert hot.scored[0] == ("src/heavy.py", 105.0, 4, 1, 10, 5)
    assert hot.scored[1] == ("src/light.py", 0.0, 0, 0, 2, 0)
    assert hot.files == 2
    assert hot.max_risk == 105.0


def test_zero_complexity_file_contributes_nothing():
    """Only facts.worst_files rank: a churny flat file gets no row."""
    facts = plain_facts((("src/heavy.py", 10),))
    churn = {"src/flat.py": 40, "src/heavy.py": 4}
    hot = measure_hotspot(facts, None, churn, {})
    assert [row[0] for row in hot.scored] == ["src/heavy.py"]
    assert hot.files == 1


def test_coupling_none_counts_zero_dependents():
    facts = plain_facts((("src/heavy.py", 10),))
    hot = measure_hotspot(facts, None, {"src/heavy.py": 4}, {"src/heavy.py": 1})
    assert hot.scored[0] == ("src/heavy.py", 70.0, 4, 1, 10, 0)


def test_fix_commits_price_three_plain_commits():
    facts = plain_facts((("src/fixy.py", 4),))
    hot = measure_hotspot(facts, None, {"src/fixy.py": 2}, {"src/fixy.py": 1})
    # (2 + 3 * 1) * 4 = 20.0: one fix-shaped commit weighs three plain ones.
    assert hot.scored[0][1] == 20.0


def test_ties_resolve_alphabetically():
    facts = plain_facts((("src/b.py", 5), ("src/a.py", 5)))
    churn = {"src/a.py": 3, "src/b.py": 3}
    hot = measure_hotspot(facts, None, churn, {})
    assert hot.scored[0][0] == "src/a.py"
    assert hot.scored[0][1] == hot.scored[1][1] == 15.0


# --- the verdict: floors, budget, silence -----------------------------------


def test_no_history_stays_silent_even_with_complexity():
    """Empty churn prices risk 0: history is the numerator, silence follows."""
    facts = plain_facts((("src/heavy.py", 100),))
    coupling = coupling_with({"src/heavy.py": 20})
    assert check_hotspot_metrics(facts, coupling, {}, {}) == []


def test_snippet_corpus_gets_no_verdict():
    facts = plain_facts((("src/heavy.py", 100),), exec_lines=100)
    assert facts.exec_lines < MIN_CORPUS_LINES
    assert check_hotspot_metrics(facts, None, {"src/heavy.py": 5000}, {}) == []


def test_empty_ranking_stays_silent():
    facts = plain_facts()
    hot = measure_hotspot(facts, None, {}, {})
    assert hot.scored == ()
    assert hot.files == 0
    assert hot.max_risk == 0.0
    assert check_hotspot_metrics(facts, None, {"src/heavy.py": 30}, {}) == []


def test_over_budget_emits_one_advisory_finding():
    facts = plain_facts((("src/heavy.py", 10), ("src/light.py", 2)))
    coupling = coupling_with({"src/heavy.py": 5})
    churn = {"src/heavy.py": 4000}
    defects = {"src/heavy.py": 1000}
    hits = check_hotspot_metrics(facts, coupling, churn, defects)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, mod.Finding)  # the detector's own model object
    assert hit.check == "hotspot-risk"
    assert hit.advisory is True
    assert hit.weight == 2
    assert hit.path == "src/heavy.py"
    assert hit.line == 1
    # (4000 + 3 * 1000) * 10 * 1.5 = 105000
    assert f"hotspot risk 105000 (budget {HOTSPOT_RISK_BUDGET})" in hit.message
    assert (
        "src/heavy.py - 4000 commits (1000 fix commits) x complexity 10 x 5 dependents"
        in hit.message
    )


def test_message_renders_bounded_ranked_list():
    """Six hot files render the top four plus a +2 tail."""
    worst = tuple((f"src/h{i}.py", 10) for i in range(6))
    churn = {f"src/h{i}.py": (i + 1) * 1000 for i in range(6)}
    hits = check_hotspot_metrics(plain_facts(worst), None, churn, {})
    assert len(hits) == 1
    message = hits[0].message
    assert (
        "hottest: src/h5.py 60000, src/h4.py 50000, src/h3.py 40000, src/h2.py 30000, +2 more"
    ) in message
    assert "src/h1.py" not in message
    assert "src/h0.py" not in message


# --- churn_map against real and broken git ----------------------------------


def _git(repo: Path, *args: str) -> None:
    """Run git with a throwaway identity; no config file is ever touched."""
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=hotspot-test",
            "-c",
            "user.email=hotspot-test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_churn_map_reads_a_real_repo(tmp_path, tmp_path_factory):
    _git(tmp_path, "init", "-b", "main")
    pkg = tmp_path / "pkg"
    outside = tmp_path / "outside"
    pkg.mkdir()
    outside.mkdir()
    (pkg / "alpha.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "beta.py").write_text("OTHER = 2\n", encoding="utf-8")
    (outside / "extra.py").write_text("EXTRA = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "add report")
    (pkg / "alpha.py").write_text("VALUE = 1 + 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "fix: handle empty payload")

    churn, defects = churn_map(tmp_path)
    assert churn == {"pkg/alpha.py": 2, "pkg/beta.py": 1, "outside/extra.py": 1}
    assert defects == {"pkg/alpha.py": 1}

    # A subdirectory scan root strips git's repo-root prefix.
    sub_churn, sub_defects = churn_map(pkg)
    assert sub_churn == {"alpha.py": 2, "beta.py": 1}
    assert sub_defects == {"alpha.py": 1}

    # A directory without history stays silent - never a guessed count.
    assert churn_map(tmp_path_factory.mktemp("no-history")) == ({}, {})


def test_git_failure_degrades_to_empty_maps(monkeypatch):
    """A timeout is one of the swallowed failures: silence, not a guess."""

    def blow_up(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr("slop_hotspot.subprocess.run", blow_up)
    assert churn_map(Path("/anywhere")) == ({}, {})


def test_hotspot_check_is_registered_advisory_maintainability():
    assert mod.category_of("hotspot-risk") == "maintainability"
    assert "hotspot-risk" in mod.REMEDIES
    assert mod.check_tier("hotspot-risk") == 0.75  # maintainability pressure tier
    registered = mod.Finding(path="x", line=1, check="hotspot-risk", message="m", weight=2)
    assert registered.advisory
