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

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from slop_analysis_coverage import EXCLUDED_EXTS
from slop_files import is_ignored, is_test_path, read_text_lax
from slop_lizard import LIZARD_EXTS, parse_file_facts
from slop_model import Finding
from slop_patterns import DUP_LINE_MIN_LEN

# Budgets calibrated at v2.13 against this codebase: 247/1000
# complexity, 2.3% duplication over ~11k non-test lines - the bars sit
# above the engine's own numbers, advisory evidence, never a gate.
COMPLEXITY_DENSITY_BUDGET = 300  # decision points per 1000 executable lines
DUP_DENSITY_BUDGET = 5.0  # duplicated executable lines, percent of corpus
DUP_BLOCK_DENSITY_BUDGET = 2.0  # block-clone lines, percent of corpus (v2.18)
MIN_CORPUS_LINES = 250  # under this the corpus is a snippet, not a project

# v2.18 block clones (audit finding D): a block is a contiguous run of
# at least this many normalized identical lines appearing at a second
# location - the shape Qlty and SonarQube count as duplicated blocks,
# distinct from the repeated-line density the old metric reports. Six
# normalized lines is deliberately tighter than SonarQube's ten-
# statement default: line normalization (not tokenization) is the
# engine's contract, and the budget carries the sensitivity.
MIN_BLOCK_LINES = 6
MAX_BUCKET_PAIRS = 200  # anchor comparisons per identical-line bucket, bounded

MAX_LANGS_RENDER = 4
MAX_FILES_RENDER = 4
MAX_LINE_RENDER = 60
MAX_COVERAGE_NAMES = 8  # parse-failed/unsupported names named before cutoff

# Generated-code policy: name-matched generated files leave the corpus.
GENERATED_NAME = re.compile(
    r"(?:_pb2\.py|_pb2\.go|_gen\.go|\.g\.go|_generated\.py|\.generated\.[a-z]+)$"
)


@dataclass(frozen=True)
class DensityCoverage:
    """What the corpus actually covered (v2.18, audit finding E).

    The five-state taxonomy the new-code gate renders, carried to the
    repo-wide scan: a file the lexer rejects or a language with no
    lexer must never vanish into an apparently clean ratio. Counts
    always speak; the names are bounded render material.
    """

    eligible: int = 0  # source files the corpus tried to measure
    measured: int = 0  # parsed and priced
    parse_failed: tuple[str, ...] = ()  # eligible but the lexer rejected them
    excluded_test: int = 0  # documented exclusion: test paths
    excluded_generated: int = 0  # documented exclusion: generated names
    excluded_ignored: int = 0  # documented exclusion: ignore globs
    excluded_nonsource: int = 0  # documented exclusion: docs/configs/assets
    unsupported: int = 0  # no lexer for the extension
    unsupported_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class DensityFacts:
    """One project's measured corpus: two numerators, one denominator."""

    files: int
    exec_lines: int
    complexity: int
    duplicated: int
    per_lang: dict[str, tuple[int, int]]  # lang -> (exec lines, complexity)
    worst_files: tuple[tuple[str, int], ...]  # (rel path, complexity), desc
    worst_line: tuple[str, int, str] | None  # (path, line, text) most repeated
    # v2.18 block clones (audit finding D): lines inside a contiguous
    # repeated run, distinct from the repeated-line count above.
    dup_block_lines: int = 0
    worst_block: tuple[int, str, int] | None = None  # (run length, path, line)


def _block_clones(
    seqs: list[tuple[str, list[tuple[int, str]]]],
) -> tuple[int, tuple[int, str, int] | None]:
    """Lines inside contiguous repeated runs, and the longest run's location.

    Every normalized line indexes into a bucket of positions; each
    later position extends the bucket's anchor into a run while the two
    sequences agree. Runs of MIN_BLOCK_LINES or more mark every line
    they cover on both sides. Pairing against the bucket's first
    position keeps the work bounded (MAX_BUCKET_PAIRS extensions per
    bucket) and deterministic; it is a documented bound, not a full
    clone-pair enumeration - token-level or AST-level clone detection
    is the language-native roadmap row, not this metric.
    """
    buckets: dict[str, list[tuple[int, int]]] = {}
    for file_idx, (_rel, lines) in enumerate(seqs):
        for seq_idx, (_lineno, norm) in enumerate(lines):
            buckets.setdefault(norm, []).append((file_idx, seq_idx))
    covered: set[tuple[int, int]] = set()
    worst: tuple[int, str, int] | None = None  # (run length, rel, start lineno)
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        anchor = bucket[0]
        a_file, a_idx = anchor
        for other in bucket[1 : MAX_BUCKET_PAIRS + 1]:
            b_file, b_idx = other
            if (a_file, a_idx) == (b_file, b_idx):
                continue
            run = 0
            while (
                a_idx + run < len(seqs[a_file][1])
                and b_idx + run < len(seqs[b_file][1])
                and seqs[a_file][1][a_idx + run][1] == seqs[b_file][1][b_idx + run][1]
            ):
                run += 1
            if run >= MIN_BLOCK_LINES:
                for k in range(run):
                    covered.add((a_file, a_idx + k))
                    covered.add((b_file, b_idx + k))
                if worst is None or run > worst[0]:
                    worst = (run, seqs[a_file][0], seqs[a_file][1][a_idx][0])
    return len(covered), worst


def _dup_lines(text: str) -> list[tuple[int, str]]:
    """Candidate duplication lines: stripped, non-comment, long enough.

    The same line filters as the boilerplate-duplication check, tracked
    with their line numbers so the evidence can carry a location.
    """
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "*", "<!--")):
            continue
        if len(line) >= DUP_LINE_MIN_LEN:
            out.append((lineno, line))
    return out


def measure_corpus(
    root: Path, files: list[Path], globs: list[str]
) -> tuple[DensityFacts | None, DensityCoverage]:
    """Both densities plus the corpus's coverage states (v2.18, finding E).

    Every discovered file lands in exactly one named state - measured,
    parse-failed, excluded by a documented rule, or unsupported
    language - so a degraded corpus can never read as a clean one:
    the coverage rides with the facts, and the verdicts' callers render
    it. Returns (facts, coverage); facts is None when nothing
    measurable stands (empty corpus, or under MIN_CORPUS_LINES).
    """
    failed: list[str] = []
    unsupported: list[str] = []
    coverage = {
        "eligible": 0,
        "measured": 0,
        "excluded_test": 0,
        "excluded_generated": 0,
        "excluded_ignored": 0,
        "excluded_nonsource": 0,
        "unsupported": 0,
    }
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    per_lang: dict[str, tuple[int, int]] = {}
    worst: list[tuple[str, int]] = []
    seqs: list[tuple[str, list[tuple[int, str]]]] = []
    exec_lines = complexity = 0
    for path in files:
        ext = path.suffix.lower()
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if not ext or ext in EXCLUDED_EXTS:
            coverage["excluded_nonsource"] += 1
            continue
        if is_ignored(rel, globs):
            coverage["excluded_ignored"] += 1
            continue
        if is_test_path(rel):
            coverage["excluded_test"] += 1
            continue
        if GENERATED_NAME.search(path.name):
            coverage["excluded_generated"] += 1
            continue
        if ext not in LIZARD_EXTS:
            coverage["unsupported"] += 1
            if len(unsupported) < MAX_COVERAGE_NAMES:
                unsupported.append(rel)
            continue
        coverage["eligible"] += 1
        text = read_text_lax(path)
        facts = parse_file_facts(str(path), text)
        if facts is None:
            if len(failed) < MAX_COVERAGE_NAMES:
                failed.append(rel)
            continue  # no parse: the file leaves both sides of the ratio
        coverage["measured"] += 1
        exec_lines += facts.nloc
        complexity += facts.ccn
        lang = ext.lstrip(".")
        seen_n, seen_c = per_lang.get(lang, (0, 0))
        per_lang[lang] = (seen_n + facts.nloc, seen_c + facts.ccn)
        if facts.ccn:
            worst.append((rel, facts.ccn))
        seq = _dup_lines(text)
        seqs.append((rel, seq))
        for lineno, line in seq:
            counter[line] += 1
            first_seen.setdefault(line, (rel, lineno))
    report = DensityCoverage(
        eligible=coverage["eligible"],
        measured=coverage["measured"],
        parse_failed=tuple(failed),
        excluded_test=coverage["excluded_test"],
        excluded_generated=coverage["excluded_generated"],
        excluded_ignored=coverage["excluded_ignored"],
        excluded_nonsource=coverage["excluded_nonsource"],
        unsupported=coverage["unsupported"],
        unsupported_names=tuple(unsupported),
    )
    if not exec_lines or exec_lines < MIN_CORPUS_LINES:
        return None, report
    duplicated = sum(n for n in counter.values() if n >= 2)
    block_lines, worst_run = _block_clones(seqs)
    worst_line: tuple[str, int, str] | None = None
    if counter:
        top, top_count = max(counter.items(), key=lambda kv: kv[1])
        if top_count >= 2:
            seen_rel, seen_lineno = first_seen[top]
            worst_line = (seen_rel, seen_lineno, top)
    return (
        DensityFacts(
            files=coverage["measured"],
            exec_lines=exec_lines,
            complexity=complexity,
            duplicated=duplicated,
            per_lang=per_lang,
            worst_files=tuple(sorted(worst, key=lambda item: -item[1])),
            worst_line=worst_line,
            dup_block_lines=block_lines,
            worst_block=worst_run,
        ),
        report,
    )


def measure(root: Path, files: list[Path], globs: list[str]) -> DensityFacts | None:
    """Both densities over one corpus; None when nothing measurable stands.

    An empty corpus, a corpus the lexer cannot read, and a corpus under
    MIN_CORPUS_LINES all return None: no verdict, not a guessed one.
    Callers that need the coverage states (audit finding E) call
    measure_corpus directly and render them.
    """
    return measure_corpus(root, files, globs)[0]


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


def _coverage_finding(coverage: DensityCoverage) -> Finding | None:
    """The corpus's states, advisory, whenever analysis coverage is not total.

    Audit finding E: a parser failure or an unsupported language must
    never vanish into an apparently clean ratio. The finding names the
    failed and unsupported files (bounded) and counts every documented
    exclusion; a fully measured corpus stays silent.
    """
    if not coverage.parse_failed and not coverage.unsupported:
        return None
    parts = [f"{coverage.measured} measured"]
    if coverage.parse_failed:
        named = ", ".join(f"`{name}`" for name in coverage.parse_failed[:MAX_COVERAGE_NAMES])
        parts.append(f"{len(coverage.parse_failed)} parse-failed ({named})")
    if coverage.unsupported:
        named = ", ".join(f"`{name}`" for name in coverage.unsupported_names[:MAX_COVERAGE_NAMES])
        more = "" if len(coverage.unsupported_names) < MAX_COVERAGE_NAMES else "..."
        parts.append(f"{coverage.unsupported} unsupported language ({named}{more})")
    excluded = (
        coverage.excluded_test
        + coverage.excluded_generated
        + coverage.excluded_ignored
        + coverage.excluded_nonsource
    )
    if excluded:
        parts.append(f"{excluded} excluded by rule (tests/generated/ignore/non-source)")
    anchor = coverage.parse_failed[0] if coverage.parse_failed else "."
    return Finding(
        path=anchor,
        line=1,
        check="analysis-coverage",
        message="corpus analysis coverage: " + "; ".join(parts),
        weight=2,
        advisory=True,
    )


def check_analysis_coverage(coverage: DensityCoverage) -> list[Finding]:
    """The one-shot coverage verdict: silent when every eligible file measured."""
    finding = _coverage_finding(coverage)
    return [finding] if finding else []
