"""Project density metrics: complexity density and duplication density.

The roadmap's P1 metric layer, slice two. LCOM4 judged structure inside
a class; the densities judge the project as a whole, the way SonarQube
and Qlty report them:

- complexity density - total cyclomatic complexity per 1000 executable
    lines. The 15-budget in slop_lizard judges one function; this ratio
    judges the codebase: a project that packs decision points tighter
    than the budget line is harder to change no matter how healthy each
    function looks alone. Same cyclomatic number as the function budget
    - one notion of complexity everywhere, so the ratio and the gate
    can never disagree.
- duplication density - duplicated executable lines as a percent of
    the corpus. The boilerplate-duplication check flags egregious
    repeated lines; the density is the trend metric, the number a
    ratchet can hold and a refactor can prove down.

Measurement corpus (one corpus, one denominator, both metrics):
source files with a lexer in slop_lizard, outside test paths, outside
the ignore globs, and outside generated trees. A missing analyzer or a
file the lexer rejects drops that file from BOTH sides of the ratio -
a degraded measurement, never a guessed one (the lizard contract).

Generated-code policy: files whose NAME matches a generated pattern
(``*_pb2.py``, ``*_pb2.go``, ``*_gen.go``, ``*.g.go``, ``*_generated.py``,
``*.generated.*``) are excluded entirely. Generated code is machine-
written and regenerated wholesale; it inflates both densities without
being maintainable debt. Name-based, so the policy is cheap, auditable
and deterministic; content markers are a future widening.

Test files are excluded by the same is_test_path oracle the other
repo-level checks use: fixture-heavy suites legitimately repeat
assertions, and production complexity is the signal here - SonarQube
keeps its duplication ratio off test code for the same reason.

Minimum corpus: under MIN_CORPUS_LINES executable lines the corpus is
a snippet, not a project, and both ratios stay silent. A ratio over a
ten-line corpus is noise (four one-line functions over ten executable
lines measure 400/1000 on perfectly healthy code); the densities are
project measurements and only speak when there is a project to
measure.

Both metrics are advisory, maintainability dimension, one finding per
metric per scan, emitted only when the budget is exceeded - a CLEAN
scan stays CLEAN. The finding anchors at the worst contributor and the
message carries the numbers, the per-language breakdown and the top
offenders as evidence.
"""

from __future__ import annotations

from collections.abc import Mapping

from slop_corpus import (  # noqa: F401 - the public corpus surface re-exported
    GENERATED_NAME,
    MIN_BLOCK_LINES,
    MIN_CORPUS_LINES,
    DensityCoverage,
    DensityFacts,
    check_analysis_coverage,
    measure,
    measure_corpus,
)
from slop_model import Finding

# Budgets calibrated at v2.13 against this codebase: 247/1000
# complexity, 2.3% duplication over ~11k non-test lines - the bars sit
# above the engine's own numbers, advisory evidence, never a gate.
COMPLEXITY_DENSITY_BUDGET = 300  # decision points per 1000 executable lines
DUP_DENSITY_BUDGET = 5.0  # duplicated executable lines, percent of corpus
DUP_BLOCK_DENSITY_BUDGET = 2.0  # block-clone lines, percent of corpus (v2.18)

MAX_LANGS_RENDER = 4
MAX_FILES_RENDER = 4
MAX_LINE_RENDER = 60


def _render_langs(per_lang: dict[str, tuple[int, int]]) -> str:
    """Per-language complexity density, densest first, bounded."""
    ranked = sorted(
        ((lang, 1000 * ccn / lines) for lang, (lines, ccn) in per_lang.items() if lines),
        key=lambda pair: -pair[1],
    )
    text = ", ".join(f"{lang} {density:.0f}" for lang, density in ranked[:MAX_LANGS_RENDER])
    if len(ranked) > MAX_LANGS_RENDER:
        text += f", +{len(ranked) - MAX_LANGS_RENDER} more"
    return text


def _render_files(worst: tuple[tuple[str, int], ...]) -> str:
    """The heaviest contributors by total complexity, bounded."""
    names = ", ".join(f"{name} {ccn}" for name, ccn in worst[:MAX_FILES_RENDER])
    if len(worst) > MAX_FILES_RENDER:
        names += f", +{len(worst) - MAX_FILES_RENDER} more"
    return names


def _budget(budgets: Mapping[str, float] | None, check: str, default: float) -> float:
    """One effective budget: the profile override when present, else the shipped constant."""
    if budgets is None:
        return default
    return budgets.get(check, default)


def _complexity_finding(facts: DensityFacts, budget: float) -> Finding:
    density = 1000 * facts.complexity / facts.exec_lines
    anchor = facts.worst_files[0][0] if facts.worst_files else "."
    message = (
        f"complexity density {density:.0f} per 1000 executable lines "
        f"(budget {budget}): {facts.complexity} decision points "
        f"across {facts.exec_lines} lines in {facts.files} files; "
        f"by language: {_render_langs(facts.per_lang)}; "
        f"worst contributors: {_render_files(facts.worst_files)}"
    )
    return Finding(
        path=anchor,
        line=1,
        check="complexity-density",
        message=message,
        weight=2,
        advisory=True,
    )


def _duplication_finding(facts: DensityFacts, budget: float) -> Finding:
    pct = 100 * facts.duplicated / facts.exec_lines
    evidence = ""
    anchor_path, anchor_line = ".", 1
    if facts.worst_line:
        seen_rel, seen_lineno, text = facts.worst_line
        evidence = f"; most repeated: {text[:MAX_LINE_RENDER]}... ({seen_rel}:{seen_lineno})"
        anchor_path, anchor_line = seen_rel, seen_lineno
    message = (
        f"duplication density {pct:.1f}% (budget {budget}%): "
        f"{facts.duplicated} duplicated executable lines of {facts.exec_lines}"
        f"{evidence}"
    )
    return Finding(
        path=anchor_path,
        line=anchor_line,
        check="duplication-density",
        message=message,
        weight=2,
        advisory=True,
    )


def _dup_block_finding(facts: DensityFacts, budget: float) -> Finding:
    """The block-clone verdict: the contiguous-runs metric, evidence anchored."""
    pct = 100 * facts.dup_block_lines / facts.exec_lines
    evidence = ""
    anchor_path, anchor_line = ".", 1
    if facts.worst_block:
        run, seen_rel, seen_lineno = facts.worst_block
        evidence = f"; largest run: {seen_rel}:{seen_lineno} ({run} lines)"
        anchor_path, anchor_line = seen_rel, seen_lineno
    message = (
        f"duplication blocks {pct:.1f}% (budget {budget:g}%, contiguous runs of "
        f"{MIN_BLOCK_LINES}+ identical lines): {facts.dup_block_lines} "
        f"block-duplicated lines of {facts.exec_lines}"
        f"{evidence}"
    )
    return Finding(
        path=anchor_path,
        line=anchor_line,
        check="dup-block-density",
        message=message,
        weight=2,
        advisory=True,
    )


def density_findings(
    facts: DensityFacts, budgets: Mapping[str, float] | None = None
) -> list[Finding]:
    """The three density findings for a measured corpus, over budget only.

    Takes the measured facts rather than a root: the pipeline computes
    the corpus once and shares it with the debt ratio, so every ratio
    in the report divides by the same denominator. `budgets` carries
    per-check replacement thresholds keyed by check id (the analyzer
    profiles' budget overrides); None keeps the module constants, so
    the shipped verdict and message are reproduced byte for byte.
    The three metrics are deliberately distinct (audit finding D):
    complexity-density judges decision-point pressure, duplication-
    density counts repeated LINES (the trend metric, unchanged), and
    dup-block-density counts lines inside contiguous repeated runs -
    the clone-shaped measure - so a repeated one-liner cannot read as
    a copied block.
    """
    complexity_budget = _budget(budgets, "complexity-density", COMPLEXITY_DENSITY_BUDGET)
    dup_budget = _budget(budgets, "duplication-density", DUP_DENSITY_BUDGET)
    block_budget = _budget(budgets, "dup-block-density", DUP_BLOCK_DENSITY_BUDGET)
    findings: list[Finding] = []
    if 1000 * facts.complexity / facts.exec_lines > complexity_budget:
        findings.append(_complexity_finding(facts, complexity_budget))
    if 100 * facts.duplicated / facts.exec_lines > dup_budget:
        findings.append(_duplication_finding(facts, dup_budget))
    if 100 * facts.dup_block_lines / facts.exec_lines > block_budget:
        findings.append(_dup_block_finding(facts, block_budget))
    return findings
