"""The benchmark seed: twelve declared-expected fixtures and their runner.

The roadmap's benchmark section ("the proof competitors cannot fake")
promises a fixture set where every fixture carries a declared expected
outcome and a runner judges the shipped engines against the
declarations. This module is that seed. Each fixture under
fixtures/bench/<slug>/ declares its expectation in manifest.json (slug,
rule, engine, expected, reason); the runner imports the engine functions
directly - no subprocess python - scans only that fixture's own subtree,
and maps the outcome:

- expected "detected" passes when the rule's check id appears in the
engine's findings for the fixture, fails when it does not;
- expected "clean" passes when the check id does not appear, fails when
it does;
- expected "pending" is judged by nobody: the row reports PENDING with
the manifest's reason. The seed shipped with two pendings; v2.17.0
ships both phases - the long-file verdict consults the LCOM4 cohesion
oracle (a proven-cohesive long file reports `long-but-cohesive`
instead of the size-only bloat flag, and the row's probe additionally
demands the ordinary `bloat-long-file` verdict stayed silent) and
slop_test_redundancy judges exact duplicate test bodies - so all
twelve rows are proven today. The vocabulary keeps "pending" for the
next deferred measurement.

Precision and recall math are the audit's later phase and are out of
scope here; the seed proves only that each fixture produces its declared
result - PASS / FAIL / PENDING - with deterministic output and
fail-closed exit codes: 0 with pendings allowed, 1 on any FAIL, 2 on
configuration errors (missing fixtures dir, malformed or disagreeing
manifest, a missing or unknown member fixture).

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
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from fixture_bench_fixtures import build_git_fixture
from fixture_bench_round8 import _ROUND8_CLEAN_RULES, _ROUND8_DETECTED, _ROUND8_RENAMED
from fixture_bench_round9 import ROUND9_CLEAN_RULES, ROUND9_DETECTED, ROUND9_RENAMED
from slop_abstraction import check_abstraction
from slop_bloat import check_bloat
from slop_comments import extract_comments
from slop_cpp import check_cpp_slop
from slop_csharp import check_csharp_slop
from slop_cycles import check_import_cycles
from slop_dead import check_unused_declarations
from slop_density import measure as measure_density
from slop_deps import check_unused_dependencies
from slop_duplicates import scan_duplicates
from slop_family import check_go, check_jvm_slop, check_rust
from slop_files import read_text_lax
from slop_hotspot import check_hotspot_metrics, churn_map
from slop_lcom4 import check_class_cohesion
from slop_lizard import lizard_available
from slop_model import Finding
from slop_patterns import FAMILY_OF_EXT
from slop_py_checks import check_py_ast
from slop_py_family import check_py_print_debug
from slop_test_redundancy import check_test_redundancy
from slop_ts_checks import check_ts_slop
from slop_type_decls import check_duplicate_type_decls

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bench"
MANIFEST_KEYS = {"slug", "rule", "engine", "expected", "reason"}
EXPECTED_VALUES = ("clean", "detected", "pending")


class ConfigError(Exception):
    """A benchmark misconfiguration: fail closed with exit code 2."""


class Pending(Exception):
    """A fixture that cannot be judged right now; message is the reason."""


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


# --- the per-fixture probes -------------------------------------------------

ProbeFn = Callable[[Path, list[Path]], list[Finding]]


def _py_pairs(root: Path, files: list[Path]) -> list[tuple[Path, str]]:
    """(path, rel) for the fixture's Python sources, sorted by rel path."""
    return [
        (path, path.relative_to(root).as_posix()) for path in sorted(files) if path.suffix == ".py"
    ]


def _probe_duplicates(root: Path, files: list[Path]) -> list[Finding]:
    del root  # the duplication oracle reads only the file list
    return scan_duplicates(files)


def _probe_lcom4(root: Path, files: list[Path]) -> list[Finding]:
    return check_class_cohesion(root, files, [])


def _probe_dead(root: Path, files: list[Path]) -> list[Finding]:
    return check_unused_declarations(root, files, [])


def _probe_family_scan(root: Path, files: list[Path]) -> list[Finding]:
    """The round-8 per-file family columns over one fixture tree.

    One shared probe for the whole tranche: the manifest names the check
    id it judges, so a single scanner sweep answers every row. Repo-level
    engines (duplication, density) stay out - the probe judges the named
    family oracle, not the whole scan.
    """
    del root
    scanners: dict[str, Callable[..., list[Finding]]] = {
        "csharp": check_csharp_slop,
        "cpp": check_cpp_slop,
        "js": check_ts_slop,
        "jvm": check_jvm_slop,
        "go": check_go,
        "rust": check_rust,
    }
    findings: list[Finding] = []
    for path in sorted(files):
        family = FAMILY_OF_EXT.get(path.suffix.lower(), "")
        runner = scanners.get(family)
        if runner is None:
            continue
        text = read_text_lax(path)
        if family == "cpp":
            findings.extend(runner(path, text, family))
        elif family == "rust":
            findings.extend(check_rust(path, text, family))
        else:
            findings.extend(runner(path, text, family, path.name))
    return findings


def _probe_type_decls(root: Path, files: list[Path]) -> list[Finding]:
    """The cross-file duplicate-type oracle over the fixture tree."""
    return check_duplicate_type_decls(root, files, [])


def _probe_deps(root: Path, files: list[Path]) -> list[Finding]:
    return check_unused_dependencies(root, files, [])


def _probe_cycles(root: Path, files: list[Path]) -> list[Finding]:
    return check_import_cycles(root, files, [])


def _probe_abstraction(root: Path, files: list[Path]) -> list[Finding]:
    """check_abstraction is per-file; aggregate over the fixture's .py files."""
    findings: list[Finding] = []
    for path, _rel in _py_pairs(root, files):
        findings.extend(check_abstraction(path, read_text_lax(path), "py"))
    return findings


def _probe_cyclomatic(root: Path, files: list[Path]) -> list[Finding]:
    """slop_bloat's budgets over the slop_lizard parse (high-cyclomatic here)."""
    if not lizard_available():
        raise Pending("lizard analyzer unavailable")
    findings: list[Finding] = []
    for path, rel in _py_pairs(root, files):
        text = read_text_lax(path)
        findings.extend(check_bloat(path, text, "py", rel, extract_comments(text, "py")))
    return findings


def _probe_hotspot(root: Path, files: list[Path]) -> list[Finding]:
    """Real history in a temp repo; PENDING on git trouble, never a guess."""
    del root, files  # the checked-in dir is manifest-only; history is built below
    if shutil.which("git") is None:
        raise Pending("git unavailable")
    if not lizard_available():
        raise Pending("lizard analyzer unavailable")
    with tempfile.TemporaryDirectory(prefix="fixture-bench-") as tmp:
        repo = Path(tmp) / "warehouse"
        if not build_git_fixture(repo):
            raise Pending("git unavailable")
        sources = sorted(repo.rglob("*.py"))
        churn, defects = churn_map(repo)
        facts = measure_density(repo, sources, [])
        if facts is None:
            return []
        return check_hotspot_metrics(facts, None, churn, defects)


def _probe_test_redundancy(root: Path, files: list[Path]) -> list[Finding]:
    """check_test_redundancy is per-file; aggregate over the fixture's .py files."""
    findings: list[Finding] = []
    for path, rel in _py_pairs(root, files):
        text = read_text_lax(path)
        findings.extend(check_test_redundancy(path, text, rel))
    return findings


@dataclass(frozen=True)
class Probe:
    """One fixture's engine contract: the finding engine and its check id.

    `absent` names check ids that must stay silent for a detected
    declaration to pass - the long-but-cohesive row proves not only
    that the context-aware verdict fired but that the ordinary
    `bloat-long-file` verdict did not.
    """

    engine: str
    rule: str
    run: ProbeFn
    absent: tuple[str, ...] = ()


PROBES: dict[str, Probe] = {
    "abstraction-chain": Probe("slop_abstraction", "forwarding-chain", _probe_abstraction),
    "churn-hotspot": Probe("slop_hotspot", "hotspot-risk", _probe_hotspot),
    "circular-dependencies": Probe("slop_cycles", "import-cycle", _probe_cycles),
    "duplicate-implementation": Probe(
        "slop_duplicates", "boilerplate-duplication", _probe_duplicates
    ),
    "dynamic-registration": Probe("slop_dead", "unused-declaration", _probe_dead),
    "framework-wrapper": Probe("slop_abstraction", "no-behavior-class", _probe_abstraction),
    "long-but-cohesive": Probe(
        "slop_bloat",
        "long-but-cohesive",
        _probe_cyclomatic,
        absent=("bloat-long-file",),
    ),
    "low-cohesion": Probe("slop_lcom4", "low-cohesion", _probe_lcom4),
    "redundant-tests": Probe("slop_test_redundancy", "redundant-test", _probe_test_redundancy),
    "short-but-complex": Probe("slop_bloat", "high-cyclomatic", _probe_cyclomatic),
    "unnecessary-dependency": Probe("slop_deps", "unused-dependency", _probe_deps),
    "unused-function": Probe("slop_dead", "unused-declaration", _probe_dead),
    # --- round-8 tranche: the family columns + the cross-file oracle ---
    # Data-driven: every detected slug's rule is its own name; the clean
    # rows and the cross-file oracle carry their explicit mapping.
}
PROBES["duplicate-type-declaration"] = Probe(
    "slop_type_decls", "duplicate-type-declaration", _probe_type_decls
)
for _SLUG in _ROUND8_DETECTED:
    PROBES[_SLUG] = Probe("slop_family", _ROUND8_RENAMED.get(_SLUG, _SLUG), _probe_family_scan)
for _CLEAN_SLUG, _CLEAN_RULE in _ROUND8_CLEAN_RULES.items():
    if _CLEAN_SLUG.startswith("duplicate"):
        PROBES[_CLEAN_SLUG] = Probe("slop_type_decls", _CLEAN_RULE, _probe_type_decls)
    else:
        PROBES[_CLEAN_SLUG] = Probe("slop_family", _CLEAN_RULE, _probe_family_scan)


def _probe_py_family(root: Path, files: list[Path]) -> list[Finding]:
    """The round-9 Python family column over one fixture tree.

    One shared probe for the tranche: broad-except is driven by the
    check_py_ast walk, print-debug by its own module - the manifest
    names the check id it judges, so one sweep answers every row. The
    fixture filename is the rel path, which keeps the scoping
    exemptions (entrypoints, script dirs) honest for the print rows.
    """
    del root
    findings: list[Finding] = []
    for path in sorted(files):
        if path.suffix.lower() != ".py":
            continue
        text = read_text_lax(path)
        findings.extend(check_py_ast(path, text, "py"))
        findings.extend(check_py_print_debug(path, text, "py", path.name))
    return findings


for _SLUG in ROUND9_DETECTED:
    PROBES[_SLUG] = Probe("slop_py_family", ROUND9_RENAMED.get(_SLUG, _SLUG), _probe_py_family)
for _CLEAN_SLUG, _CLEAN_RULE in ROUND9_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_py_family", _CLEAN_RULE, _probe_py_family)


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
