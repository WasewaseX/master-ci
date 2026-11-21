"""Contract tests for the benchmark (roadmap: the proof competitors cannot fake).

Covers the benchmark's declared contract:
- every manifest parses, carries exactly the five declaration keys, and
the twelve roadmap rows are all present under fixtures/bench/,
- manifest files are 2-space indented, key-sorted and newline-terminated,
- the runner's registry agrees with every manifest on engine and rule,
- each DETECTED fixture produces its check id, each CLEAN fixture stays
silent, and no row is pending any more: v2.17 shipped the two phases
the seed deferred (the pending vocabulary itself is still honored and
is pinned with a synthetic row),
- exit codes: 0 with the twelve proven rows, 1 forced by a declaration
the engine contradicts - including the long-but-cohesive row's demand
that the ordinary long-file verdict stayed silent - 2 on missing dir
and malformed manifests,
- deterministic output bytes across repeated runs, table and json,
- hygiene: no .git internals under fixtures/bench, every fixture file
tiny except the long-but-cohesive corpus (whose length is the point),
ASCII, concrete (no placeholder tokens, no TODO markers),
- the churn fixture builds real git history in a temp repo, and a
missing git reports PENDING with a reason, never a guessed count.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT / "scripts"))

import fixture_bench_probes as fp  # noqa: E402
import fixture_benchmark as fb  # noqa: E402
from fixture_bench_fixtures import HOTSPOT_COMMITS  # noqa: E402
from fixture_bench_round15 import (  # noqa: E402
    ROUND15_CSHARP_CLEAN_RULES,
    ROUND15_CSHARP_DETECTED,
    ROUND15_CSHARP_RENAMED,
)

BENCH = _ROOT / "fixtures" / "bench"
SCRIPT = _ROOT / "scripts" / "fixture_benchmark.py"
SEED_SLUGS = {
    "abstraction-chain",
    "churn-hotspot",
    "circular-dependencies",
    "duplicate-implementation",
    "dynamic-registration",
    "framework-wrapper",
    "long-but-cohesive",
    "low-cohesion",
    "redundant-tests",
    "short-but-complex",
    "unnecessary-dependency",
    "unused-function",
}

# Round-8 tranche: the family columns and the cross-file type oracle,
# one labeled row per new check plus the nearest negative for the
# oracles whose exemption logic a future edit could break.
ROUND8_SLUGS = {
    "csharp-async-void",
    "csharp-broad-catch",
    "csharp-catch-reads-exception-clean",
    "csharp-console-leftover",
    "csharp-empty-catch-rethrow",
    "csharp-if-ladder",
    "csharp-index-loop",
    "csharp-linq-count",
    "csharp-not-implemented",
    "csharp-null-forgiving",
    "csharp-null-guard-clean",
    "csharp-redundant-doc-comment",
    "csharp-string-concat-in-loop",
    "csharp-suppressed-warning",
    "csharp-sync-over-async",
    "cpp-c-style-cast",
    "cpp-define-constant",
    "cpp-endl-in-stream",
    "cpp-iostream-leftover",
    "cpp-manual-delete",
    "cpp-modern-clean",
    "cpp-not-implemented",
    "cpp-null-literal",
    "cpp-using-namespace-std-in-header",
    "ts-redundant-coercion",
    "ts-hidden-fallback",
    "ts-loud-catch-clean",
    "go-library-panic",
    "go-main-panic-clean",
    "rust-unwrap",
    "rust-test-unwrap-clean",
    "scala-broad-catch",
    "duplicate-type-declaration",
    "duplicate-type-clean",
}

ROUND9_SLUGS = {
    "py-broad-except",
    "py-broad-declared-clean",
    "py-print-debug",
    "py-print-main-guard-clean",
}

# Round-10 tranche: the accuracy corpus deepening - four checks pushed
# to the five-judged-row promotion threshold, each with the
# exemption-protecting clean row the corpus protocol asks for.
ROUND10_SLUGS = {
    "csharp-async-void-queue-worker",
    "csharp-async-void-service-save",
    "csharp-async-void-handler-pair",
    "csharp-async-void-callback",
    "csharp-async-void-on-prefix-clean",
    "csharp-broad-catch-log-and-default",
    "csharp-broad-catch-system-qualified",
    "csharp-broad-catch-two-handlers",
    "csharp-broad-catch-cache-fallback",
    "csharp-broad-catch-logs-exception-clean",
    "rust-unwrap-config-lookup",
    "rust-unwrap-parse-result",
    "rust-unwrap-lock-poison",
    "rust-unwrap-before-cfg-test",
    "rust-unwrap-expect-clean",
    "py-print-service-module",
    "py-print-model-module",
    "py-print-nested-helper",
    "py-print-multiple-sites",
    "py-print-stderr-clean",
}

# Round-13 tranche: the C/C++ accuracy column deepening - the whole
# eight-check column pushed to the five-judged-row promotion threshold,
# each check with four diverse positives and one exemption-pinning
# nearest-negative clean row.
ROUND13_SLUGS = {
    "cpp-cast-driver-cast",
    "cpp-cast-db-pointer",
    "cpp-cast-ratio-division",
    "cpp-cast-byte-length",
    "cpp-cast-named-casts-clean",
    "cpp-define-timeout-ms",
    "cpp-define-default-port",
    "cpp-define-oversample",
    "cpp-define-local-path-max",
    "cpp-define-constexpr-clean",
    "cpp-endl-logger-flush",
    "cpp-endl-progress-line",
    "cpp-endl-report-row",
    "cpp-endl-banner-loop",
    "cpp-endl-newline-clean",
    "cpp-iostream-debug-trace",
    "cpp-iostream-inspect-cout",
    "cpp-iostream-cerr-parser",
    "cpp-iostream-two-sites",
    "cpp-iostream-cli-main-clean",
    "cpp-delete-cache-raw",
    "cpp-delete-buffer-parse",
    "cpp-delete-plugin-release",
    "cpp-delete-two-resources",
    "cpp-delete-unique-ptr-clean",
    "cpp-notimpl-interface-stub",
    "cpp-notimpl-platform-gap",
    "cpp-notimpl-assert-stub",
    "cpp-notimpl-optional-cap",
    "cpp-notimpl-real-throw-clean",
    "cpp-null-legacy-call",
    "cpp-null-member-init",
    "cpp-null-handle-reset",
    "cpp-null-default-arg",
    "cpp-null-nullptr-clean",
    "cpp-usingns-widget-header",
    "cpp-usingns-util-header",
    "cpp-usingns-logger-header",
    "cpp-usingns-geometry-header",
    "cpp-usingns-tu-clean",
}

# Round-15 tranche: the C# accuracy column deepening - the eleven
# one-judged-row csharp checks pushed to the five-judged-row promotion
# threshold, each with four diverse positives and one
# exemption-pinning nearest-negative clean row.
ROUND15_SLUGS = (
    set(ROUND15_CSHARP_DETECTED) | set(ROUND15_CSHARP_RENAMED) | set(ROUND15_CSHARP_CLEAN_RULES)
)

# round-19 (roadmap A9): the similarity-scored near-duplicate, one
# detected row and its exemption-pinning clean row.
ROUND19_SLUGS = {
    "near-duplicate-clone",
    "near-duplicate-clone-clean",
}


BENCH_SLUGS = (
    SEED_SLUGS
    | ROUND8_SLUGS
    | ROUND9_SLUGS
    | ROUND10_SLUGS
    | ROUND13_SLUGS
    | ROUND15_SLUGS
    | ROUND19_SLUGS
)


def run_cli(*args: str, cwd: Path = _ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def row_by_slug(rows: list[fb.BenchRow], slug: str) -> fb.BenchRow:
    return next(row for row in rows if row.slug == slug)


# --- declarations -----------------------------------------------------------


def test_manifests_cover_the_benchmark_table():
    """Every fixture dir declares exactly the table's slugs: seed to round-15."""
    rows = fb.load_benchmark(BENCH)
    assert {row.slug for row in rows} == BENCH_SLUGS
    assert len(rows) == 167
    assert {row.slug for row in rows} >= SEED_SLUGS
    for row in rows:
        assert row.expected in ("detected", "clean", "pending")
        if row.expected == "pending":
            assert row.reason, "a pending row must carry its reason"
    pendings = {row.slug for row in rows if row.expected == "pending"}
    assert pendings == set(), "both deferred phases shipped in v2.17: none may stay pending"


def test_manifest_format_is_sorted_and_terminated():
    """Manifests are 2-space indented, key-sorted, newline-terminated JSON."""
    for manifest_path in sorted(BENCH.rglob("manifest.json")):
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"
        assert set(data) == {"slug", "rule", "engine", "expected", "reason"}


def test_runner_registry_agrees_with_every_manifest():
    """The runner's probe table and the manifests never drift apart."""
    for manifest_path in sorted(BENCH.rglob("manifest.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        probe = fb.PROBES[data["slug"]]
        assert probe.engine == data["engine"]
        assert probe.rule == data["rule"]


# --- verdicts ---------------------------------------------------------------


def test_detected_fixtures_produce_their_check_id():
    """Every detected declaration is met: the rule id appears in the findings."""
    for row in fb.load_benchmark(BENCH):
        if row.expected != "detected":
            continue
        result = fb.evaluate(row, BENCH)
        assert result.verdict == "PASS", (row.slug, result.observed)
        # the named rule must fire; sibling family findings may ride along
        assert row.rule in result.observed.split(","), (row.slug, result.observed)


def test_clean_fixtures_stay_clean():
    """Every clean declaration is met: the named check id never appears."""
    for row in fb.load_benchmark(BENCH):
        if row.expected != "clean":
            continue
        result = fb.evaluate(row, BENCH)
        assert result.verdict == "PASS", (row.slug, result.observed)
        assert result.observed == "none"


def test_pending_vocabulary_still_judges_nobody():
    """A pending declaration is judged by nobody - the seed's contract survives.

    No shipped row is pending any more, so the vocabulary is pinned with
    a synthetic row: evaluate() must report PENDING without running the
    probe, the exact not-judged shape the seed promised.
    """
    row = fb.BenchRow(
        slug="redundant-tests",
        rule="redundant-test",
        engine="slop_test_redundancy",
        expected="pending",
        reason="a future deferred measurement would carry its reason here",
    )
    result = fb.evaluate(row, BENCH)
    assert result.verdict == "PENDING"
    assert result.observed == "not-judged"
    assert result.reason == row.reason


# --- CLI contract -----------------------------------------------------------


def test_table_run_exits_zero_all_proven():
    """The shipped benchmark runs green: exit 0, twelve rows, sorted by slug."""
    out, code = fb.run_benchmark(BENCH, "table")
    lines = out.splitlines()
    assert code == 0
    assert lines[0].split() == ["slug", "rule", "expected", "observed", "verdict"]
    data_rows = lines[1:-1]
    assert len(data_rows) == 167
    slugs = [line.split()[0] for line in data_rows]
    assert slugs == sorted(slugs)
    assert lines[-1] == "167 fixtures: 167 pass, 0 fail, 0 pending"


def test_json_run_shape_and_summary():
    """The json payload lists every fixture with a matching verdict summary."""
    out, code = fb.run_benchmark(BENCH, "json")
    payload = json.loads(out)
    assert code == 0
    assert {fixture["slug"] for fixture in payload["fixtures"]} == BENCH_SLUGS
    summary = payload["summary"]
    assert summary["total"] == 167
    assert summary["pass"] == 167 and summary["fail"] == 0 and summary["pending"] == 0


def test_failed_declaration_exits_one(tmp_path):
    """A declaration the engine contradicts fails the run with exit code 1."""
    copied = tmp_path / "bench"
    shutil.copytree(BENCH, copied)
    manifest_path = copied / "low-cohesion" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["expected"] = "clean"
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    proc = run_cli("--fixtures-dir", str(copied), "--format", "json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    failed = {f["slug"]: f["verdict"] for f in payload["fixtures"]}
    assert failed["low-cohesion"] == "FAIL"


# --- the two v2.17 rows -----------------------------------------------------


def _debt_source(rows: int = 110) -> str:
    """A long file whose class splits into two disconnected method groups."""
    lines = [
        '"""A long file with cohesion debt: two disjoint state clusters."""',
        "",
        "class SplitHub:",
        "    def __init__(self):",
        "        self.balance = 0",
        "        self.outbox = []",
        "",
    ]
    for index in range(rows):
        lines += [
            f"    def debit_{index}(self, amount):",
            "        self.balance += amount",
            "        return self.balance",
            "",
            f"    def post_{index}(self, message):",
            "        self.outbox.append(message)",
            "        return len(self.outbox)",
            "",
        ]
    return "\n".join(lines)


def test_long_but_cohesive_row_proves_the_context_aware_verdict():
    """The row passes on the context-aware verdict with the ordinary one silent."""
    row = row_by_slug(fb.load_benchmark(BENCH), "long-but-cohesive")
    assert fb.PROBES[row.slug].absent == ("bloat-long-file",)
    result = fb.evaluate(row, BENCH)
    assert result.verdict == "PASS"
    assert result.observed == "long-but-cohesive"


def test_long_but_cohesive_row_fails_when_the_ordinary_verdict_fires(tmp_path):
    """Swapping the fixture for a long file with cohesion debt flips the row to FAIL.

    The probe's absent constraint is what makes the row judge the
    context-aware phase honestly: a size-only engine that never consults
    cohesion cannot pass this row.
    """
    copied = tmp_path / "bench"
    shutil.copytree(BENCH, copied)
    (copied / "long-but-cohesive" / "store_ledger.py").write_text(_debt_source(), encoding="utf-8")
    row = row_by_slug(fb.load_benchmark(copied), "long-but-cohesive")
    result = fb.evaluate(row, copied)
    assert result.verdict == "FAIL"
    observed = result.observed.split(",")
    assert "bloat-long-file" in observed
    assert "long-but-cohesive" not in observed


def test_redundant_tests_row_and_its_distinct_test():
    """The row passes on redundant-test; the distinct test never joins a group."""
    row = row_by_slug(fb.load_benchmark(BENCH), "redundant-tests")
    result = fb.evaluate(row, BENCH)
    assert result.verdict == "PASS"
    assert result.observed == "redundant-test"
    fixture_dir = BENCH / "redundant-tests"
    findings = fp.probe_test_redundancy(fixture_dir, fb._fixture_files(fixture_dir))
    assert len(findings) == 1, "one finding per duplicate group"
    message = findings[0].message
    for twin in (
        "test_rounded_rate_matches_reference",
        "test_rounded_rate_matches_reference_again",
        "test_rounded_rate_matches_reference_repeated",
    ):
        assert twin in message
    assert "test_line_sum_matches_invoice_total" not in message
    assert message.count("(line") == 3


def test_missing_fixtures_dir_exits_two():
    """A missing fixtures dir is a configuration error: exit 2, stderr only."""
    proc = run_cli("--fixtures-dir", str(_ROOT / "fixtures" / "absent-bench"))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "not found" in proc.stderr


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.write_text("{not json", encoding="utf-8"), id="bad-json"),
        pytest.param(
            lambda p: p.write_text(
                json.dumps(
                    {
                        "slug": "mystery-fixture",
                        "rule": "low-cohesion",
                        "engine": "slop_lcom4",
                        "expected": "detected",
                        "reason": "",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            ),
            id="unknown-slug",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps(
                    {
                        "slug": "low-cohesion",
                        "rule": "bloat-long-file",
                        "engine": "slop_lcom4",
                        "expected": "detected",
                        "reason": "",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            ),
            id="stale-rule",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps(
                    {
                        "slug": "low-cohesion",
                        "rule": "low-cohesion",
                        "engine": "slop_lcom4",
                        "expected": "bogus",
                        "reason": "",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            ),
            id="bad-expected-value",
        ),
    ],
)
def test_malformed_manifests_exit_two(tmp_path, mutate):
    """Any malformed or disagreeing manifest fails closed with exit code 2."""
    copied = tmp_path / "bench"
    shutil.copytree(BENCH, copied)
    mutate(copied / "low-cohesion" / "manifest.json")
    proc = run_cli("--fixtures-dir", str(copied))
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_output_is_deterministic_across_runs(tmp_path):
    """Two runs render identical bytes, table and json alike."""
    first = run_cli("--fixtures-dir", str(BENCH), "--format", "table")
    second = run_cli("--fixtures-dir", str(BENCH), "--format", "table")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    json_out, json_code = fb.run_benchmark(BENCH, "json")
    assert json_code == 0
    assert json.loads(json_out) == json.loads(fb.run_benchmark(BENCH, "json")[0])


# --- fixture hygiene --------------------------------------------------------


def test_no_git_internals_under_fixtures():
    """The temp-repo rule holds: no .git directory ever lives under bench."""
    assert not any(path.name == ".git" for path in BENCH.rglob("*"))


def test_fixture_files_are_tiny_ascii_and_concrete():
    """Every fixture file: under 30 lines, ASCII, no placeholders, no TODO.

    The one exemption is the long-but-cohesive corpus, whose length is
    the declaration - it must exceed the 400-line budget, never fall
    back under it.
    """
    import re

    for path in sorted(BENCH.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if path.parent.name == "long-but-cohesive" and path.suffix == ".py":
            assert len(text.splitlines()) > 400, path
        else:
            assert len(text.splitlines()) < 30, path
        assert text.isascii(), path
        assert not re.search(r"\b(foo|bar|baz)\b", text), path
        assert "TODO" not in text and "todo" not in text, path


# --- the churn fixture's real history ---------------------------------------


def test_churn_fixture_builds_real_history(tmp_path):
    """The builder commits the seed plus every subject against stockroom.py."""
    from fixture_bench_fixtures import build_git_fixture

    repo = tmp_path / "warehouse"
    assert build_git_fixture(repo) is True
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--name-only", "--pretty=format:%s"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    blocks = [block for block in log.split("\n\n") if block.strip()]
    assert len(blocks) == 1 + len(HOTSPOT_COMMITS)
    touched = [block for block in blocks if "stockroom.py" in block]
    assert len(touched) >= 8
    assert (repo / "stockroom.py").is_file()


def test_missing_git_reports_pending_not_guessed(monkeypatch):
    """Without git the hotspot row reports PENDING 'git unavailable', no count."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    rows = fb.load_benchmark(BENCH)
    result = fb.evaluate(row_by_slug(rows, "churn-hotspot"), BENCH)
    assert result.verdict == "PENDING"
    assert result.reason == "git unavailable"
    assert result.observed == "not-judged"
