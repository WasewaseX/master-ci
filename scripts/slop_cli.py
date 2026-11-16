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
    file, invalid JSON, wrong schema version, unknown check ids), an
    analyzer profile that cannot be honored (unreadable file, unknown
    check id, illegal severity), or --require-parsers set while an
    analyzer (lizard, tree-sitter) is not importable.
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

from coverage_gate import coverage_gate_stage
from slop_arch import ArchConfigError
from slop_cognitive import tree_sitter_available
from slop_files import load_phrases
from slop_lizard import lizard_available
from slop_metrics_doc import write_document
from slop_model import enforce_structural_budget
from slop_profiles import ProfileSpec, apply_profile_stage, profile_from_args
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


def _validate_request(args: argparse.Namespace) -> tuple[int, ProfileSpec | None]:
    """Parser guarantee plus the profile gate; (exit, profile) out."""
    parser_exit = _parser_guarantee(args)
    if parser_exit:
        return parser_exit, None
    profile = profile_from_args(args)
    if profile is None:
        return 2, None
    if args.enforce_bloat:
        # Registry mutation must precede the scan: Finding.__post_init__
        # resolves severity from the enforced set at construction time.
        enforce_structural_budget()
    return 0, profile


def _scan_stage(args: argparse.Namespace, profile: ProfileSpec) -> tuple[list, dict]:
    """One scan with the profile's budgets; ArchConfigError propagates."""
    # Imported here, not at module top: ai_slop_detector re-exports main
    # from this module at its own import time, so a top-level import would
    # be a circular import. By the time main() runs, both modules are loaded.
    from ai_slop_detector import scan

    metrics_bundle: dict = {}
    findings = scan(
        args.root,
        load_phrases(),
        extra_globs=args.ignore,
        provenance=args.provenance,
        budgets=profile.budgets or None,
        metrics_out=metrics_bundle if args.metrics_json else None,
    )
    return findings, metrics_bundle


def _metrics_stage(args: argparse.Namespace, findings: list, bundle: dict) -> bool:
    """Write the structured metrics document; returns False on failure.

    OSError fails closed at the caller: a requested metrics document
    that cannot be written is a configuration error, never a silent
    gap in the store.
    """
    if not args.metrics_json:
        return True
    try:
        write_document(args.metrics_json, findings, bundle)
    except OSError as exc:
        print(f"ai-integrity: metrics document rejected: {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parser_exit, profile = _validate_request(args)
    if parser_exit:
        return parser_exit
    if profile is None:
        return 2
    try:
        findings, metrics_bundle = _scan_stage(args, profile)
    except ArchConfigError as exc:
        # Fail closed: a declared rule set that cannot be honored must
        # fail the run, not silently pass. Exit 2 = configuration error.
        print(f"ai-integrity: architecture config rejected: {exc}", file=sys.stderr)
        return 2

    findings, _ = apply_profile_stage(findings, profile)
    if not _metrics_stage(args, findings, metrics_bundle):
        return 2
    ratchet_exit = _quality_ratchet(args, findings)
    if ratchet_exit == 2 or (args.write_quality_baseline and ratchet_exit == 0):
        return ratchet_exit
    suppressed = 0
    if args.baseline:
        findings, suppressed = apply_baseline(findings, args.baseline)
    gate_exit, gate_sections = coverage_gate_stage(args, findings)
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


# The argument surface lives in slop_cli_args (v2.18 budget split);
# parse_args stays the single entry point, re-exported here.
from slop_cli_args import parse_args  # noqa: E402,F401
