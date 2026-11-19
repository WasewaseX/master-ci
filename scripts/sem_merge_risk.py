"""PR merge-risk, reverse-engineered from CodeRabbit's Merge Risk
(roadmap B31): one documented, versioned scoring model over the inputs
this repository already produces, with every contribution's evidence
attached and missing information reported as missing.

Model version 1, additive and capped at 100:

- blocking findings count 25 each (a reviewer fixes these pre-merge)
- security-category findings 15 each
- reliability-category findings 10 each
- maintainability-category findings 3 each
- every other advisory finding 2 each
- blast radius: 20-49 changed files +10, 50 or more +20
- new-code coverage unavailable +5, recorded as a data gap

Severity class comes from the live registries (`is_blocking`,
`CHECK_CATEGORIES`) - the scorer never re-implements severity rules.
A low score is never proof of safety, and the markdown report says so.
Missing findings input yields an `incomplete` report with the data-gap
penalty, never a silent zero.

Exit codes: 0 scored, 2 usage error.
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

RISK_MODEL_VERSION = 1
WEIGHTS = {"blocking": 25, "security": 15, "reliability": 10, "maintainability": 3, "other": 2}
BLAST_SMALL = 10  # 20-49 changed files
BLAST_LARGE = 20  # 50+ changed files
DATA_GAP = 5  # findings or coverage unknown
CAP = 100


@dc.dataclass
class RiskReport:
    """The score, its version, every contribution and the data gaps."""

    score: int
    model_version: int = RISK_MODEL_VERSION
    incomplete: bool = False
    contributions: list[dict] = dc.field(default_factory=list)


def _severity_class(check: str) -> str:
    """blocking > security > reliability > maintainability > other."""
    if is_blocking(check):
        return "blocking"
    category = CHECK_CATEGORIES.get(check, "ai-integrity")
    if category in WEIGHTS:
        return category
    return "other"


def parse_findings(payload: str) -> list[dict]:
    """Findings from the detector's JSON: a bare list or {"findings": []}."""
    data = json.loads(payload)
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict)]
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return [f for f in data["findings"] if isinstance(f, dict)]
    return []


def score(
    findings: list[dict],
    changed_count: int,
    coverage_known: bool,
    findings_available: bool | None = None,
) -> RiskReport:
    """The versioned additive model with evidence on every row.

    ``findings_available`` separates a legitimately empty scan (no
    findings, no gap) from a missing input (a data gap, never a silent
    zero); None derives it from the list so the model stays honest by
    default.
    """
    report = RiskReport(score=0)
    available = bool(findings) if findings_available is None else findings_available
    classes: dict[str, int] = {}
    for finding in findings:
        check = str(finding.get("check", ""))
        if not check:
            continue
        cls = _severity_class(check)
        classes[cls] = classes.get(cls, 0) + 1
    for cls in ("blocking", "security", "reliability", "maintainability", "other"):
        count = classes.get(cls, 0)
        if count:
            report.contributions.append(
                {"kind": f"{cls}-findings", "count": count, "weight": WEIGHTS[cls], "points": count * WEIGHTS[cls]}
            )
    if changed_count >= 50:
        report.contributions.append({"kind": "blast-radius", "count": changed_count, "weight": BLAST_LARGE, "points": BLAST_LARGE})
    elif changed_count >= 20:
        report.contributions.append({"kind": "blast-radius", "count": changed_count, "weight": BLAST_SMALL, "points": BLAST_SMALL})
    if not available:
        report.incomplete = True
        report.contributions.append(
            {"kind": "data-gap", "count": 1, "weight": DATA_GAP, "points": DATA_GAP, "detail": "no findings input"}
        )
    if not coverage_known:
        report.contributions.append(
            {"kind": "data-gap", "count": 1, "weight": DATA_GAP, "points": DATA_GAP, "detail": "new-code coverage unknown"}
        )
    report.score = min(CAP, sum(int(c["points"]) for c in report.contributions))
    return report


def render_markdown(report: RiskReport, changed_count: int) -> str:
    """The PR-comment form, with the never-proof-of-safety line."""
    lines = [
        f"# Merge risk (model v{report.model_version})",
        "",
        f"Score **{report.score}/100** over {changed_count} changed files."
        + (" Report is incomplete - inputs were missing." if report.incomplete else ""),
        "",
        "| Contribution | Count | Weight each | Points |",
        "| --- | --- | --- | --- |",
    ]
    for c in report.contributions:
        lines.append(
            f"| {c['kind']} | {c['count']} | {c['weight']} | {c['points']} |"
        )
    lines += [
        "",
        "A low score is not proof of safety: it only means the model's inputs found nothing.",
        "Missing information is reported as a data gap, never scored as zero risk.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: --findings (JSON file, '-' for stdin), --changed count, flags."""
    parser = argparse.ArgumentParser(description="score a PR's merge risk")
    parser.add_argument("--findings", default="-", help="detector JSON file, or - for stdin")
    parser.add_argument("--changed", type=int, default=0, help="number of changed files")
    parser.add_argument("--coverage-known", action="store_true", help="new-code coverage is available")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.findings == "-":
        payload = sys.stdin.read()
    elif Path(args.findings).is_file():
        payload = Path(args.findings).read_text(encoding="utf-8")
    else:
        payload = ""
    findings = parse_findings(payload) if payload.strip() else []
    report = score(findings, args.changed, args.coverage_known)
    if args.json:
        print(json.dumps(vars(report), indent=2))
    else:
        print(render_markdown(report, args.changed), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
