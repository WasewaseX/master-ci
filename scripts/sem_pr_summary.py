"""Structured PR summaries, reverse-engineered from CodeRabbit's
Summary (roadmap B21): one deterministic markdown report of what the
scan found on this exact commit, stamped with the head SHA it
describes.

The SHA stamp is the contract. A PR summary outliving its commit
silently reviews code that no longer exists, so every summary carries
the ``head_sha`` it was built for and ``--check-stale`` answers the
follow-up question CI and reviewers actually ask: is this summary
still about the current head? A missing SHA is refused at build time -
a summary untied to a commit is exactly the failure mode this tool
exists to remove.

The summary is a map of the scan, not a review: it groups the
detector's findings by the live registry's dimensions (never a second
taxonomy), names every blocking finding with its location, prices the
dimension counts, and reports missing inputs as data gaps - findings
input absent is a gap, never a silent zero (the sem_merge_risk rule).
A low count is not proof of safety and the markdown says so.

Schema version 1; render is byte-deterministic for identical inputs so
a re-run cannot churn the comment.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import json
import sys
from pathlib import Path
from typing import Sequence

from slop_categories import CHECK_CATEGORIES
from slop_model import is_blocking

SUMMARY_SCHEMA_VERSION = 1
MAX_BLOCKING_LISTED = 10
DIMENSIONS = (
    "architecture",
    "code-health",
    "reliability",
    "maintainability",
    "security",
    "test-assurance",
    "dependency-health",
    "ai-integrity",
)


@dc.dataclass
class PrSummary:
    """The scan's map for one exact commit, versioned and SHA-stamped."""

    head_sha: str
    changed_count: int
    schema_version: int = SUMMARY_SCHEMA_VERSION
    blocking_count: int = 0
    advisory_count: int = 0
    findings_available: bool = True
    coverage_known: bool = False
    dimensions: dict[str, int] = dc.field(default_factory=dict)
    top_checks: list[tuple[str, int]] = dc.field(default_factory=list)
    blocking: list[dict] = dc.field(default_factory=list)
    data_gaps: list[str] = dc.field(default_factory=list)


def parse_findings(payload: str) -> list[dict]:
    """Findings from the detector's JSON: a bare list or {"findings": []}."""
    data = json.loads(payload)
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict)]
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return [f for f in data["findings"] if isinstance(f, dict)]
    return []


def _tally_findings(summary: PrSummary, findings: list[dict]) -> None:
    """Fill counts, dimensions, per-check ranking and the blocking list."""
    checks: dict[str, int] = {}
    blocking_rows: list[dict] = []
    for finding in findings:
        check = str(finding.get("check", ""))
        if not check:
            continue
        if is_blocking(check):
            summary.blocking_count += 1
            blocking_rows.append(
                {
                    "check": check,
                    "path": str(finding.get("path", "")),
                    "line": finding.get("line", 0),
                }
            )
        else:
            summary.advisory_count += 1
        dimension = CHECK_CATEGORIES.get(check, "ai-integrity")
        summary.dimensions[dimension] = summary.dimensions.get(dimension, 0) + 1
        checks[check] = checks.get(check, 0) + 1
    summary.top_checks = sorted(checks.items(), key=lambda kv: (-kv[1], kv[0]))
    summary.blocking = sorted(
        blocking_rows,
        key=lambda row: (row["path"], int(row["line"]), row["check"]),
    )[:MAX_BLOCKING_LISTED]


def build_summary(
    findings: list[dict],
    head_sha: str,
    changed_count: int = 0,
    coverage_known: bool = False,
    findings_available: bool | None = None,
) -> PrSummary:
    """Build the SHA-stamped summary; a missing head SHA is refused.

    ``findings_available`` separates a legitimately clean scan from a
    missing input the same way the risk model does: None derives it
    from the list, an explicit False records the gap.
    """
    if not head_sha or not head_sha.strip():
        raise ValueError("head_sha is required: a summary must name the commit it describes")
    available = bool(findings) if findings_available is None else findings_available
    summary = PrSummary(
        head_sha=head_sha.strip(), changed_count=changed_count, findings_available=available
    )
    summary.coverage_known = coverage_known
    _tally_findings(summary, findings)
    if not available:
        summary.data_gaps.append("no findings input")
    if not coverage_known:
        summary.data_gaps.append("new-code coverage unknown")
    return summary


def is_stale(summary: PrSummary, current_sha: str) -> bool:
    """True when the summary no longer describes the current head."""
    return summary.head_sha != (current_sha or "").strip()


def render_markdown(summary: PrSummary) -> str:
    """The PR-comment form: SHA-stamped, honest about what it is not."""
    lines = [
        f"# Scan summary (schema v{summary.schema_version})",
        "",
        f"Head **{summary.head_sha}** - {summary.changed_count} changed files, "
        f"**{summary.blocking_count}** blocking / **{summary.advisory_count}** advisory.",
        "",
        "| Dimension | Findings |",
        "| --- | --- |",
    ]
    for dimension in DIMENSIONS:
        count = summary.dimensions.get(dimension, 0)
        if count:
            lines.append(f"| {dimension} | {count} |")
    if not summary.dimensions:
        lines.append("| (none) | 0 |")
    if summary.blocking:
        lines += ["", "## Blocking findings", "", "| Check | Location |", "| --- | --- |"]
        for row in summary.blocking:
            lines.append(f"| {row['check']} | {row['path']}:{row['line']} |")
        if summary.blocking_count > len(summary.blocking):
            lines.append(
                f"| ... | {summary.blocking_count - len(summary.blocking)} more blocking "
                "findings not listed |"
            )
    if summary.top_checks:
        lines += ["", "## Most frequent checks", "", "| Check | Count |", "| --- | --- |"]
        for check, count in summary.top_checks[:5]:
            lines.append(f"| {check} | {count} |")
    if summary.data_gaps:
        lines += ["", "## Data gaps", ""]
        lines += [f"- {gap}" for gap in summary.data_gaps]
    lines += [
        "",
        "A summary maps the scan, it is not a review: a low count is not proof of safety,",
        "and a summary stamped for an older head reviews code that no longer exists.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: --head-sha (required), --findings (JSON, '-' for stdin).

    Exit 0 summary rendered (or staleness answer fresh), 1 stale when
    ``--check-stale`` names a different SHA, 2 usage or build refusal.
    """
    parser = argparse.ArgumentParser(description="build a SHA-stamped PR scan summary")
    parser.add_argument("--findings", default="-", help="detector JSON file, or - for stdin")
    parser.add_argument("--head-sha", required=True, help="the commit the summary describes")
    parser.add_argument("--changed", type=int, default=0, help="number of changed files")
    parser.add_argument(
        "--coverage-known", action="store_true", help="new-code coverage is available"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--check-stale", default=None, help="answer whether this summary still matches a SHA"
    )
    args = parser.parse_args(argv)
    if args.findings == "-":
        payload = sys.stdin.read()
    elif Path(args.findings).is_file():
        payload = Path(args.findings).read_text(encoding="utf-8")
    else:
        payload = ""
    try:
        findings = parse_findings(payload) if payload.strip() else []
        summary = build_summary(findings, args.head_sha, args.changed, args.coverage_known)
    except ValueError as exc:
        print(f"pr-summary refused: {exc}", file=sys.stderr)
        return 2
    if args.check_stale is not None:
        print("fresh" if not is_stale(summary, args.check_stale) else "stale")
        return 0 if not is_stale(summary, args.check_stale) else 1
    if args.json:
        payload = dc.asdict(summary)
        payload["top_checks"] = [list(kv) for kv in summary.top_checks]
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(summary), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
