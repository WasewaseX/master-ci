#!/usr/bin/env python3
"""New-code quality gates: dimension ceilings on the changed lines.

Roadmap item 7. The coverage gate (v1.21) proved test assurance on the
lines a change adds; this module extends the same merge-base model to
the other gated dimensions - a Sonar-style quality gate on new code.
Legacy files keep their history, but every added line must meet
today's bar:

- ``--max-slop-score``: the blocking score of detector findings that
    land on added lines (zero tolerance by default in CI).
- ``--max-cognitive``: cognitive complexity of functions the change
    touches (a Sonar-style reader-effort measure).
- ``--max-cyclomatic``: cyclomatic complexity of touched functions.

A function counts as touched when the diff's added lines intersect its
span, approximated as ``[start, max(start + nloc, next start))`` - an
over-approximation on purpose: a gate may over-flag a legacy neighbor,
never miss a new offender.

Contract:
- exit 0  every requested ceiling holds (or no ceiling requested)
- exit 1  a ceiling is breached by code the change added
- exit 2  a requested gate cannot be evaluated: no diff base, git
    failed, the parser a requested ceiling needs is not importable
    (v2.5: the gate demands lizard for cyclomatic and tree-sitter for
    cognitive instead of silently reporting zero touched functions), or
    a changed file that a requested ceiling's own parser failed to
    parse (v2.8: the analysis-coverage pass - the hosted canary PR run
    showed a parse failure was indistinguishable from "no functions
    here"; unanalyzed must never read as clean). Fail-closed - a gate
    that cannot be evaluated never silently passes, same semantics as
    the coverage gate.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_slop_detector import scan
from coverage_gate import git_changed_lines
from slop_analysis_coverage import analysis_coverage, coverage_section
from slop_cognitive import cognitive_facts, tree_sitter_available
from slop_files import load_phrases, read_text_lax
from slop_lizard import FunctionFact, lizard_available, parse_functions
from slop_model import Finding
from slop_reporting import MAX_ANNOTATIONS, annotate, blocking_score


class NewCodeGateError(Exception):
    """A requested gate cannot be evaluated - maps to exit 2."""


@dataclass(frozen=True)
class TouchedFunction:
    """One parsed function whose span meets the diff's added lines."""

    path: str
    name: str
    line: int
    ccn: int
    cognitive: int | None  # None when no tree-sitter fact exists for it


def _rel(path: str, root: Path) -> str:
    """Finding paths are repo-relative or absolute; the diff uses rel/posix."""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError:
            return ""
    return candidate.as_posix()


def findings_on_new_lines(
    root: Path, findings: list[Finding], changed: dict[str, set[int]]
) -> list[Finding]:
    """Findings whose (file, line) lands on a line the change added."""
    kept: list[Finding] = []
    for item in findings:
        lines = changed.get(_rel(item.path, root))
        if lines and item.line in lines:
            kept.append(item)
    return kept


def touched_functions(root: Path, changed: dict[str, set[int]]) -> list[TouchedFunction]:
    """Parsed functions whose span intersects the diff's added lines."""
    touched: list[TouchedFunction] = []
    for rel, lines in sorted(changed.items()):
        touched.extend(_file_touched_functions(root / rel, rel, lines))
    return touched


def _cognitive_scores(path: Path, text: str) -> dict[int, int]:
    facts = cognitive_facts(path.suffix.lower(), text) or []
    return {fact.line: fact.score for fact in facts}


def _span_end(fact: FunctionFact, index: int, ordered: list[FunctionFact]) -> int:
    """Over-approximated physical span: nloc, stretched to the next fact."""
    end = fact.line + fact.nloc
    if index + 1 < len(ordered):
        end = max(end, ordered[index + 1].line - 1)
    return end


def _file_touched_functions(path: Path, rel: str, lines: set[int]) -> list[TouchedFunction]:
    if not path.is_file():
        return []
    text = read_text_lax(path)
    facts = parse_functions(str(path), text)
    if not facts:
        return []
    cognitive = _cognitive_scores(path, text)
    ordered = sorted(facts, key=lambda fact: fact.line)
    touched: list[TouchedFunction] = []
    for index, fact in enumerate(ordered):
        end = _span_end(fact, index, ordered)
        if not any(fact.line <= line < end for line in lines):
            continue
        touched.append(
            TouchedFunction(rel, fact.name, fact.line, fact.ccn, cognitive.get(fact.line))
        )
    return touched


def run_gate(
    root: Path,
    *,
    diff_base: str,
    max_slop: int,
    max_cognitive: int,
    max_cyclomatic: int,
) -> tuple[int, list[str], list[Finding]]:
    """Evaluate the requested ceilings; raise NewCodeGateError on unevaluable.

    Returns (exit code, markdown sections, new-line findings for the
    annotation pass).
    """
    requested = max_slop != 100 or max_cognitive > 0 or max_cyclomatic > 0
    if not requested:
        return 0, [], []
    # The v2.5 parser guarantee: a requested ceiling is a promise to
    # measure, and a missing analyzer would turn the promise into a
    # silent "0 functions touched" pass. Refuse before doing any work.
    if max_cyclomatic > 0 and not lizard_available():
        raise NewCodeGateError(
            "a cyclomatic ceiling is requested but lizard is not importable - "
            "install the pinned analyzers (configs/tool-versions.yml); the "
            "gate refuses to pass on a metric it could not measure"
        )
    if max_cognitive > 0 and not tree_sitter_available():
        raise NewCodeGateError(
            "a cognitive ceiling is requested but tree-sitter is not "
            "importable - install the pinned analyzers "
            "(configs/tool-versions.yml); the gate refuses to pass on a "
            "metric it could not measure"
        )
    if not diff_base:
        raise NewCodeGateError("new-code gate requested but --diff-base is empty")
    try:
        changed = git_changed_lines(root, diff_base)
    except Exception as exc:  # noqa: BLE001 - the gate maps any git failure to exit 2
        raise NewCodeGateError(f"cannot diff against {diff_base!r}: {exc}") from exc
    sections: list[str] = []
    if max_cognitive > 0 or max_cyclomatic > 0:
        # Analysis coverage (v2.8): a file the requested parser cannot
        # read is fatal here - downstream it would degrade into "no
        # functions touched", the silent pass v2.5 exists to prevent.
        coverage = analysis_coverage(
            root,
            changed,
            want_cognitive=max_cognitive > 0,
            want_cyclomatic=max_cyclomatic > 0,
        )
        if coverage.parse_failed:
            shown = ", ".join(coverage.parse_failed[:10])
            more = (
                f" (+{len(coverage.parse_failed) - 10} more)"
                if len(coverage.parse_failed) > 10
                else ""
            )
            raise NewCodeGateError(
                "a requested ceiling's parser could not measure every changed "
                f"file - parse failures: {shown}{more}; fix the syntax or "
                "document an exclusion, the gate refuses to treat unanalyzed "
                "code as clean"
            )
        sections.append(coverage_section(coverage))
    exit_code, slop_sections, findings = _slop_ceiling(root, changed, max_slop)
    complexity_exit, complexity_sections = _complexity_ceiling(
        root, changed, max_cognitive, max_cyclomatic
    )
    sections += slop_sections + complexity_sections
    exit_code = max(exit_code, complexity_exit)
    return exit_code, sections, findings


def _slop_ceiling(
    root: Path, changed: dict[str, set[int]], max_slop: int
) -> tuple[int, list[str], list[Finding]]:
    if max_slop == 100:
        return 0, [], []
    findings = findings_on_new_lines(root, scan(root, load_phrases()), changed)
    score = blocking_score(findings)
    exit_code = 1 if score > max_slop else 0
    return exit_code, [_slop_section(findings, score, max_slop)], findings


def _complexity_ceiling(
    root: Path, changed: dict[str, set[int]], max_cognitive: int, max_cyclomatic: int
) -> tuple[int, list[str]]:
    if max_cognitive <= 0 and max_cyclomatic <= 0:
        return 0, []
    touched = touched_functions(root, changed)
    exit_code = 0
    for item in touched:
        if max_cognitive > 0 and item.cognitive is not None and item.cognitive > max_cognitive:
            exit_code = 1
        if max_cyclomatic > 0 and item.ccn > max_cyclomatic:
            exit_code = 1
    return exit_code, [_complexity_section(touched, max_cognitive, max_cyclomatic)]


def _slop_section(findings: list[Finding], score: int, ceiling: int) -> str:
    lines = [
        "### New-code gate: blocking findings on added lines",
        "",
        f"blocking score **{score}** against the {ceiling:g} ceiling "
        f"({len(findings)} finding(s) on added lines).",
        "",
    ]
    if findings:
        lines += ["| Check | Location | Weight | Message |", "|---|---|---|---|"]
        for item in findings[:MAX_ANNOTATIONS]:
            lines.append(
                f"| `{item.check}` | `{item.path}:{item.line}` | {item.weight} | {item.message} |"
            )
        lines.append("")
    return "\n".join(lines)


def _complexity_section(
    touched: list[TouchedFunction], max_cognitive: int, max_cyclomatic: int
) -> str:
    lines = [
        "### New-code gate: complexity of touched functions",
        "",
        f"{len(touched)} function(s) intersect the diff's added lines "
        f"(ceilings: cognitive {max_cognitive or 'off'}, cyclomatic {max_cyclomatic or 'off'}).",
        "",
    ]
    worst = sorted(
        touched,
        key=lambda item: -(item.cognitive if item.cognitive is not None else 0) - item.ccn,
    )[:MAX_ANNOTATIONS]
    if worst:
        lines += [
            "| Function | Location | Cognitive | Cyclomatic |",
            "|---|---|---|---|",
        ]
        for item in worst:
            cognitive = str(item.cognitive) if item.cognitive is not None else "n/a"
            lines.append(
                f"| `{item.name}` | `{item.path}:{item.line}` | {cognitive} | {item.ccn} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="Repo path the diff belongs to")
    parser.add_argument(
        "--diff-base", default="", help="Merge-base ref defining the new-code window"
    )
    parser.add_argument(
        "--max-slop-score",
        type=int,
        default=100,
        help="Blocking-score ceiling for findings on added lines (100 = off, 0 = zero tolerance)",
    )
    parser.add_argument(
        "--max-cognitive", type=int, default=0, help="Cognitive-complexity ceiling (0 = off)"
    )
    parser.add_argument(
        "--max-cyclomatic", type=int, default=0, help="Cyclomatic-complexity ceiling (0 = off)"
    )
    args = parser.parse_args(argv)
    try:
        exit_code, sections, findings = run_gate(
            args.root,
            diff_base=args.diff_base,
            max_slop=args.max_slop_score,
            max_cognitive=args.max_cognitive,
            max_cyclomatic=args.max_cyclomatic,
        )
    except NewCodeGateError as exc:
        print(f"new-code gate rejected: {exc}", file=sys.stderr)
        return 2
    annotate(findings)
    if sections:
        print("\n".join(sections))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
