#!/usr/bin/env python3
"""Command-line interface for the master-ci integrity engine.

A linter, not an oracle. It reports deterministic signals that correlate
with machine-written filler and with agent-introduced defects: telltale
vocabulary in comments, emoji padding, elided code, stub functions, empty
tests, swallowed exceptions, phantom and undeclared imports, debug residue,
placeholder credentials, plus - advisory only - prose rhythm patterns in
documents and UI-gradient slop in frontend files.

Design borrowed in spirit from three public slop detectors: flamehaven01's
structural deficit model (purity = exp(-0.5 * blocking weight), per-file
deficit, 0.6*worst + 0.4*mean aggregation, CLEAN/SUSPICIOUS/INFLATED/
CRITICAL verdicts), FixMySlop's rule-with-remedy + baseline + SARIF flow,
and slopdetector.org's explainable, evidence-first prose checks.

Contract:
- Exit code 1 when the *blocking* score exceeds --max-score, when the
    AI-integrity dimension score exceeds --max-slop-score (the
    scanaislop-style PR gate; default 100 keeps the CLI advisory-only),
    or when a requested coverage gate is breached (--min-coverage,
    --min-new-coverage).
- Exit code 1 also when the quality ratchet regresses: a committed
    --quality-baseline (schema v2) records per-project per-check finding
    counts plus the finding identities, and any project pushed above
    its recorded count fails the run; the identity ledger reports
    new / resolved / reintroduced findings beside the counts, so
    swapping one finding for another at a flat total is visible churn,
    not an invisible pass.
- Exit code 2 when configuration cannot be honored: an architecture
    config that is present but invalid, a coverage gate that cannot
    be evaluated (no report, an unparseable report, a missing diff
    base), a quality baseline that cannot be interpreted (missing
    file, invalid JSON, wrong schema version, unknown check ids), or
    --require-parsers set while an analyzer (lizard, tree-sitter) is
    not importable.
    Fail-closed: a rule set or gate that cannot be evaluated fails the
    run instead of being silently skipped.
- Blocking-grade signals are deterministic content defects (elided code,
    stubs, empty tests, swallowed exceptions, chatbot residue in comments);
    --enforce-bloat adds the structural-budget family (file size, parameter
    count, comment ratio, banners, function length, nesting, chained
    dict-gets) to the blocking set for PR-gating enforcement.
    Everything stylistic or speculative is advisory: reported and annotated,
    never failing a build on its own - advisory pressure is scored through
    the deficit model (per-rule saturation cap, impact tiers) and gates
    through the slop-score ceiling instead.
- Provenance signals (`ai-provenance`) and the test inventory
    (`missing-tests`) are traceability signals, never authorship verdicts;
    advisory. Disable with `--no-provenance`.
- Every finding carries a check id and a remediation hint.
- Repos opt out per path or per check via `.ai-integrity-ignore` (glob
    lines, or `check:<id>` lines), and can accept existing findings via
    `--baseline` / record them with `--write-baseline`.
- `--sarif out.sarif` emits SARIF 2.1.0 for the GitHub Security tab.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coverage_gate import CoverageGateError, run_gate
from slop_arch import ArchConfigError
from slop_cognitive import tree_sitter_available
from slop_files import load_phrases
from slop_lizard import lizard_available
from slop_model import Finding, enforce_structural_budget
from slop_ratchet import (
    RatchetError,
    compare,
    load_baseline as load_quality_baseline,
    render as render_ratchet,
    scan_state,
    write_baseline as write_quality_baseline_file,
)
from slop_reporting import (
    advisory_score,
    ai_integrity_score,
    annotate,
    apply_baseline,
    blocking_score,
    category_breakdown,
    write_baseline,
    write_report,
    write_sarif,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Repo path to scan")
    _add_gate_args(parser)
    _add_baseline_args(parser)
    _add_coverage_args(parser)
    return parser.parse_args(argv)


def _add_gate_args(parser: argparse.ArgumentParser) -> None:
    """The enforcement knobs: score caps, bloat escalation, provenance."""
    parser.add_argument(
        "--max-score",
        type=float,
        default=8,
        help="Fail when the blocking score exceeds this value",
    )
    parser.add_argument(
        "--max-slop-score",
        type=float,
        default=100.0,
        help=(
            "Fail when the AI-integrity dimension score (0=clean, 100=awful) "
            "exceeds this ceiling; the scanaislop-style PR gate. Code-quality "
            "dimensions are reported separately and never feed this gate. "
            "Default 100 = off"
        ),
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Emit GitHub ::warning annotations on stdout",
    )
    parser.add_argument(
        "--enforce-bloat",
        action="store_true",
        help=(
            "Escalate the structural-budget family (bloat-long-file, "
            "bloat-param-count, bloat-comment-ratio, bloat-banner-comments, "
            "god-function, deep-nesting, chained-dict-get) from advisory to "
            "blocking: these findings feed the blocking score and fail the "
            "build above --max-score. The PR gate for file-size, parameter, "
            "comment-ratio and nesting budgets"
        ),
    )
    parser.add_argument(
        "--provenance",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Report advisory provenance markers (ai-provenance) and the "
            "repository test inventory (missing-tests); default on"
        ),
    )
    parser.add_argument(
        "--require-parsers",
        action="store_true",
        help=(
            "Fail closed (exit 2) when the lizard / tree-sitter analyzers "
            "are not importable. CI jobs that promise parser-backed "
            "dimensions install the pinned analyzers and demand them: a "
            "parser-backed scan must never silently degrade to line "
            "heuristics"
        ),
    )


def _add_baseline_args(parser: argparse.ArgumentParser) -> None:
    """The two baselines: fingerprint suppression and the quality ratchet."""
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip files matching this glob (repeatable, also via .ai-integrity-ignore)",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Suppress findings recorded in this baseline file",
    )
    parser.add_argument(
        "--write-baseline",
        default="",
        help="Record current findings as the new baseline and exit 0",
    )
    parser.add_argument(
        "--quality-baseline",
        default="",
        metavar="PATH",
        help=(
            "Enforce the quality ratchet against this committed baseline: "
            "per-project per-check finding counts and the finding "
            "identities (schema v2); any per-project increase fails "
            "(exit 1), a baseline that cannot be interpreted fails closed "
            "(exit 2). Counts what the scan reports, before --baseline "
            "suppression"
        ),
    )
    parser.add_argument(
        "--write-quality-baseline",
        default="",
        metavar="PATH",
        help=(
            "Record current per-project counts and identities as the new "
            "quality-ratchet floor and exit 0 - the reviewed escape hatch "
            "after paying debt or adopting a new check catalog (v1 "
            "baselines are rejected; re-record)"
        ),
    )


def _add_coverage_args(parser: argparse.ArgumentParser) -> None:
    """The measured-coverage gates: floors, diff window, report paths."""
    parser.add_argument(
        "--report-file",
        default="",
        help="Write a markdown report to this path",
    )
    parser.add_argument(
        "--sarif",
        default="",
        help="Write SARIF 2.1.0 results to this path (for the Security tab)",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.0,
        help=(
            "Fail (exit 1) when measured coverage in a coverage.json / "
            "coverage.xml / cover.out report is below this percent. With "
            "no report found the gate cannot be evaluated and the run "
            "fails closed (exit 2). Default 0 = off"
        ),
    )
    parser.add_argument(
        "--min-new-coverage",
        type=float,
        default=0.0,
        help=(
            "Fail (exit 1) when coverage over the lines added vs "
            "--diff-base is below this percent; per-file breaches are "
            "reported as low-new-coverage findings. Default 0 = off"
        ),
    )
    parser.add_argument(
        "--diff-base",
        default="",
        metavar="REF",
        help=(
            "Git ref defining the new-code window (merge-base diff, e.g. "
            "origin/main); required when --min-new-coverage is set"
        ),
    )
    parser.add_argument(
        "--coverage-report",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Coverage report to consume (repeatable); default: "
            "auto-discover coverage.json, coverage.xml, cover.out at the root"
        ),
    )


def _coverage_gate(args: argparse.Namespace, findings: list) -> tuple[int, list[str]]:
    """Run the requested coverage gates; SystemExit(2) when unevaluable.

    Returns (exit contribution, report sections); gate findings are
    appended to `findings` as advisory low-new-coverage entries.
    """
    if args.min_coverage <= 0 and args.min_new_coverage <= 0:
        return 0, []
    try:
        gate = run_gate(
            args.root,
            min_coverage=args.min_coverage,
            min_new_coverage=args.min_new_coverage,
            diff_base=args.diff_base,
            report_paths=args.coverage_report,
        )
    except CoverageGateError as exc:
        # Fail closed: a gate that cannot be evaluated must fail the
        # run, not silently pass. Exit 2 = configuration error.
        print(f"ai-integrity: coverage gate rejected: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    findings.extend(
        Finding(path=path, line=line, check="low-new-coverage", message=message, weight=1)
        for path, line, message in gate.findings
    )
    return gate.exit_code, gate.sections


def _parser_guarantee(args: argparse.Namespace) -> int:
    """--require-parsers: demand the analyzers or fail closed (exit 2).

    CI jobs that promise parse-backed dimensions install the pinned
    analyzers and demand them; a missing tool is the configuration-error
    class, never a weakened, silently passing scan.
    """
    if not args.require_parsers:
        return 0
    missing = [
        name
        for name, ok in (
            ("lizard", lizard_available()),
            ("tree-sitter", tree_sitter_available()),
        )
        if not ok
    ]
    if not missing:
        return 0
    print(
        "ai-integrity: --require-parsers: analyzer(s) not importable: "
        f"{', '.join(missing)} - install the pinned versions from "
        "configs/tool-versions.yml; a parser-backed scan must never "
        "silently degrade to line heuristics",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parser_exit = _parser_guarantee(args)
    if parser_exit:
        return parser_exit
    if args.enforce_bloat:
        # Registry mutation must precede the scan: Finding.__post_init__
        # resolves severity from the enforced set at construction time.
        enforce_structural_budget()
    # Imported here, not at module top: ai_slop_detector re-exports main
    # from this module at its own import time, so a top-level import would
    # be a circular import. By the time main() runs, both modules are loaded.
    from ai_slop_detector import scan

    phrases = load_phrases()
    try:
        findings = scan(args.root, phrases, extra_globs=args.ignore, provenance=args.provenance)
    except ArchConfigError as exc:
        # Fail closed: a declared rule set that cannot be honored must
        # fail the run, not silently pass. Exit 2 = configuration error.
        print(f"ai-integrity: architecture config rejected: {exc}", file=sys.stderr)
        return 2
    ratchet_exit = _quality_ratchet(args, findings)
    if ratchet_exit == 2 or (args.write_quality_baseline and ratchet_exit == 0):
        return ratchet_exit
    suppressed = 0
    if args.baseline:
        findings, suppressed = apply_baseline(findings, args.baseline)
    gate_exit, gate_sections = _coverage_gate(args, findings)
    if args.write_baseline:
        write_baseline(args.write_baseline, findings)
        print(
            f"ai-integrity baseline written: {len(findings)} fingerprint(s) "
            f"-> {args.write_baseline}"
        )
        return 0
    ai_score = _emit_results(args, findings, suppressed, gate_sections)
    failed = (
        blocking_score(findings) > args.max_score
        or ai_score > args.max_slop_score
        or gate_exit == 1
        or ratchet_exit == 1
    )
    return 1 if failed else 0


def _emit_results(
    args: argparse.Namespace,
    findings: list,
    suppressed: int,
    gate_sections: list[str],
) -> int:
    """Write every requested artifact and the one-line score summary.

    Returns the AI-integrity dimension score so main() can judge the
    ceiling against it.
    """
    if args.report_file:
        write_report(
            args.report_file,
            args.root,
            findings,
            suppressed=suppressed,
            extra_sections=gate_sections,
        )
    if args.sarif:
        write_sarif(args.sarif, findings)
    if args.annotate:
        annotate(findings)
    ai_score, ai_grade, ai_verdict = ai_integrity_score(findings)
    dims = " ".join(
        f"{name}={row['grade']}({row['score']})" for name, row in category_breakdown(findings)
    )
    print(
        f"ai-integrity blocking={blocking_score(findings)} max={args.max_score} "
        f"advisory={advisory_score(findings)} findings={len(findings)} "
        f"slop={ai_score}/100 grade={ai_grade} verdict={ai_verdict} "
        f"ceiling={args.max_slop_score:g} dims: {dims}"
        + (" enforce=bloat" if args.enforce_bloat else "")
    )
    return ai_score


def _quality_ratchet(args: argparse.Namespace, findings: list) -> int:
    """Write or enforce the quality ratchet; returns its exit contribution.

    0 = not requested, passed, or the floor was written; 1 = a check
    regressed above its recorded count; 2 = the baseline cannot be
    interpreted (fail-closed configuration error).
    """
    if args.write_quality_baseline:
        counts = write_quality_baseline_file(args.write_quality_baseline, findings, root=args.root)
        projects = len(counts)
        checks = sum(len(per_project) for per_project in counts.values())
        print(
            f"quality ratchet floor written: {projects} project(s), "
            f"{checks} check floor(s) -> {args.write_quality_baseline}"
        )
        return 0
    if not args.quality_baseline:
        return 0
    try:
        floor = load_quality_baseline(args.quality_baseline)
    except RatchetError as exc:
        print(f"ai-integrity: quality ratchet rejected: {exc}", file=sys.stderr)
        return 2
    current = scan_state(findings, root=args.root)
    result = compare(current, floor)
    for line in render_ratchet(current, floor, result):
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
