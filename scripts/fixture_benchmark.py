"""The benchmark runner: manifest judging, rendering, and the exit contract.

The roadmap's benchmark section ("the proof competitors cannot fake")
promises a fixture set where every fixture carries a declared expected
outcome and a runner judges the shipped engines against the
declarations. The engine contract per fixture - the Probe table - lives
in fixture_bench_probes.py; this module is the judge and the CLI. Each
manifest (slug, rule, engine, expected, reason) is validated against
the runner's table and mapped to an outcome:

- expected "detected" passes when the rule's check id appears in the
engine's findings for the fixture, fails when it does not;
- expected "clean" passes when the check id does not appear, fails
when it does;
- expected "pending" is judged by nobody: the row reports PENDING with
the manifest's reason. The vocabulary keeps "pending" for the next
deferred measurement.

Output is deterministic and fail-closed: 0 with pendings allowed, 1 on
any FAIL, 2 on configuration errors (missing fixtures dir, malformed or
disagreeing manifest, a missing or unknown member fixture).

Two details worth naming. The churn-hotspot fixture is built at run
time in a temp directory with real git history (see
fixture_bench_fixtures); git unavailable means PENDING with a reason,
never a guessed count, and no .git directory ever lives under
fixtures/. And the high-cyclomatic check id is emitted by slop_bloat
judging the slop_lizard parse, so the manifest names slop_bloat - the
module whose findings the runner reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from fixture_bench_probes import PROBES, ConfigError, Pending

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bench"
MANIFEST_KEYS = {"slug", "rule", "engine", "expected", "reason"}
EXPECTED_VALUES = ("clean", "detected", "pending")


@dataclass(frozen=True)
class BenchRow:
    """One manifest's declaration, validated against the runner's table."""

    slug: str
    rule: str
    engine: str
    expected: str
    reason: str


@dataclass(frozen=True)
class RowResult:
    """One judged fixture: observed check ids and the verdict."""

    row: BenchRow
    observed: str
    verdict: str
    reason: str


# --- manifest loading and judging -------------------------------------------


def _fixture_files(fixture_dir: Path) -> list[Path]:
    """Every file in the fixture subtree except the manifest itself."""
    return sorted(
        path
        for path in fixture_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts
    )


def _load_row(fixture_dir: Path) -> BenchRow:
    """Parse and validate one manifest; ConfigError on any disagreement."""
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ConfigError(f"{manifest_path}: missing manifest.json")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{manifest_path}: malformed manifest ({exc})") from exc
    if not isinstance(data, dict) or set(data) != MANIFEST_KEYS:
        raise ConfigError(f"{manifest_path}: manifest keys must be exactly {sorted(MANIFEST_KEYS)}")
    if any(not isinstance(data[key], str) for key in MANIFEST_KEYS):
        raise ConfigError(f"{manifest_path}: every manifest value must be a string")
    if data["slug"] != fixture_dir.name:
        raise ConfigError(f"{manifest_path}: slug {data['slug']!r} does not match the directory")
    probe = PROBES.get(data["slug"])
    if probe is None:
        raise ConfigError(f"{manifest_path}: unknown benchmark slug {data['slug']!r}")
    if data["engine"] != probe.engine or data["rule"] != probe.rule:
        raise ConfigError(
            f"{manifest_path}: declaration disagrees with the runner "
            f"(engine {probe.engine!r}, rule {probe.rule!r})"
        )
    if data["expected"] not in EXPECTED_VALUES:
        raise ConfigError(f"{manifest_path}: expected must be one of {sorted(EXPECTED_VALUES)}")
    if data["expected"] == "pending" and not data["reason"]:
        raise ConfigError(f"{manifest_path}: a pending row must carry its reason")
    return BenchRow(data["slug"], data["rule"], data["engine"], data["expected"], data["reason"])


def load_benchmark(fixtures_dir: Path) -> list[BenchRow]:
    """Every member fixture's declaration, slug-sorted, fail-closed.

    An unknown fixture directory is as much a misconfiguration as a
    missing one: the benchmark table is the contract, and a surprise
    member must not silently scan or silently vanish.
    """
    rows: list[BenchRow] = []
    seen: set[str] = set()
    for entry in sorted(fixtures_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name not in PROBES:
            raise ConfigError(f"{entry}: fixture slug is not in the benchmark table")
        seen.add(entry.name)
        rows.append(_load_row(entry))
    missing = sorted(set(PROBES) - seen)
    if missing:
        raise ConfigError(f"{fixtures_dir}: missing fixture dirs: {', '.join(missing)}")
    return sorted(rows, key=lambda row: row.slug)


def evaluate(row: BenchRow, fixtures_dir: Path) -> RowResult:
    """Judge one declaration against its engine; PENDING never guesses."""
    if row.expected == "pending":
        return RowResult(row, "not-judged", "PENDING", row.reason)
    probe = PROBES[row.slug]
    fixture_dir = fixtures_dir / row.slug
    try:
        findings = probe.run(fixture_dir, _fixture_files(fixture_dir))
    except Pending as pending:
        return RowResult(row, "not-judged", "PENDING", str(pending))
    observed_ids = sorted({finding.check for finding in findings})
    observed = ",".join(observed_ids) if observed_ids else "none"
    if row.expected == "detected":
        present = row.rule in observed_ids
        silent = not set(probe.absent).intersection(observed_ids)
        verdict = "PASS" if present and silent else "FAIL"
    else:
        verdict = "PASS" if row.rule not in observed_ids else "FAIL"
    return RowResult(row, observed, verdict, row.reason)


# --- rendering and the exit contract ----------------------------------------


def _counts(results: list[RowResult]) -> dict[str, int]:
    verdicts = [result.verdict for result in results]
    return {
        "pass": verdicts.count("PASS"),
        "fail": verdicts.count("FAIL"),
        "pending": verdicts.count("PENDING"),
    }


def _render_table(results: list[RowResult]) -> str:
    """One row per fixture: slug | rule | expected | observed | verdict."""
    headers = ("slug", "rule", "expected", "observed", "verdict")
    cells = [
        [result.row.slug, result.row.rule, result.row.expected, result.observed, result.verdict]
        for result in results
    ]
    widths = [
        max(len(headers[col]), *(len(row[col]) for row in cells)) for col in range(len(headers))
    ]
    lines = ["  ".join(header.ljust(width) for header, width in zip(headers, widths))]
    lines.extend("  ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in cells)
    counts = _counts(results)
    lines.append(
        f"{len(results)} fixtures: {counts['pass']} pass, {counts['fail']} fail, "
        f"{counts['pending']} pending"
    )
    return "\n".join(lines) + "\n"


def _render_json(results: list[RowResult]) -> str:
    payload = {
        "fixtures": [
            {
                "slug": result.row.slug,
                "rule": result.row.rule,
                "engine": result.row.engine,
                "expected": result.row.expected,
                "observed": result.observed,
                "verdict": result.verdict,
                "reason": result.reason,
            }
            for result in results
        ],
        "summary": {"total": len(results), **_counts(results)},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _exit_code(results: list[RowResult]) -> int:
    return 1 if any(result.verdict == "FAIL" for result in results) else 0


def run_benchmark(fixtures_dir: Path, fmt: str) -> tuple[str, int]:
    """Load, judge and render the benchmark; ConfigError on misconfiguration."""
    if fmt not in ("table", "json"):
        raise ConfigError(f"unknown format {fmt!r} (use table or json)")
    if not fixtures_dir.is_dir():
        raise ConfigError(f"fixtures dir not found: {fixtures_dir}")
    rows = load_benchmark(fixtures_dir)
    results = [evaluate(row, fixtures_dir) for row in rows]
    renderer = _render_json if fmt == "json" else _render_table
    return renderer(results), _exit_code(results)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: exit 0 with pendings, 1 on any FAIL, 2 on config errors."""
    parser = argparse.ArgumentParser(
        description="Judge the benchmark fixtures against their declared expected outcomes."
    )
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR))
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    try:
        output, code = run_benchmark(Path(args.fixtures_dir), args.format)
    except ConfigError as exc:
        print(f"fixture_benchmark: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
