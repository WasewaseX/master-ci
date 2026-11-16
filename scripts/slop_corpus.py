"""The measured corpus: classification, block clones and coverage states.

v2.18's audit round grew the corpus machinery until the density module
broke its own 400-line file budget and the structural checks flagged
its own measurement loop - the detector eating its own cooking, caught
by the template's selftest. The corpus primitives live here now, split
along their seams: file classification, block-clone detection and the
coverage states. slop_density re-exports the public names, so callers
and tests keep their import surface; the budgets and the verdict
renderers stay there.

Measurement corpus (one corpus, one denominator): source files with a
lexer in slop_lizard, outside test paths, outside the ignore globs, and
outside generated trees. A missing analyzer or a file the lexer rejects
leaves BOTH sides of the ratio - a degraded measurement, never a
guessed one - and the coverage states name it (audit finding E).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from slop_analysis_coverage import EXCLUDED_EXTS
from slop_files import is_ignored, is_test_path, read_text_lax
from slop_lizard import LIZARD_EXTS, parse_file_facts
from slop_model import Finding
from slop_patterns import DUP_LINE_MIN_LEN

# Under MIN_CORPUS_LINES executable lines the corpus is a snippet, not
# a project, and every ratio stays silent.
MIN_CORPUS_LINES = 250

# v2.18 block clones (audit finding D): a block is a contiguous run of
# at least this many normalized identical lines appearing at a second
# location - the shape Qlty and SonarQube count as duplicated blocks,
# distinct from the repeated-line density the old metric reports. Six
# normalized lines is deliberately tighter than SonarQube's ten-
# statement default: line normalization (not tokenization) is the
# engine's contract, and the budget carries the sensitivity.
MIN_BLOCK_LINES = 6
MAX_BUCKET_PAIRS = 200  # anchor comparisons per identical-line bucket, bounded

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


def _file_state(path: Path, root: Path, globs: list[str]) -> tuple[str, str]:
    """One file's corpus state: nonsource, ignored, test, generated,
    unsupported or eligible - with its repo-relative path."""
    ext = path.suffix.lower()
    try:
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
    except ValueError:
        rel = path.as_posix()
    if not ext or ext in EXCLUDED_EXTS:
        return "nonsource", rel
    if is_ignored(rel, globs):
        return "ignored", rel
    if is_test_path(rel):
        return "test", rel
    if GENERATED_NAME.search(path.name):
        return "generated", rel
    if ext not in LIZARD_EXTS:
        return "unsupported", rel
    return "eligible", rel


def _dup_lines(text: str) -> list[tuple[int, str]]:
    """Candidate duplication lines: stripped, non-comment, long enough."""
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "*", "<!--")):
            continue
        if len(line) >= DUP_LINE_MIN_LEN:
            out.append((lineno, line))
    return out


def _line_buckets(
    seqs: list[tuple[str, list[tuple[int, str]]]],
) -> dict[str, list[tuple[int, int]]]:
    """Every normalized line's positions: (file index, index in sequence)."""
    buckets: dict[str, list[tuple[int, int]]] = {}
    for file_idx, (_rel, lines) in enumerate(seqs):
        for seq_idx, (_lineno, norm) in enumerate(lines):
            buckets.setdefault(norm, []).append((file_idx, seq_idx))
    return buckets


def _extend_run(
    seqs: list[tuple[str, list[tuple[int, str]]]], a: tuple[int, int], b: tuple[int, int]
) -> int:
    """How far two positions agree, line by normalized line."""
    a_file, a_idx = a
    b_file, b_idx = b
    run = 0
    while (
        a_idx + run < len(seqs[a_file][1])
        and b_idx + run < len(seqs[b_file][1])
        and seqs[a_file][1][a_idx + run][1] == seqs[b_file][1][b_idx + run][1]
    ):
        run += 1
    return run


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
    buckets = _line_buckets(seqs)
    covered: set[tuple[int, int]] = set()
    worst: tuple[int, str, int] | None = None  # (run length, rel, start lineno)
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        anchor = bucket[0]
        for other in bucket[1 : MAX_BUCKET_PAIRS + 1]:
            run = _extend_run(seqs, anchor, other)
            if run < MIN_BLOCK_LINES:
                continue
            a_file, a_idx = anchor
            for k in range(run):
                covered.add((a_file, a_idx + k))
                covered.add((other[0], other[1] + k))
            if worst is None or run > worst[0]:
                worst = (run, seqs[a_file][0], seqs[a_file][1][a_idx][0])
    return len(covered), worst


class _CorpusAccumulator:
    """The per-file accumulation: counters, sequences and coverage counts."""

    def __init__(self) -> None:
        self.counter: Counter[str] = Counter()
        self.first_seen: dict[str, tuple[str, int]] = {}
        self.per_lang: dict[str, tuple[int, int]] = {}
        self.worst: list[tuple[str, int]] = []
        self.seqs: list[tuple[str, list[tuple[int, str]]]] = []
        self.exec_lines = 0
        self.complexity = 0
        self.counts = {
            "eligible": 0,
            "measured": 0,
            "excluded_test": 0,
            "excluded_generated": 0,
            "excluded_ignored": 0,
            "excluded_nonsource": 0,
            "unsupported": 0,
        }
        self.failed: list[str] = []
        self.unsupported: list[str] = []

    def exclude(self, state: str) -> None:
        """Count one file under a documented exclusion (or unsupported)."""
        if state == "test":
            self.counts["excluded_test"] += 1
        elif state == "generated":
            self.counts["excluded_generated"] += 1
        elif state == "ignored":
            self.counts["excluded_ignored"] += 1
        elif state == "nonsource":
            self.counts["excluded_nonsource"] += 1
        elif state == "unsupported":
            self.counts["unsupported"] += 1

    def name_unsupported(self, rel: str) -> None:
        if len(self.unsupported) < MAX_COVERAGE_NAMES:
            self.unsupported.append(rel)

    def name_failed(self, rel: str) -> None:
        if len(self.failed) < MAX_COVERAGE_NAMES:
            self.failed.append(rel)

    def accumulate(self, rel: str, ext: str, text: str, path: Path) -> None:
        """One measured file: lines, complexity, language, dup candidates."""
        facts = parse_file_facts(str(path), text)
        if facts is None:
            self.name_failed(rel)
            return  # no parse: the file leaves both sides of the ratio
        self.counts["measured"] += 1
        self.exec_lines += facts.nloc
        self.complexity += facts.ccn
        lang = ext.lstrip(".")
        seen_n, seen_c = self.per_lang.get(lang, (0, 0))
        self.per_lang[lang] = (seen_n + facts.nloc, seen_c + facts.ccn)
        if facts.ccn:
            self.worst.append((rel, facts.ccn))
        seq = _dup_lines(text)
        self.seqs.append((rel, seq))
        for lineno, line in seq:
            self.counter[line] += 1
            self.first_seen.setdefault(line, (rel, lineno))

    def coverage(self) -> DensityCoverage:
        return DensityCoverage(
            eligible=self.counts["eligible"],
            measured=self.counts["measured"],
            parse_failed=tuple(self.failed),
            excluded_test=self.counts["excluded_test"],
            excluded_generated=self.counts["excluded_generated"],
            excluded_ignored=self.counts["excluded_ignored"],
            excluded_nonsource=self.counts["excluded_nonsource"],
            unsupported=self.counts["unsupported"],
            unsupported_names=tuple(self.unsupported),
        )

    def facts(self) -> DensityFacts | None:
        """The assembled facts, or None under the minimum-corpus floor."""
        if not self.exec_lines or self.exec_lines < MIN_CORPUS_LINES:
            return None
        duplicated = sum(n for n in self.counter.values() if n >= 2)
        block_lines, worst_run = _block_clones(self.seqs)
        worst_line: tuple[str, int, str] | None = None
        if self.counter:
            top, top_count = max(self.counter.items(), key=lambda kv: kv[1])
            if top_count >= 2:
                seen_rel, seen_lineno = self.first_seen[top]
                worst_line = (seen_rel, seen_lineno, top)
        return DensityFacts(
            files=self.counts["measured"],
            exec_lines=self.exec_lines,
            complexity=self.complexity,
            duplicated=duplicated,
            per_lang=self.per_lang,
            worst_files=tuple(sorted(self.worst, key=lambda item: -item[1])),
            worst_line=worst_line,
            dup_block_lines=block_lines,
            worst_block=worst_run,
        )


def measure_corpus(
    root: Path, files: list[Path], globs: list[str]
) -> tuple[DensityFacts | None, DensityCoverage]:
    """Both densities plus the corpus's coverage states (v2.18, finding E).

    Every discovered file lands in exactly one named state - measured,
    parse-failed, excluded by a documented rule, or unsupported
    language - so a degraded corpus can never read as a clean one.
    Returns (facts, coverage); facts is None when nothing measurable
    stands (an empty corpus, or under MIN_CORPUS_LINES).
    """
    acc = _CorpusAccumulator()
    for path in files:
        state, rel = _file_state(path, root, globs)
        if state != "eligible":
            acc.exclude(state)
            if state == "unsupported":
                acc.name_unsupported(rel)
            continue
        acc.counts["eligible"] += 1
        acc.accumulate(rel, path.suffix.lower(), read_text_lax(path), path)
    return acc.facts(), acc.coverage()


def measure(root: Path, files: list[Path], globs: list[str]) -> DensityFacts | None:
    """Both densities over one corpus; None when nothing measurable stands."""
    return measure_corpus(root, files, globs)[0]


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
