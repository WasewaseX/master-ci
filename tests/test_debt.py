"""Contract tests for the remediation debt ratio (v2.14, P1).

Covers the documented contract in docs/checks.md:
- a dirty corpus over budget reports one debt-ratio finding,
- the SQALE denominator: reconstruction cost from the measured corpus,
- pricing by registry class: design 120, structural 30, blocking 20,
  default 10 minutes,
- the floor: a snippet corpus gets no debt verdict even when dirty,
- the engine's own finding never prices itself,
- robustness: unparseable files, empty repositories,
- registration: advisory, maintainability, tier, remedies.

The dirty corpora are smelly-module builders: bare excepts are the
reliably priced blocking defect, and the corpus has to clear the
densities' MIN_CORPUS_LINES floor before the ratio may speak.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_debt import debt_report, remediation_minutes  # noqa: E402
from slop_density import DensityFacts  # noqa: E402
from slop_model import Finding  # noqa: E402


def scan(tmp_path, files=None, extra_globs=None):
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases(), extra_globs=extra_globs)


def debt_hits(findings):
    return [f for f in findings if f.check == "debt-ratio"]


# --- corpus builders -------------------------------------------------------
#
# smelly module: two bare excepts (the reliably priced blocking defect)
# over ~12 executable lines, all short enough that no duplication trips.
SMELLY = """
def handler{i}(raw):
    try:
        parsed = json.loads(raw)
    except:
        parsed = None
    if parsed is None:
        return DEFAULT{i}
    return parsed["value"] + OFFSET{i}

def fallback{i}(raw):
    try:
        return json.loads(raw)["value"]
    except:
        return DEFAULT{i}
"""


def smelly_modules(count: int) -> dict[str, str]:
    return {f"smelly{i}.py": SMELLY.format(i=i) for i in range(count)}


# flat module: ~9 clean executable lines, every line short and unique.
FLAT = """
OFFSET{i} = {off}

def add{i}(a, b):
    shifted = a + OFFSET{i}
    return shifted + b

def sub{i}(a, b):
    shifted = a - OFFSET{i}
    return shifted - b
"""


def flat_modules(count: int) -> dict[str, str]:
    return {f"flat{i}.py": FLAT.format(i=i, off=3 + i) for i in range(count)}


def plain_facts(exec_lines: int) -> DensityFacts:
    """A minimal measured corpus for direct check_debt_metrics calls."""
    return DensityFacts(
        files=2,
        exec_lines=exec_lines,
        complexity=0,
        duplicated=0,
        per_lang={},
        worst_files=(),
        worst_line=None,
    )


# ---------------------------------------------------------------------------
# the scan-level contract
# ---------------------------------------------------------------------------


def test_dirty_corpus_reports_debt_ratio(tmp_path):
    files = smelly_modules(24)  # ~290 exec lines, ~48 blocking defects
    hits = debt_hits(scan(tmp_path, files))
    assert len(hits) == 1
    assert hits[0].advisory and hits[0].weight == 2
    assert "debt ratio" in hits[0].message
    assert f"(budget {mod.DEBT_RATIO_BUDGET}%)" in hits[0].message
    assert "worst check:" in hits[0].message


def test_clean_corpus_stays_silent(tmp_path):
    files = flat_modules(30)  # ~270 exec lines of clean flat code
    assert not debt_hits(scan(tmp_path, files))


def test_snippet_corpus_gets_no_verdict(tmp_path):
    """The floor contract: dirty but a snippet, so the ratio stays silent."""
    assert not debt_hits(scan(tmp_path, smelly_modules(2)))


def test_malformed_file_degrades_without_crashing(tmp_path):
    files = {"broken.py": "def gone(:\n    return\n", **smelly_modules(24)}
    findings = scan(tmp_path, files)
    assert isinstance(findings, list)


def test_empty_repository_is_silent(tmp_path):
    assert not debt_hits(scan(tmp_path))


# ---------------------------------------------------------------------------
# the SQALE model: pricing, denominator, exclusion
# ---------------------------------------------------------------------------


def test_pricing_by_registry_class():
    design = Finding(path="a.py", line=1, check="import-cycle", message="m", weight=3)
    density = Finding(path="a.py", line=1, check="complexity-density", message="m", weight=2)
    structural = Finding(path="a.py", line=1, check="god-function", message="m", weight=2)
    blocking = Finding(path="a.py", line=1, check="bare-except", message="m", weight=1)
    default = Finding(path="a.py", line=1, check="generic-name", message="m", weight=2)
    assert remediation_minutes(design) == mod.DESIGN_MINUTES
    assert remediation_minutes(density) == mod.DESIGN_MINUTES
    assert remediation_minutes(structural) == mod.STRUCTURAL_MINUTES
    assert remediation_minutes(blocking) == mod.BLOCKING_MINUTES
    assert remediation_minutes(default) == mod.DEFAULT_MINUTES


def test_sqale_denominator_is_the_measured_corpus():
    findings = [
        Finding(path="a.py", line=1, check="generic-name", message="m", weight=2) for _ in range(4)
    ]
    report = debt_report(findings, 300)
    assert report.dev_minutes == 300 * mod.DEV_MIN_PER_LINE
    assert report.minutes == 4 * mod.DEFAULT_MINUTES
    assert abs(report.ratio - 100 * 40 / 900) < 1e-9


def test_engine_finding_never_prices_itself():
    big = Finding(path="a.py", line=1, check="debt-ratio", message="m", weight=5)
    report = debt_report([big], 300)
    assert report.findings == 0
    assert report.minutes == 0
    assert report.ratio == 0.0


def test_worst_check_and_anchor():
    cycle = Finding(path="z.py", line=1, check="import-cycle", message="m", weight=3)
    bare1 = Finding(path="a.py", line=2, check="bare-except", message="m", weight=1)
    bare2 = Finding(path="b.py", line=3, check="bare-except", message="m", weight=1)
    report = debt_report([bare1, bare2, cycle], 300)
    assert report.worst == ("import-cycle", mod.DESIGN_MINUTES, 1)
    assert report.worst_path == "z.py"
    assert report.minutes == 2 * mod.BLOCKING_MINUTES + mod.DESIGN_MINUTES


# ---------------------------------------------------------------------------
# the finding: over budget only, effort rendered as hours
# ---------------------------------------------------------------------------


def test_ratio_under_budget_stays_silent():
    findings = [
        Finding(path="a.py", line=1, check="generic-name", message="m", weight=2) for _ in range(4)
    ]
    assert mod.check_debt_metrics(plain_facts(300), findings) == []


def test_effort_renders_as_hours_in_the_message():
    cycles = [
        Finding(path=f"c{i}.py", line=1, check="import-cycle", message="m", weight=3)
        for i in range(8)
    ]
    hits = mod.check_debt_metrics(plain_facts(300), cycles)
    assert len(hits) == 1
    assert "16h 00m" in hits[0].message  # 8 x DESIGN_MINUTES
    assert hits[0].path == "c0.py"  # anchored at the most expensive finding
    assert "over 300 executable lines" in hits[0].message


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_debt_check_is_registered_advisory_maintainability():
    assert mod.category_of("debt-ratio") == "maintainability"
    assert "debt-ratio" in mod.REMEDIES
    assert mod.check_tier("debt-ratio") == 0.75  # maintainability pressure tier
    assert mod.Finding(path="x", line=1, check="debt-ratio", message="m", weight=2).advisory
