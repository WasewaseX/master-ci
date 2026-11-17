#!/usr/bin/env python3
"""The accuracy benchmark: labeled rows, precision, recall, clean controls.

Audit finding H (2026-11-17): the fixture benchmark proves 12 declared
outcomes and the performance benchmark proves speed - neither proves
detection accuracy. No test count establishes a low false-positive rate
on real code. This harness is the instrument that does, honestly and
from a small seed:

- positive rows: the fixture benchmark's labeled manifests, judged per
    rule (expected "detected" that does not appear is a false negative);
- negative rows: clean control projects - complete, hand-shaped code
    with consumers, manifests and docs - where ANY quality-dimension
    finding is a false positive, counted per check;
- known false positives: corpus-declared, reason-carrying exemptions
    (``corpus.json``). They stay VISIBLE in the report as their own
    column - a limitation recorded is not a limitation hidden - but they
    do not fail the run: the corpus documents them because they are
    understood, not because they are excused.

Per check the report reads: judged rows, TP/FP/FN, precision, recall,
and the promotion verdict - ``promotion-ready evidence`` needs
precision >= 0.8 across >= 5 judged rows; everything else states
``advisory-only`` out loud, which is the discipline the audit asked
for: uncertain rules stay advisory until their accuracy is established.

The corpus is a seed, not the holdout: the protocol for widening it
with real public repositories (versions, commit hashes, per-analyzer
runs, competitor comparisons) lives in benchmarks/accuracy/README.md.
Exit codes: 0 all judged rows agree with their labels, 1 any violation
or unexpected false positive, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ai_slop_detector import scan as full_scan
from fixture_benchmark import (
    DEFAULT_FIXTURES_DIR,
    ConfigError,
    evaluate,
    load_benchmark,
)
from slop_categories import CHECK_CATEGORIES
from slop_files import load_phrases

REPO = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO / "benchmarks" / "accuracy"
CORPUS_MANIFEST = CORPUS_DIR / "corpus.json"
CONTROLS = ("clean_python", "clean_js", "clean_go")

# Promotion evidence thresholds - documented, conservative, and meant
# to be revisited only through the README's protocol.
PROMOTION_MIN_PRECISION = 0.8
PROMOTION_MIN_ROWS = 5

# Negative-control labels apply to the quality dimensions; the
# ai-integrity authorship family judges prose style on its own corpus.
NEGATIVE_EXCLUDED_CATEGORY = "ai-integrity"


class CorpusError(ConfigError):
    """The corpus manifest is malformed - exit 2, never a guessed report."""


@dataclass
class Accusation:
    """One false positive a clean control produced."""

    check: str
    fixture: str
    path: str
    line: int
    message: str
    known: bool
    reason: str = ""


@dataclass
class CheckStats:
    """One check's labeled performance over the whole corpus.

    `tn` counts explicitly-declared clean rows that stayed clean (the
    fixtures' expected-clean manifests); the negative controls'
    all-checks-clean labels contribute only their violations, because a
    clean verdict on a language a check cannot even parse is a vacuous
    row, not evidence.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    known_fp: int = 0
    details: list[Accusation] = field(default_factory=list)

    @property
    def judged(self) -> int:
        return self.tp + self.fp + self.fn

    @property
    def precision(self) -> float | None:
        if self.tp + self.fp == 0:
            return None
        return self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        if self.tp + self.fn == 0:
            return None
        return self.tp / (self.tp + self.fn)

    @property
    def promotion(self) -> str:
        precision = self.precision
        if precision is None or self.judged < PROMOTION_MIN_ROWS:
            return "advisory-only (insufficient accuracy evidence)"
        if precision >= PROMOTION_MIN_PRECISION:
            return "promotion-ready evidence"
        return "advisory-only (precision below the promotion floor)"


def _git_describe() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def load_known_fps() -> dict[tuple[str, str], str]:
    """(check, fixture) -> reason, from the corpus manifest, fail-closed."""
    if not CORPUS_MANIFEST.is_file():
        raise CorpusError(f"corpus manifest missing: {CORPUS_MANIFEST}")
    try:
        data = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{CORPUS_MANIFEST} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "accuracy-v1":
        raise CorpusError(f"{CORPUS_MANIFEST} must be an accuracy-v1 object")
    known: dict[tuple[str, str], str] = {}
    for entry in data.get("known_fps", []):
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key] for key in ("check", "fixture", "reason")
        ):
            raise CorpusError(f"{CORPUS_MANIFEST}: each known_fp needs check, fixture, reason")
        known[(entry["check"], entry["fixture"])] = entry["reason"]
    return known


def judge_positives(fixtures_dir: Path, stats: dict[str, CheckStats]) -> list[tuple[str, str, str]]:
    """The labeled fixture rows: PASS feeds TP/TN, FAIL feeds FN/FP."""
    violations: list[tuple[str, str, str]] = []
    for row in load_benchmark(fixtures_dir):
        if row.expected == "pending":
            continue
        result = evaluate(row, fixtures_dir)
        entry = stats.setdefault(row.rule, CheckStats())
        if row.expected == "detected":
            if result.verdict == "PASS":
                entry.tp += 1
            else:
                entry.fn += 1
                violations.append(
                    (row.rule, row.slug, f"expected detected, observed {result.observed}")
                )
        elif row.expected == "clean":
            if result.verdict == "PASS":
                entry.tn += 1  # expected clean, none observed
            else:
                entry.fp += 1
                violations.append(
                    (row.rule, row.slug, f"expected clean, observed {result.observed}")
                )
    return violations


def judge_negatives(
    stats: dict[str, CheckStats], known: dict[tuple[str, str], str]
) -> list[tuple[str, str, str]]:
    """Clean controls: every quality-dimension finding is a false positive."""
    violations: list[tuple[str, str, str]] = []
    phrases = load_phrases()
    for slug in CONTROLS:
        root = CORPUS_DIR / slug
        if not root.is_dir():
            raise CorpusError(f"clean control missing: {root}")
        findings = full_scan(root, phrases, provenance=False)
        for finding in findings:
            if CHECK_CATEGORIES.get(finding.check) == NEGATIVE_EXCLUDED_CATEGORY:
                continue
            reason = known.get((finding.check, slug))
            entry = stats.setdefault(finding.check, CheckStats())
            if reason is not None:
                entry.known_fp += 1
                entry.details.append(
                    Accusation(
                        check=finding.check,
                        fixture=slug,
                        path=finding.path,
                        line=finding.line,
                        message=finding.message,
                        known=True,
                        reason=reason,
                    )
                )
                continue
            entry.fp += 1
            entry.details.append(
                Accusation(
                    check=finding.check,
                    fixture=slug,
                    path=finding.path,
                    line=finding.line,
                    message=finding.message,
                    known=False,
                )
            )
            violations.append(
                (
                    finding.check,
                    slug,
                    f"unexpected false positive at {finding.path}:{finding.line}",
                )
            )
    return violations


def build_report(
    stats: dict[str, CheckStats],
    violations: list[tuple[str, str, str]],
    dataset_version: str,
) -> dict[str, object]:
    """The whole report as data: per-check stats, violations, provenance."""
    checks = {}
    for check, entry in sorted(stats.items()):
        checks[check] = {
            "judged": entry.judged,
            "tp": entry.tp,
            "fp": entry.fp,
            "fn": entry.fn,
            "tn": entry.tn,
            "known_fp": entry.known_fp,
            "precision": entry.precision,
            "recall": entry.recall,
            "promotion": entry.promotion,
            "false_positives": [
                {
                    "fixture": item.fixture,
                    "path": item.path,
                    "line": item.line,
                    "message": item.message,
                    "known": item.known,
                    "reason": item.reason,
                }
                for item in entry.details
            ],
        }
    return {
        "schema": "accuracy-report-v1",
        "dataset_version": dataset_version,
        "engine_version": dataset_version,
        "corpus": {
            "positives": str(DEFAULT_FIXTURES_DIR),
            "negative_controls": [str(CORPUS_DIR / slug) for slug in CONTROLS],
        },
        "promotion_rule": {
            "min_precision": PROMOTION_MIN_PRECISION,
            "min_judged_rows": PROMOTION_MIN_ROWS,
        },
        "checks": checks,
        "violations": [
            {"check": check, "fixture": slug, "detail": detail}
            for check, slug, detail in violations
        ],
    }


def _collect_known_fps(checks: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """The corpus-declared false positives, every check's details flattened."""
    known: list[dict[str, object]] = []
    for cell in checks.values():
        raw = cell["false_positives"]
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item["known"]:
                    known.append(item)
    return known


def _stats_rows(checks: dict[str, dict[str, object]]) -> list[str]:
    """One markdown row per check, precision and recall rendered."""
    rows: list[str] = []
    for check, cell in sorted(checks.items()):
        precision = "n/a" if cell["precision"] is None else f"{cell['precision']:.2f}"
        recall = "n/a" if cell["recall"] is None else f"{cell['recall']:.2f}"
        rows.append(
            f"| {check} | {cell['judged']} | {cell['tp']} | {cell['fp']} | {cell['tn']} | "
            f"{cell['known_fp']} | {cell['fn']} | {precision} | {recall} | {cell['promotion']} |"
        )
    return rows


def _render_table(report: dict[str, object]) -> str:
    checks: dict[str, dict[str, object]] = report["checks"]  # type: ignore[assignment]
    violations: list[dict[str, str]] = report["violations"]  # type: ignore[assignment]
    corpus: dict[str, object] = report["corpus"]  # type: ignore[assignment]
    negatives: list[str] = corpus["negative_controls"]  # type: ignore[assignment]
    lines = [
        "## Accuracy benchmark (audit finding H)",
        "",
        f"- dataset/engine version: {report['dataset_version']}",
        f"- positives: {corpus['positives']} (labeled manifests)",
        "- negative controls: " + ", ".join(Path(p).name for p in negatives),
        "",
        "| check | judged | TP | FP | TN | known FP | FN | precision | recall | promotion verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += _stats_rows(checks)
    if violations:
        lines += ["", "### Violations", ""]
        for item in violations:
            lines.append(f"- {item['check']} @ {item['fixture']}: {item['detail']}")
    known_fps = _collect_known_fps(checks)
    if known_fps:
        lines += ["", "### Known false positives (visible, corpus-declared)", ""]
    for known in known_fps:
        fixture = str(known["fixture"])
        message = str(known["message"])
        reason = str(known["reason"])
        lines.append(f"- {fixture}: {message[:110]} - {reason}")
    lines.append("")
    return "\n".join(lines)


def run_accuracy_benchmark(fixtures_dir: Path, json_path: Path | None = None) -> tuple[str, int]:
    """Judge the whole labeled corpus; ConfigError on misconfiguration."""
    known = load_known_fps()
    stats: dict[str, CheckStats] = {}
    violations = judge_positives(fixtures_dir, stats)
    violations += judge_negatives(stats, known)
    report = build_report(stats, violations, _git_describe())
    if json_path is not None:
        json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return _render_table(report), 1 if violations else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge the labeled corpus: precision, recall, clean controls."
    )
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR))
    parser.add_argument(
        "--json", dest="json_path", default=None, help="also write the report as JSON"
    )
    args = parser.parse_args(argv)
    try:
        output, code = run_accuracy_benchmark(Path(args.fixtures_dir), args.json_path)
    except ConfigError as exc:
        print(f"accuracy_benchmark: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
