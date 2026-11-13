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
from dataclasses import dataclass
from pathlib import Path

from slop_files import is_ignored, is_test_path, read_text_lax
from slop_lizard import LIZARD_EXTS, parse_file_facts
from slop_model import Finding
from slop_patterns import DUP_LINE_MIN_LEN

# Budgets calibrated at v2.13 against this codebase: 247/1000
# complexity, 2.3% duplication over ~11k non-test lines - the bars sit
# above the engine's own numbers, advisory evidence, never a gate.
COMPLEXITY_DENSITY_BUDGET = 300  # decision points per 1000 executable lines
DUP_DENSITY_BUDGET = 5.0  # duplicated executable lines, percent of corpus
MIN_CORPUS_LINES = 250  # under this the corpus is a snippet, not a project

MAX_LANGS_RENDER = 4
MAX_FILES_RENDER = 4
MAX_LINE_RENDER = 60

# Generated-code policy: name-matched generated files leave the corpus.
GENERATED_NAME = re.compile(
    r"(?:_pb2\.py|_pb2\.go|_gen\.go|\.g\.go|_generated\.py|\.generated\.[a-z]+)$"
)


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


def _eligible(root: Path, files: list[Path], globs: list[str]) -> list[tuple[Path, str]]:
    """The measured corpus: lexer-backed sources, no tests, no generated."""
    out: list[tuple[Path, str]] = []
    for path in files:
        if path.suffix.lower() not in LIZARD_EXTS:
            continue
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs) or is_test_path(rel) or GENERATED_NAME.search(path.name):
            continue
        out.append((path, rel))
    return sorted(out, key=lambda pair: pair[1])


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


def measure(root: Path, files: list[Path], globs: list[str]) -> DensityFacts | None:
    """Both densities over one corpus; None when nothing measurable stands.

    An empty corpus, a corpus the lexer cannot read, and a corpus under
    MIN_CORPUS_LINES all return None: no verdict, not a guessed one.
    """
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    per_lang: dict[str, tuple[int, int]] = {}
    worst: list[tuple[str, int]] = []
    exec_lines = complexity = n_files = 0
    for path, rel in _eligible(root, files, globs):
        text = read_text_lax(path)
        facts = parse_file_facts(str(path), text)
        if facts is None:
            continue  # no parse: the file leaves both sides of the ratio
        n_files += 1
        exec_lines += facts.nloc
        complexity += facts.ccn
        lang = path.suffix.lower().lstrip(".")
        seen_n, seen_c = per_lang.get(lang, (0, 0))
        per_lang[lang] = (seen_n + facts.nloc, seen_c + facts.ccn)
        if facts.ccn:
            worst.append((rel, facts.ccn))
        for lineno, line in _dup_lines(text):
            counter[line] += 1
            first_seen.setdefault(line, (rel, lineno))
    if not exec_lines or exec_lines < MIN_CORPUS_LINES:
        return None
    duplicated = sum(n for n in counter.values() if n >= 2)
    worst_line: tuple[str, int, str] | None = None
    if counter:
        top, top_count = max(counter.items(), key=lambda kv: kv[1])
        if top_count >= 2:
            seen_rel, seen_lineno = first_seen[top]
            worst_line = (seen_rel, seen_lineno, top)
    return DensityFacts(
        files=n_files,
        exec_lines=exec_lines,
        complexity=complexity,
        duplicated=duplicated,
        per_lang=per_lang,
        worst_files=tuple(sorted(worst, key=lambda item: -item[1])),
        worst_line=worst_line,
    )


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


def _complexity_finding(facts: DensityFacts) -> Finding:
    density = 1000 * facts.complexity / facts.exec_lines
    anchor = facts.worst_files[0][0] if facts.worst_files else "."
    message = (
        f"complexity density {density:.0f} per 1000 executable lines "
        f"(budget {COMPLEXITY_DENSITY_BUDGET}): {facts.complexity} decision points "
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


def _duplication_finding(facts: DensityFacts) -> Finding:
    pct = 100 * facts.duplicated / facts.exec_lines
    evidence = ""
    anchor_path, anchor_line = ".", 1
    if facts.worst_line:
        seen_rel, seen_lineno, text = facts.worst_line
        evidence = f"; most repeated: {text[:MAX_LINE_RENDER]}... ({seen_rel}:{seen_lineno})"
        anchor_path, anchor_line = seen_rel, seen_lineno
    message = (
        f"duplication density {pct:.1f}% (budget {DUP_DENSITY_BUDGET}%): "
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


def density_findings(facts: DensityFacts) -> list[Finding]:
    """The two density findings for a measured corpus, over budget only.

    Takes the measured facts rather than a root: the pipeline computes
    the corpus once and shares it with the debt ratio, so every ratio
    in the report divides by the same denominator.
    """
    findings: list[Finding] = []
    if 1000 * facts.complexity / facts.exec_lines > COMPLEXITY_DENSITY_BUDGET:
        findings.append(_complexity_finding(facts))
    if 100 * facts.duplicated / facts.exec_lines > DUP_DENSITY_BUDGET:
        findings.append(_duplication_finding(facts))
    return findings
