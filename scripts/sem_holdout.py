"""The accuracy corpus holdout split and the real-repo protocol (A20).

The accuracy benchmark judges one corpus - every labeled row is seen
by every check it names, and the promotion verdict reads the same rows
it was tuned on. A corpus that never splits cannot prove the labels
generalize: the discipline every ML evaluation lives by is the train /
holdout cut, and this module brings it to the accuracy corpus
deterministically - a case's split is a pure function of its id and
the salt, so two runs (two machines, two months) land every case in
the same split and a re-run cannot shuffle evidence around.

The second half is the real-repo corpus the audit asked for:
``benchmarks/accuracy/corpus-real.json`` names real public repositories
(upstream URL, the exact commit they were vendored at) and their
per-check labels, exactly as the accuracy README's widening protocol
describes. The manifest is validated fail-closed - a wrong schema, a
short commit hash or an unknown label verb refuses the run - and a
named fixture that is not vendored yet is a recorded data gap, visible
in every report, never a guessed pass: a corpus entry that promises
evidence it does not carry is not evidence.

What is judged: the fixture-benchmark rows judged per fixture slug -
the case the split manifest names is the case the counts name - and
every vendored real-repo fixture judged per label (``detected`` needs
the check's finding present, ``clean`` needs it absent, ai-integrity
prose judgments stay out of clean rows exactly as in the accuracy
benchmark). A manifest label naming a check the registry does not
know (``debug-prnit``) is refused at load, not judged into a vacuous
clean row. The report records the split manifest, per-case counts
labeled from that same manifest (never a re-hash under a different
ratio), the real rows and the gaps - the artifact a widening PR
attaches.

Exit codes: 0 no violations and no unexpected findings, 1 any label
violation, 2 configuration error. Advisory evidence, not a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from accuracy_benchmark import CheckStats, evaluate, judge_positive
from ai_slop_detector import scan as full_scan
from fixture_bench_probes import ConfigError
from fixture_benchmark import load_benchmark
from slop_categories import CHECK_CATEGORIES
from slop_files import load_phrases
from slop_model import REMEDIES

REPO = Path(__file__).resolve().parents[1]
REAL_CORPUS = REPO / "benchmarks" / "accuracy" / "corpus-real.json"

SALT = "accuracy-holdout-v1"
DEFAULT_RATIO = 30
LABEL_VERBS = frozenset({"detected", "clean"})
NEGATIVE_EXCLUDED_CATEGORY = "ai-integrity"
_HEX = frozenset("0123456789abcdef")


class CorpusError(ConfigError):
    """The corpus or its manifest is misconfigured - exit 2, never a guess."""


class _Ledger:
    """One judging run's accumulators: per-case stats, violations, gaps."""

    def __init__(self) -> None:
        self.stats: dict[str, CheckStats] = {}
        self.violations: list[tuple[str, str, str]] = []
        self.gaps: list[dict[str, str]] = []


def split_case(case_id: str, ratio: int = DEFAULT_RATIO, salt: str = SALT) -> str:
    """One case's split: a pure function of salt and id, never of order."""
    digest = hashlib.sha256(f"{salt}:{case_id}".encode("utf-8")).hexdigest()
    return "holdout" if int(digest[:8], 16) % 100 < ratio else "train"


def split_cases(
    case_ids: Sequence[str], ratio: int = DEFAULT_RATIO, salt: str = SALT
) -> dict[str, str]:
    """Every case exactly once, each in exactly one split."""
    return {case: split_case(case, ratio, salt) for case in case_ids}


def load_real_corpus(manifest_path: Path) -> list[dict[str, object]]:
    """The accuracy-real-v1 manifest, validated fail-closed."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"real corpus manifest missing: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{manifest_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "accuracy-real-v1":
        raise CorpusError(f"{manifest_path} must be an accuracy-real-v1 object")
    repos = data.get("repos")
    if not isinstance(repos, list):
        raise CorpusError(f"{manifest_path}: 'repos' must be a list")
    for entry in repos:
        _validate_repo(entry, manifest_path)
    return repos


def _validate_repo_identity(entry: dict[str, object], path: Path) -> None:
    """Provenance fields: non-empty strings and a full 40-hex commit."""
    for field_name in ("repo", "upstream_url", "commit"):
        value = entry.get(field_name)
        if not isinstance(value, str) or not value:
            raise CorpusError(f"{path}: each repo needs a non-empty {field_name}")
    commit = str(entry["commit"]).lower()
    if len(commit) != 40 or set(commit) - _HEX:
        raise CorpusError(f"{path}: {entry['repo']} commit must be a 40-hex sha")


def _validate_fixture_labels(fixture: dict[str, object], path: Path) -> None:
    """Every label names a live check and a verb the schema knows.

    The check registry (``slop_model.REMEDIES``) is the gate: a typo
    such as ``debug-prnit`` would otherwise be judged as a clean row
    that can never disagree - no finding carries an id that does not
    exist - and a corpus that scores vacuous rows is not evidence.
    """
    labels = fixture.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise CorpusError(f"{path}: fixture {fixture.get('path')} needs labels")
    for check, verb in labels.items():
        if not isinstance(check, str) or not check:
            raise CorpusError(f"{path}: labels need non-empty check ids")
        if check not in REMEDIES:
            raise CorpusError(f"{path}: label {check!r} is not a live check id")
        if verb not in LABEL_VERBS:
            raise CorpusError(f"{path}: label {check}:{verb!r} must be detected or clean")


def _validate_repo(entry: object, path: Path) -> None:
    """One repo entry: provenance, then its fixture list and labels."""
    if not isinstance(entry, dict):
        raise CorpusError(f"{path}: each repo entry must be an object")
    _validate_repo_identity(entry, path)
    fixtures = entry.get("fixtures")
    if not isinstance(fixtures, list):
        raise CorpusError(f"{path}: {entry['repo']} 'fixtures' must be a list")
    for fixture in fixtures:
        if (
            not isinstance(fixture, dict)
            or not isinstance(fixture.get("path"), str)
            or not fixture["path"]
        ):
            raise CorpusError(f"{path}: each fixture needs a non-empty path")
        _validate_fixture_labels(fixture, path)


def _judge_labels(
    case: str,
    labels: dict[str, object],
    checks: set[str],
    entry: CheckStats,
    violations: list[tuple[str, str, str]],
) -> None:
    """One fixture's labels against one scan: tp / fp / fn / tn.

    ``detected`` passes when the check's finding is present, ``clean``
    when it is absent (ai-integrity prose judgments stay out, exactly
    as the accuracy benchmark's negative controls do).
    """
    for check, verb in labels.items():
        if verb == "detected":
            if check in checks:
                entry.tp += 1
            else:
                entry.fn += 1
                violations.append((case, check, "expected detected, observed none"))
        elif check in checks and CHECK_CATEGORIES.get(check) != NEGATIVE_EXCLUDED_CATEGORY:
            entry.fp += 1
            violations.append((case, check, "expected clean, observed a finding"))
        else:
            entry.tn += 1


def _judge_one_fixture(
    fixture: object,
    repo_name: str,
    corpus_root: Path,
    phrases: dict,
    ledger: _Ledger,
) -> None:
    """One vendored fixture: a missing tree is a gap, a present one is judged."""
    if not isinstance(fixture, dict):
        return
    path_part = str(fixture.get("path", ""))
    case = f"{repo_name}:{path_part}"
    fixture_path = corpus_root / path_part
    if not fixture_path.is_dir():
        ledger.gaps.append({"case": case, "reason": "vendored fixture missing from the corpus"})
        return
    labels = fixture.get("labels")
    if not isinstance(labels, dict):
        return
    findings = full_scan(fixture_path, phrases, provenance=False)
    checks = {finding.check for finding in findings}
    entry = ledger.stats.setdefault(case, CheckStats())
    _judge_labels(case, labels, checks, entry, ledger.violations)


def judge_real_rows(
    repos: Sequence[dict[str, object]], corpus_root: Path
) -> tuple[dict[str, CheckStats], list[tuple[str, str, str]], list[dict[str, str]]]:
    """Vendored fixtures judged per label; missing ones are data gaps.

    The stats keys are ``repo:path`` case ids, so the split manifest
    names the same cases the counts name.
    """
    phrases = load_phrases()
    ledger = _Ledger()
    for repo in repos:
        fixtures = repo.get("fixtures")
        if not isinstance(fixtures, list):
            continue
        name = str(repo.get("repo", "?"))
        for fixture in fixtures:
            _judge_one_fixture(fixture, name, corpus_root, phrases, ledger)
    return ledger.stats, ledger.violations, ledger.gaps


def _real_case_ids(repos: Sequence[dict[str, object]]) -> list[str]:
    """``repo:path`` case ids for every manifest fixture, vendored or not."""
    ids: list[str] = []
    for repo in repos:
        fixtures = repo.get("fixtures")
        if not isinstance(fixtures, list):
            continue
        ids.extend(
            f"{repo.get('repo', '?')}:{fixture.get('path', '')}"
            for fixture in fixtures
            if isinstance(fixture, dict)
        )
    return ids


def _build_report(
    ratio: int,
    splits: dict[str, str],
    stats: dict[str, CheckStats],
    real_stats: dict[str, CheckStats],
    gaps: list[dict[str, str]],
    violations: list[tuple[str, str, str]],
) -> dict[str, object]:
    """The whole report as data: splits, per-case counts, gaps, violations."""
    return {
        "schema": "holdout-report-v1",
        "salt": SALT,
        "ratio": ratio,
        "splits": {case: splits[case] for case in sorted(splits)},
        "fixture_rows": _counts(stats, splits),
        "real_rows": _counts(real_stats, splits),
        "gaps": gaps,
        "violations": [
            {"case": case, "check": check, "detail": detail} for case, check, detail in violations
        ],
    }


def _judge_fixture_rows(rows: Sequence, fixtures_dir: Path, ledger: _Ledger) -> None:
    """The fixture-benchmark rows, judged per fixture slug.

    The case is ``row.slug`` - the id the split manifest assigns - so a
    slug's counts never merge another slug's results, and training and
    holdout evidence stay on their own sides of the cut. Keying by
    check (``row.rule``) instead would mix both splits under one name
    and split the evidence by a hash of the check's label. The verdict
    itself comes from the shared positive kernel
    (``accuracy_benchmark.judge_positive``) - one judging rule for
    every caller, no near-copy per module.
    """
    for row in rows:
        if row.expected == "pending":
            continue
        result = evaluate(row, fixtures_dir)
        entry = ledger.stats.setdefault(row.slug, CheckStats())
        judge_positive(row, result, entry, ledger.violations)


def run_holdout(
    fixtures_dir: Path,
    real_manifest: Path | None = None,
    ratio: int = DEFAULT_RATIO,
) -> tuple[str, dict[str, object], int]:
    """Judge both corpora through the split; (markdown, report, exit)."""
    rows = load_benchmark(fixtures_dir)
    case_ids = [row.slug for row in rows]
    repos: list[dict[str, object]] = []
    if real_manifest is not None:
        repos = load_real_corpus(real_manifest)
        case_ids.extend(_real_case_ids(repos))
    splits = split_cases(case_ids, ratio)
    ledger = _Ledger()
    _judge_fixture_rows(rows, fixtures_dir, ledger)
    gaps: list[dict[str, str]] = []
    real_stats: dict[str, CheckStats] = {}
    if real_manifest is not None:
        real_stats, real_violations, gaps = judge_real_rows(repos, REAL_CORPUS.parent)
        ledger.violations += real_violations
    report = _build_report(ratio, splits, ledger.stats, real_stats, gaps, ledger.violations)
    text = _render(report)
    return text, report, 1 if ledger.violations else 0


def _counts(stats: dict[str, CheckStats], splits: dict[str, str]) -> dict[str, dict[str, object]]:
    """Per-case counts, the assigned split folded in, deterministic order.

    The split comes from the already-computed manifest - the same map
    the report's ``splits`` field prints - never from re-hashing the
    case under a default ratio, which would contradict the very
    manifest the report ships (``--ratio 0`` labels no row holdout).
    """
    out: dict[str, dict[str, object]] = {}
    for case, entry in sorted(stats.items()):
        out[case] = {
            "split": splits[case],
            "tp": entry.tp,
            "fp": entry.fp,
            "fn": entry.fn,
            "tn": entry.tn,
        }
    return out


def _render_split(splits: dict[str, str]) -> list[str]:
    """The manifest header and one line per case, train first."""
    train = sorted(case for case, split in splits.items() if split == "train")
    holdout = sorted(case for case, split in splits.items() if split == "holdout")
    lines = [
        f"- train: {len(train)} cases, holdout: {len(holdout)} cases, "
        "every case in exactly one split",
        "",
        "## Split manifest",
        "",
    ]
    for case in train:
        lines.append(f"- {case}: train")
    for case in holdout:
        lines.append(f"- {case}: holdout")
    return lines


def _render_cases(name: str, rows: dict[str, dict[str, object]]) -> list[str]:
    """One judged-rows section; absent rows render nothing."""
    if not rows:
        return []
    lines = ["", f"## {name}", ""]
    for case, counts in rows.items():
        lines.append(
            f"- {case}: {counts['split']} "
            f"(tp {counts['tp']} fp {counts['fp']} fn {counts['fn']} tn {counts['tn']})"
        )
    return lines


def _render_gaps(gaps: Sequence[dict[str, str]]) -> list[str]:
    """A data-gaps section: one bullet per named-but-absent fixture."""
    if not gaps:
        return []
    lines = ["", "## Data gaps", ""]
    for gap in gaps:
        lines.append(f"- {gap['case']}: {gap['reason']}")
    return lines


def _render_violations(violations: Sequence[dict[str, str]]) -> list[str]:
    """A violations section: case, check and what disagreed."""
    if not violations:
        return []
    lines = ["", "## Violations", ""]
    for row in violations:
        lines.append(f"- {row['case']} {row['check']}: {row['detail']}")
    return lines


def _render(report: dict[str, object]) -> str:
    """The markdown: the split, the rows, the gaps - all visible."""
    lines = [
        "# Holdout split and real-repo corpus",
        "",
        f"- salt: {report['salt']} - ratio: {report['ratio']}% holdout",
    ]
    lines += _render_split(report["splits"])  # type: ignore[arg-type]
    lines += _render_cases("fixture_rows", report["fixture_rows"])  # type: ignore[arg-type]
    lines += _render_cases("real_rows", report["real_rows"])  # type: ignore[arg-type]
    lines += _render_gaps(report["gaps"])  # type: ignore[arg-type]
    lines += _render_violations(report["violations"])  # type: ignore[arg-type]
    lines.append("")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,  # noqa: docs - the exploded form keeps the shared CLI signature off the boilerplate floor
) -> int:
    """CLI: --fixtures-dir [--real-corpus PATH] [--ratio N] [--json PATH]."""
    parser = argparse.ArgumentParser(
        description="deterministic holdout split over the accuracy corpus"
    )
    parser.add_argument("--fixtures-dir", default=None, help="the labeled fixture corpus")
    parser.add_argument(
        "--real-corpus",
        default=None,
        help="the accuracy-real-v1 manifest (default: none, fixtures only)",
    )
    parser.add_argument("--ratio", type=int, default=DEFAULT_RATIO, help="holdout percent")
    parser.add_argument("--json", dest="json_path", default=None, help="write the report")
    args = parser.parse_args(argv)
    if args.fixtures_dir is None:
        parser.error("--fixtures-dir is required")
    fixtures_dir = Path(args.fixtures_dir)
    real_manifest = Path(args.real_corpus) if args.real_corpus else None
    try:
        text, report, code = run_holdout(fixtures_dir, real_manifest, args.ratio)
    except (CorpusError, ConfigError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"holdout: {exc}", file=sys.stderr)
        return 2
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
