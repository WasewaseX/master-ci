#!/usr/bin/env python3
"""The check catalog: one deterministic listing of every detector rule.

The scanaislop product ships ``aislop rules`` - a browsable catalog of
what the scanner looks for, machine- and human-readable. Master CI's
catalog existed only behind the MCP server (``list_checks``), which a CI
consumer or a curious contributor never meets. Round 11 promotes the
same registry to the CLI flag ``--list-checks`` (json for tooling, md
for docs and review checklists); this module owns the builders and the
CLI owns the flag - one catalog, one surface, no duplicate mains.

The catalog is registry-only: it reads the live check sets
(``REMEDIES``, the blocking/advisory classes, the category taxonomy, the
reliability and design memberships) and never runs a scan, so it is
exact even on an empty repository and costs no analysis time. Its
contract test pins three invariants:

- every REMEDIES key appears exactly once (no drift between the
    registry and the catalog);
- the severity label always agrees with ``is_blocking`` (including the
    --enforce-bloat escalation, which the caller can pass in);
- every row carries a category from the taxonomy.
"""

from __future__ import annotations

from slop_categories import CHECK_CATEGORIES
from slop_debt import DESIGN_CHECKS
from slop_model import (
    BLOCKING_CHECKS,
    ENFORCED_CHECKS,
    REMEDIES,
    STRUCTURAL_BUDGET_CHECKS,
)
from slop_reliability import RELIABILITY_CHECKS


def build_catalog(enforced: set[str] | None = None) -> list[dict]:
    """One row per registry check, sorted by id for stable diffs.

    `enforced` folds an escalation decision (``--enforce-bloat`` mutates
    ``ENFORCED_CHECKS`` before scanning) into the severity label, so the
    catalog a strict CI profile prints is the catalog that profile obeys.
    """
    live_enforced = ENFORCED_CHECKS if enforced is None else set(enforced)
    rows: list[dict] = []
    for check in sorted(REMEDIES):
        blocking = check in BLOCKING_CHECKS or check in live_enforced
        rows.append(
            {
                "check": check,
                "severity": "blocking" if blocking else "advisory",
                "category": CHECK_CATEGORIES.get(check, "ai-integrity"),
                "structural_budget": check in STRUCTURAL_BUDGET_CHECKS,
                "reliability": check in RELIABILITY_CHECKS,
                "design": check in DESIGN_CHECKS,
                "remedy": REMEDIES[check],
            }
        )
    return rows


def render_markdown(rows: list[dict]) -> str:
    """The markdown table: grouped read order, one line per check."""
    lines = [
        "# Master CI check catalog",
        "",
        "| Check | Category | Severity | Remedy |",
        "|---|---|---|---|",
    ]
    for row in rows:
        severity = row["severity"]
        if row["structural_budget"]:
            severity += " (structural budget)"
        lines.append(f"| `{row['check']}` | {row['category']} | {severity} | {row['remedy']} |")
    lines.append("")
    return "\n".join(lines)
