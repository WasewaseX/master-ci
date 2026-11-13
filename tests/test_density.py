"""Contract tests for the project density metrics (v2.13, P1).

Covers the documented contract in docs/checks.md:
- the dense corpus reports complexity density, the flat corpus stays clean,
- duplicated lines push duplication density over its budget,
- the minimum-corpus floor: snippets get no density verdict,
- generated names, test paths and ignore globs leave the corpus,
- an unparseable file degrades the measurement without breaking it,
- registration: advisory, maintainability, tier, remedies.

The fixtures are corpus builders: each density is a project-scale
ratio, so every verdict test assembles enough modules to clear
MIN_CORPUS_LINES (250 executable lines) before asking for a verdict.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402


def scan(tmp_path, files=None, extra_globs=None):
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases(), extra_globs=extra_globs)


def density_hits(findings):
    return [f for f in findings if f.check in ("complexity-density", "duplication-density")]


# --- corpus builders -------------------------------------------------------
#
# flat module: 16 executable lines, cyclomatic 3 -> ~190/1000, every line
# under 40 chars so the duplication candidate list stays empty.
FLAT_MODULE = """
OFFSET{i} = {off}
MIN{i} = {small}

def add{i}(a, b):
    shifted = a + OFFSET{i}
    aligned = shifted * SCALE{i}
    normalized = aligned - MIN{i}
    return normalized + b

def sub{i}(a, b):
    shifted = a - OFFSET{i}
    aligned = shifted / SCALE{i}
    normalized = aligned + MIN{i}
    return normalized - b

def mix{i}(a, b):
    shifted = a * SCALE{i}
    aligned = shifted + b
    normalized = aligned - OFFSET{i}
    return normalized
"""


def flat_modules(count: int) -> dict[str, str]:
    return {f"flat{i}.py": FLAT_MODULE.format(i=i, off=3 + i, small=i) for i in range(count)}


# dense module: a decision ladder, ~20 executable lines at cyclomatic 11
# -> ~550/1000, still no line over 40 chars (no duplication signal).
DENSE_MODULE = """
def dense{i}(a, b, c):
    total = {base}
    for i in range(a):
        if i % 2 == 0:
            total += i
        elif i % 3 == 0:
            total -= i
        while total > b:
            total -= {step}
    for j in range(c):
        if j:
            total += j
        if total < 0:
            total = {base}
    return total

def again{i}(x):
    if x > {base}:
        return 1
    elif x < 0:
        return -1
    return 0
"""


def dense_modules(count: int) -> dict[str, str]:
    return {f"dense{i}.py": DENSE_MODULE.format(i=i, base=i, step=1 + i) for i in range(count)}


# duplication module: five identical long executable lines shared by
# every file, plus a short parameterized tail (no other dup candidates).
SHARED_LINES = """    result = repository.fetch_all(projects, filters=filters, limit=max_rows)
    merged = merge_results(result, dedupe=True, keep_order=True, bucket=b)
    audit_log.write(event="fetch_projects", count=len(merged), actor=actor_id)
    if not merged:
        metrics.increment("projects.fetch.empty", tags={{"actor": actor_id}})
"""

DUP_MODULE = (
    "\n"
    + SHARED_LINES
    + """
def load{i}(cursor):
    rows = table.scan(cursor)
    kept = [r for r in rows if r.ok]
    return kept

def render{i}(rows, cursor):
    page = rows[:10]
    shown = len(page)
    return page, shown, cursor
"""
)


def dup_modules(count: int) -> dict[str, str]:
    return {f"dup{i}.py": DUP_MODULE.format(i=i) for i in range(count)}


# ---------------------------------------------------------------------------
# the measurements: over vs under budget
# ---------------------------------------------------------------------------


def test_dense_corpus_reports_complexity_density(tmp_path):
    files = dense_modules(13)  # ~260 exec lines at ~550/1000
    found = [f for f in scan(tmp_path, files) if f.check == "complexity-density"]
    assert len(found) == 1
    assert found[0].advisory and found[0].weight == 2
    assert "complexity density" in found[0].message
    assert f"(budget {mod.COMPLEXITY_DENSITY_BUDGET})" in found[0].message
    assert "by language: py" in found[0].message
    assert "worst contributors: dense0.py" in found[0].message


def test_flat_corpus_stays_clean(tmp_path):
    findings = scan(tmp_path, flat_modules(16))  # ~256 exec lines at ~190/1000
    assert not [f for f in findings if f.check == "complexity-density"]


def test_snippet_corpus_gets_no_verdict(tmp_path):
    """The floor contract: a 20-line ladder is noise, not a project."""
    findings = scan(tmp_path, {"tiny.py": DENSE_MODULE.format(i=0, base=0, step=1)})
    assert not density_hits(findings)


def test_repeated_lines_report_duplication_density(tmp_path):
    files = dup_modules(20)  # ~260 exec lines, ~38% duplicated
    found = [f for f in scan(tmp_path, files) if f.check == "duplication-density"]
    assert len(found) == 1
    assert found[0].advisory and found[0].weight == 2
    assert f"(budget {mod.DUP_DENSITY_BUDGET}%)" in found[0].message
    assert "most repeated:" in found[0].message
    assert found[0].path == "dup0.py"  # anchored at the first occurrence
    assert found[0].line == 2


def test_unique_corpus_has_no_duplication_finding(tmp_path):
    findings = scan(tmp_path, flat_modules(16))
    assert not [f for f in findings if f.check == "duplication-density"]


def test_both_metrics_cap_at_two_findings(tmp_path):
    files = {**dense_modules(13), **dup_modules(20)}
    assert len(density_hits(scan(tmp_path, files))) == 2


# ---------------------------------------------------------------------------
# corpus policy: generated, tests, globs
# ---------------------------------------------------------------------------


def test_generated_names_leave_the_corpus(tmp_path):
    files = {
        **{f"gen{i}_pb2.py": DENSE_MODULE.format(i=i, base=i, step=1) for i in range(13)},
        "service.pb.go": DENSE_MODULE.format(i=99, base=0, step=1),
    }
    assert not density_hits(scan(tmp_path, files))


def test_test_files_leave_the_corpus(tmp_path):
    files = {
        **{f"tests/test_dense{i}.py": DENSE_MODULE.format(i=i, base=i, step=1) for i in range(13)}
    }
    assert not density_hits(scan(tmp_path, files))


def test_ignore_globs_are_respected(tmp_path):
    files = {f"vendored/dense{i}.py": DENSE_MODULE.format(i=i, base=i, step=1) for i in range(13)}
    assert not density_hits(scan(tmp_path, files, extra_globs=["vendored/"]))


# ---------------------------------------------------------------------------
# robustness and registration
# ---------------------------------------------------------------------------


def test_malformed_file_degrades_without_crashing(tmp_path):
    files = {"broken.py": "def gone(:\n    return\n", **flat_modules(16)}
    findings = scan(tmp_path, files)
    assert not density_hits(findings)  # flat corpus under budget, no crash


def test_corpus_without_lexer_sources_is_silent(tmp_path):
    findings = scan(tmp_path, {"readme.md": "# words\n", "run.sh": "echo hi\n"})
    assert not density_hits(findings)


def test_empty_repository_is_silent(tmp_path):
    assert not density_hits(scan(tmp_path))


def test_density_checks_are_registered_advisory_maintainability():
    for check in ("complexity-density", "duplication-density"):
        assert mod.category_of(check) == "maintainability"
        assert check in mod.REMEDIES
        assert mod.check_tier(check) == 0.75  # maintainability pressure tier
        assert mod.Finding(path="x", line=1, check=check, message="m", weight=2).advisory


# ---------------------------------------------------------------------------
# P1 acceptance: the benchmark fixture contract
# ---------------------------------------------------------------------------


def test_benchmark_fixture_short_but_complex(tmp_path):
    """The benchmark's "short but complex function" row, at project scale."""
    ladder = """
def decide(state):
    if state.a and state.b or state.c and not state.d:
        if state.e:
            for k in state.items:
                while k.pending:
                    k.step()
        elif state.f:
            return [x for x in state.items if x.ready]
    return None
"""
    files = {**dense_modules(13), "decider.py": ladder}
    found = [f for f in scan(tmp_path, files) if f.check == "complexity-density"]
    assert len(found) == 1
    assert "complexity density" in found[0].message
