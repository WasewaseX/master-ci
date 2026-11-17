"""The CLI's argument surface: one parser, four arg groups.

Split from slop_cli.py when v2.18's --metrics-json pushed the module
past the 400-line budget the engine enforces on its own source.
parse_args stays the single entry point; slop_cli re-exports it.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # Optional only so --list-checks (a registry-only surface, no scan)
    # can run without a repo path; main() still demands a root for a scan.
    parser.add_argument("root", type=Path, nargs="?", default=None, help="Repo path to scan")
    _add_gate_args(parser)
    _add_baseline_args(parser)
    _add_profile_args(parser)
    _add_output_args(parser)
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
        "--metrics-json",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the structured metrics document (metrics-v1): actual "
            "metric values incl. healthy below-threshold measurements, "
            "per-metric measured/not-measured states, and the corpus's "
            "analysis-coverage states. The snapshot store prefers this "
            "over SARIF message scraping (audit finding G)"
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


def _add_profile_args(parser: argparse.ArgumentParser) -> None:
    """The analyzer profile: downgrade-only severities, scopes, budgets."""
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Analyzer profile (JSON or YAML): downgrade-only per-check "
            "severities, scope exclusions and budget overrides. Applied "
            "before baselines, ratchets and gates; every silenced finding "
            "is reported with its reason; a profile that cannot be "
            "honored fails closed (exit 2)"
        ),
    )


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    """The round-11 output surfaces: json, badge, catalog."""
    parser.add_argument(
        "--sarif",
        default="",
        help="Write SARIF 2.1.0 results to this path (for the Security tab)",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        default="",
        metavar="PATH",
        help=(
            "Write the findings document (findings-v1) to this path: the "
            "score, both suppression counters and every finding with "
            "severity and remedy. The aislop-ci parity surface - machine "
            "output paired with the exit-code gate; deterministic, no "
            "timestamps"
        ),
    )
    parser.add_argument(
        "--badge",
        default="",
        metavar="PATH.svg",
        help=(
            "Write the AI-integrity score badge (SVG, grade-colored) to "
            "this path and print the markdown snippet for the README; "
            "shows the same slop score the --max-slop-score gate reads"
        ),
    )
    parser.add_argument(
        "--list-checks",
        dest="list_checks",
        action="store_true",
        help=(
            "Print the check catalog (registry only, no scan): every "
            "check id with category, severity and remedy. Pairs with "
            "--catalog-format json|md; exits 0"
        ),
    )
    parser.add_argument(
        "--catalog-format",
        choices=("json", "md"),
        default="json",
        help="Output format for --list-checks (default: json)",
    )


def _add_coverage_args(parser: argparse.ArgumentParser) -> None:
    """The measured-coverage gates: floors, diff window, report paths."""
    parser.add_argument(
        "--report-file",
        default="",
        help="Write a markdown report to this path",
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
