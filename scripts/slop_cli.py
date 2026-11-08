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
- Exit code 1 when the *blocking* score exceeds --max-score, or when the
    AI-integrity dimension score exceeds --max-slop-score (the
    scanaislop-style PR gate; default 100 keeps the CLI advisory-only).
- Exit code 2 when an architecture config is present but invalid: the
    config is fail-closed, so a rule set that cannot be honored fails
    the run instead of being silently skipped.
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

from slop_arch import ArchConfigError
from slop_files import load_phrases
from slop_model import enforce_structural_budget
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
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip files matching this glob (repeatable, also via .ai-integrity-ignore)",
    )
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
    suppressed = 0
    if args.baseline:
        findings, suppressed = apply_baseline(findings, args.baseline)
    if args.write_baseline:
        write_baseline(args.write_baseline, findings)
        print(
            f"ai-integrity baseline written: {len(findings)} fingerprint(s) "
            f"-> {args.write_baseline}"
        )
        return 0
    if args.report_file:
        write_report(args.report_file, args.root, findings, suppressed=suppressed)
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
    failed = blocking_score(findings) > args.max_score or ai_score > args.max_slop_score
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
