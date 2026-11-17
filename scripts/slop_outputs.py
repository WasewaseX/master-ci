#!/usr/bin/env python3
"""The round-11 machine outputs: the findings document and the score badge.

Split out of slop_reporting when the abilities tranche pushed that file
over its own 400-line budget (the structural law applies to its own
source first). Both outputs obey the determinism contract the report
and SARIF already honor: no timestamps, no environment reads - two
scans of the same tree produce byte-identical artifacts.
"""

from __future__ import annotations

import json

from slop_model import Finding
from slop_reporting import advisory_score, ai_integrity_score

BADGE_COLORS = {
    "A": "#2da44e",
    "B": "#97ca00",
    "C": "#dfb317",
    "D": "#fe7d37",
    "F": "#e05d44",
}

BADGE_COLORS = {
    "A": "#2da44e",
    "B": "#97ca00",
    "C": "#dfb317",
    "D": "#fe7d37",
    "F": "#e05d44",
}

# What is not list-typed stays out of json: no timestamps, no absolute
# paths - two scans of the same tree produce byte-identical documents,
# the same determinism contract the report and SARIF already honor.


def write_json(
    path: str,
    root: str,
    findings: list[Finding],
    suppressed_baseline: int = 0,
    suppressed_inline: int = 0,
) -> None:
    """The findings document for tooling (``--json``), schema findings-v1.

    The CI parity surface: scanaislop's ``aislop ci`` pairs a machine
    document with the exit-code gate, and until now Master CI's machine
    output was SARIF only - good for the GitHub Security tab, awkward
    for a script that wants the score and the finding list in one read.
    Severity follows the live registry, so an --enforce-bloat run
    reports the escalated severities the gate actually enforced.
    """
    blocking = sum(f.weight for f in findings if not f.advisory)
    ai_score, ai_grade, ai_verdict = ai_integrity_score(findings)
    doc = {
        "version": "findings-v1",
        "root": str(root),
        "scores": {
            "blocking": blocking,
            "advisory": advisory_score(findings),
            "ai_integrity": {
                "score": ai_score,
                "grade": ai_grade,
                "verdict": ai_verdict,
            },
        },
        "suppressed": {"baseline": suppressed_baseline, "inline": suppressed_inline},
        "findings": [
            {
                "check": item.check,
                "path": item.path.replace("\\", "/"),
                "line": max(1, item.line),
                "severity": "blocking" if not item.advisory else "advisory",
                "weight": item.weight,
                "message": item.message,
                "remedy": item.remedy,
            }
            for item in findings
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1)


def badge_markdown(score: int, grade: str) -> str:
    """The README snippet the badge backs up."""
    return (
        f"![ai-integrity](https://img.shields.io/badge/ai_integrity-{grade}"
        f"%20·%20slop%20{score}%2F100-{BADGE_COLORS.get(grade, '#9f9f9f')})"
    )


def write_badge(path: str, score: int, grade: str) -> str:
    """A flat deterministic SVG badge; returns the markdown snippet.

    The score shown is the AI-integrity slop score (0 = clean), the
    number ``--max-slop-score`` gates, with the grade for color - the
    same pair the summary line prints, so the badge can never disagree
    with the gate.
    """
    color = BADGE_COLORS.get(grade, "#9f9f9f")
    label = "ai-integrity"
    value = f"{grade} - slop {score}/100"
    # Widths scale with character count: 7px per char + padding, the
    # shields.io flat convention, so the text never overflows the cell.
    label_w = 6 + 7 * len(label)
    value_w = 6 + 7 * len(value)
    total_w = label_w + value_w
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20"'
        ' role="img" aria-label="{label}: {value}">'
        "<title>{label}: {value}</title>"
        '<linearGradient id="s" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>'
        '<clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>'
        '<g clip-path="url(#r)">'
        '<rect width="{lw}" height="20" fill="#555"/>'
        '<rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
        '<rect width="{w}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif"'
        ' text-rendering="geometricPrecision" font-size="110">'
        '<text transform="scale(.1)" x="{lx}" y="150" fill="#fff" textLength="{ltw}">{label}</text>'
        '<text transform="scale(.1)" x="{vx}" y="150" fill="#fff" textLength="{vtw}">{value}</text>'
        "</g></svg>"
    ).format(
        w=total_w,
        lw=label_w,
        vw=value_w,
        lx=label_w * 5,
        vx=label_w * 10 + value_w * 5,
        ltw=label_w * 10 - 20,
        vtw=value_w * 10 - 20,
        label=label,
        value=value,
        color=color,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)
    return badge_markdown(score, grade)
