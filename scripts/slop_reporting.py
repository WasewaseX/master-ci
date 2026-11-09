"""Scoring, report rendering, SARIF, annotations and baselines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from slop_categories import CATEGORY_LABELS, CATEGORY_ORDER, category_of
from slop_model import REMEDIES, Finding, is_blocking
from slop_patterns import IGNORE_FILE, MAX_ANNOTATIONS, TOOL_NAME

# scanaislop-aligned scoring (scanaislop.com/docs/scoring): per-rule
# saturation (maxPerRule) plus impact tiers; blocking weight stays 1.0.
MAX_PER_RULE = 40  # a single check contributes at most this much per file

TIER_MAINTAINABILITY = 0.75  # refactoring and design debt
TIER_MECHANICAL = 0.5  # cleanup a mechanical fixer can usually handle
TIER_STYLE = 0.5  # style, policy and prose pressure
TIER_INFO = 0.25  # medium-confidence traceability signals

TIERED_CHECKS: dict[str, float] = {
    "bloat-long-file": TIER_MAINTAINABILITY,
    "bloat-param-count": TIER_MAINTAINABILITY,
    "bloat-comment-ratio": TIER_MAINTAINABILITY,
    "bloat-banner-comments": TIER_MAINTAINABILITY,
    "god-function": TIER_MAINTAINABILITY,
    "deep-nesting": TIER_MAINTAINABILITY,
    "mutable-default": TIER_MAINTAINABILITY,
    "star-import": TIER_MAINTAINABILITY,
    "prose-block": TIER_MAINTAINABILITY,
    "range-len-loop": TIER_MAINTAINABILITY,
    "isinstance-ladder": TIER_MAINTAINABILITY,
    "repetitive-dispatch": TIER_MAINTAINABILITY,
    "unwrap-density": TIER_MAINTAINABILITY,
    "chained-dict-get": TIER_MAINTAINABILITY,
    "phantom-import": TIER_MECHANICAL,
    "duplicate-import": TIER_MECHANICAL,
    "debug-print": TIER_MECHANICAL,
    "constant-condition": TIER_MECHANICAL,
    "unreachable-code": TIER_MECHANICAL,
    "redundant-try-catch": TIER_MECHANICAL,
    "silent-recovery": TIER_MECHANICAL,
    "tautological-test": TIER_MECHANICAL,
    "generic-name": TIER_MECHANICAL,
    "meta-comment": TIER_MECHANICAL,
    "hardcoded-url": TIER_MECHANICAL,
    "todo-density": TIER_MECHANICAL,
    "ts-suppression": TIER_MECHANICAL,
    "blanket-suppression": TIER_MECHANICAL,
    "missing-tests": TIER_MECHANICAL,
    "low-new-coverage": TIER_MECHANICAL,
    "em-dash-density": TIER_STYLE,
    "md-emoji-headings": TIER_STYLE,
    "exclamations": TIER_STYLE,
    "rhetorical-questions": TIER_STYLE,
    "rule-of-three": TIER_STYLE,
    "ui-eyebrow-label": TIER_STYLE,
    "ui-italic-serif-hero": TIER_STYLE,
    "ui-fake-stats": TIER_STYLE,
    "ui-generic-testimonial": TIER_STYLE,
    "ui-not-just-x": TIER_STYLE,
    "ui-triple-slogan": TIER_STYLE,
    "ui-glass-glow": TIER_STYLE,
    "ui-decorative-numbering": TIER_STYLE,
    "ui-ai-gradient": TIER_STYLE,
    "ui-gradient-text": TIER_STYLE,
    "ui-emoji-decor": TIER_STYLE,
    "ui-marketing-copy": TIER_STYLE,
    "ui-placeholder-copy": TIER_STYLE,
    "placeholder-note": TIER_INFO,
    "ai-provenance": TIER_INFO,
    "go-error-assigned-blank": TIER_INFO,
}


def check_tier(check: str) -> float:
    """Impact-tier multiplier for one check id (strict/standard = 1.0)."""
    return TIERED_CHECKS.get(check, 1.0)


def file_deficit(findings: list[Finding]) -> int:
    blocking_weight = sum(f.weight for f in findings if not f.advisory)
    advisory_weight = 0.0
    per_check: dict[str, int] = {}
    for item in findings:
        if item.advisory:
            used = min(per_check.get(item.check, 0) + item.weight, MAX_PER_RULE)
            advisory_weight += (used - per_check.get(item.check, 0)) * check_tier(item.check)
            per_check[item.check] = used
    purity = math.exp(-0.5 * blocking_weight)
    deficit = 100 * (1 - purity) + 8 * math.log1p(advisory_weight)
    return min(100, round(deficit))


def project_score(deficits: list[int]) -> tuple[int, str, str]:
    """0.6*worst + 0.4*mean aggregation with grade and verdict labels."""
    if not deficits:
        return 0, "A", "CLEAN"
    worst = max(deficits)
    mean = sum(deficits) / len(deficits)
    score = round(min(100.0, 0.6 * worst + 0.4 * mean))
    if score < 10:
        grade = "A"
    elif score < 25:
        grade = "B"
    elif score < 45:
        grade = "C"
    elif score < 65:
        grade = "D"
    else:
        grade = "F"
    if score >= 70:
        verdict = "CRITICAL"
    elif score >= 50:
        verdict = "INFLATED"
    elif score >= 30:
        verdict = "SUSPICIOUS"
    else:
        verdict = "CLEAN"
    return score, grade, verdict


def fingerprint(item: Finding) -> str:
    normalized = re.sub(r"\d+", "#", item.message)
    raw = f"{item.path}|{item.check}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def blocking_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if not f.advisory)


def advisory_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if f.advisory)


# ---------------------------------------------------------------------------
# Quality dimensions: code quality never feeds the slop gate
# ---------------------------------------------------------------------------


def _category_row(cat: str, findings: list[Finding]) -> tuple[str, dict]:
    """One dimension's scorecard over its own findings only."""
    subset = [f for f in findings if category_of(f.check) == cat]
    per_file: dict[str, list[Finding]] = {}
    for item in subset:
        per_file.setdefault(item.path, []).append(item)
    score, grade, verdict = project_score([file_deficit(v) for v in per_file.values()])
    return (
        cat,
        {
            "blocking": sum(f.weight for f in subset if not f.advisory),
            "advisory": sum(f.weight for f in subset if f.advisory),
            "count": len(subset),
            "score": score,
            "grade": grade,
            "verdict": verdict,
        },
    )


def category_breakdown(findings: list[Finding]) -> list[tuple[str, dict]]:
    """Per-dimension scores, always in CATEGORY_ORDER.

    Each dimension is scored with the same deficit model over its own
    findings only, so a bloat-heavy-but-slop-free repo still grades A on
    AI integrity, and a slop-free-looking repo with swallowed exceptions
    cannot hide behind a clean slop score.
    """
    return [_category_row(cat, findings) for cat in CATEGORY_ORDER]


def ai_integrity_score(findings: list[Finding]) -> tuple[int, str, str]:
    """Score, grade, verdict for the AI-integrity dimension alone.

    This - not the whole-repo deficit - is what ``--max-slop-score``
    gates since v1.18: code-quality findings must not inflate (or mask)
    the slop number.
    """
    row = dict(category_breakdown(findings))["ai-integrity"]
    return row["score"], row["grade"], row["verdict"]


# ---------------------------------------------------------------------------
# Reporting: summary, per-rule table, fix-first brief, SARIF
# ---------------------------------------------------------------------------


def _report_rule_table(rule_counts: Counter) -> list[str]:
    lines = ["| Rule | Findings | Severity |", "|---|---|---|"]
    for check, count in rule_counts.most_common():
        severity = "blocking" if is_blocking(check) else "advisory"
        lines.append(f"| {check} | {count} | {severity} |")
    return lines


def _report_fix_first(blocking: list[Finding]) -> list[str]:
    lines = ["", "### Fix these first", ""]
    priority = sorted(blocking, key=lambda f: -f.weight)[:5]
    if priority:
        for idx, item in enumerate(priority, start=1):
            lines.append(
                f"{idx}. `{item.path}:{item.line}` [{item.check}] {item.message} -> {item.remedy}"
            )
    else:
        lines.append("No blocking findings - nothing to fix first.")
    lines += [""]
    return lines


def _report_blocking_table(blocking: list[Finding]) -> list[str]:
    if not blocking:
        return ["No blocking AI-slop signals above threshold.", ""]
    lines = [
        "### Blocking findings",
        "",
        "| Check | Where | Detail | Weight | Fix |",
        "|---|---|---|---|---|",
    ]
    for item in blocking:
        lines.append(
            f"| {item.check} | `{item.path}:{item.line}` | {item.message} "
            f"| {item.weight} | {item.remedy} |"
        )
    lines.append("")
    return lines


def _report_advisory_table(advisory: list[Finding]) -> list[str]:
    if not advisory:
        return []
    lines = [
        "### Advisory signals (never fail a build)",
        "",
        "| Check | Where | Detail | Weight |",
        "|---|---|---|---|---|",
    ]
    for item in advisory:
        lines.append(
            f"| {item.check} | `{item.path}:{item.line}` | {item.message} | {item.weight} |"
        )
    lines.append("")
    return lines


def _report_top_files(deficits: dict[str, int]) -> list[str]:
    top_files = sorted(deficits.items(), key=lambda kv: -kv[1])[:5]
    if not top_files or top_files[0][1] <= 0:
        return []
    lines = ["### Highest-deficit files", "", "| File | Deficit |", "|---|---|"]
    for name, value in top_files:
        lines.append(f"| `{name}` | {value} |")
    lines.append("")
    return lines


def _report_dimensions(rows: list[tuple[str, dict]]) -> list[str]:
    lines = [
        "",
        "### Quality dimensions",
        "",
        "Scored independently - AI integrity is the only dimension under the "
        "slop-score gate; the others are reported so code quality and AI "
        "slop never blur into one number.",
        "",
        "| Dimension | Blocking | Advisory | Score | Grade |",
        "|---|---|---|---|---|",
    ]
    for name, row in rows:
        lines.append(
            f"| {CATEGORY_LABELS[name]} | {row['blocking']} | {row['advisory']} "
            f"| {row['score']}/100 | {row['grade']} |"
        )
    lines.append("")
    return lines


def write_report(
    path: str,
    root: Path,
    findings: list[Finding],
    suppressed: int = 0,
    extra_sections: list[str] | None = None,
) -> None:
    blocking = [f for f in findings if not f.advisory]
    advisory = [f for f in findings if f.advisory]
    per_file: dict[str, list[Finding]] = {}
    for item in findings:
        per_file.setdefault(item.path, []).append(item)
    deficits = {name: file_deficit(items) for name, items in per_file.items()}
    score, grade, verdict = project_score(list(deficits.values()))
    lines = [
        "",
        f"Scanned `{root}`: {len(blocking)} blocking / {len(advisory)} advisory finding(s).",
        f"Blocking score **{blocking_score(findings)}**, advisory score "
        f"{advisory_score(findings)} (advisory findings never fail a build).",
    ]
    if suppressed:
        lines.append(f"Baseline applied: {suppressed} known finding(s) suppressed.")
    lines += [
        "",
        f"**Slop score {score}/100 - grade {grade} - {verdict}** "
        "(0.6x worst file + 0.4x mean file deficit).",
        "",
    ]
    lines += _report_dimensions(category_breakdown(findings))
    lines += extra_sections or []
    lines += _report_rule_table(Counter(f.check for f in findings))
    lines += _report_fix_first(blocking)
    lines += _report_blocking_table(blocking)
    lines += _report_advisory_table(advisory)
    lines += _report_top_files(deficits)
    lines.append(
        f"Opt out per path or check via `{IGNORE_FILE}` "
        "(glob lines, or `check:<id>` to disable one rule); "
        "accept existing findings with `--write-baseline` + `--baseline`."
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)


def write_sarif(path: str, findings: list[Finding]) -> None:
    rules = sorted({f.check for f in findings})
    rule_meta = [
        {
            "id": check,
            "shortDescription": {"text": REMEDIES.get(check, check)},
            "defaultConfiguration": {"level": "error" if is_blocking(check) else "warning"},
            "properties": {"security-severity": "8.0" if is_blocking(check) else "3.0"},
        }
        for check in rules
    ]
    results = [
        {
            "ruleId": item.check,
            "level": "error" if not item.advisory else "warning",
            "message": {"text": f"{item.message}. Fix: {item.remedy}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": item.path.replace("\\", "/"),
                        },
                        "region": {"startLine": max(1, item.line)},
                    }
                }
            ],
            "partialFingerprints": {"masterCIContextHash/v1": fingerprint(item)},
        }
        for item in findings
    ]
    sarif = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": "https://github.com/WasewaseX/master-ci",
                        "rules": rule_meta,
                    }
                },
                "originalUriBaseIds": {},
                "results": results,
            }
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sarif, handle, indent=1)


def annotate(findings: list[Finding]) -> None:
    for item in findings[:MAX_ANNOTATIONS]:
        message = item.message.replace("\n", " ")
        prefix = "[advisory] " if item.advisory else ""
        print(f"::warning file={item.path},line={item.line}::{prefix}[{item.check}] {message}")


def apply_baseline(findings: list[Finding], baseline_path: str) -> tuple[list[Finding], int]:
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings, 0
    known = set(data.get("fingerprints", []))
    if not known:
        return findings, 0
    kept = [f for f in findings if fingerprint(f) not in known]
    return kept, len(findings) - len(kept)


def write_baseline(path: str, findings: list[Finding]) -> None:
    # indent 2, not 1: consumer hygiene gates enforce the editorconfig
    # (indent_size 2), and a baseline that tripped it would block the
    # commit that records it. Existing baselines stay valid - JSON
    # parsing ignores the whitespace.
    data = {"version": 1, "fingerprints": sorted(fingerprint(f) for f in findings)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
