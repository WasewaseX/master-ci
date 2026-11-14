"""Contract tests for the benchmark seed (roadmap: the proof competitors cannot fake).

Covers the seed's declared contract:
- every manifest parses, carries exactly the five declaration keys, and
the twelve roadmap rows are all present under fixtures/bench/,
- manifest files are 2-space indented, key-sorted and newline-terminated,
- the runner's registry agrees with every manifest on engine and rule,
- each DETECTED fixture produces its check id, each CLEAN fixture stays
silent, and the two PENDING rows report PENDING with their reason,
- exit codes: 0 with pendings allowed, 1 forced by a declaration the
engine contradicts, 2 on missing dir and malformed manifests,
- deterministic output bytes across repeated runs, table and json,
- hygiene: no .git internals under fixtures/bench, every fixture file
tiny, ASCII, concrete (no placeholder tokens, no TODO markers),
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

import fixture_benchmark as fb  # noqa: E402
from fixture_bench_fixtures import HOTSPOT_COMMITS  # noqa: E402

BENCH = _ROOT / "fixtures" / "bench"
SCRIPT = _ROOT / "scripts" / "fixture_benchmark.py"
ROADMAP_SLUGS = {
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


def test_manifests_cover_the_twelve_roadmap_rows():
    """Every fixture dir declares exactly the roadmap table's twelve slugs."""
    rows = fb.load_benchmark(BENCH)
    assert {row.slug for row in rows} == ROADMAP_SLUGS
    assert len(rows) == 12
    for row in rows:
        assert row.expected in ("detected", "clean", "pending")
        if row.expected == "pending":
            assert row.reason, "a pending row must carry its reason"
    pendings = {row.slug for row in rows if row.expected == "pending"}
    assert pendings == {"long-but-cohesive", "redundant-tests"}


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
        assert result.observed == row.rule


def test_clean_fixtures_stay_clean():
    """Every clean declaration is met: the named check id never appears."""
    for row in fb.load_benchmark(BENCH):
        if row.expected != "clean":
            continue
        result = fb.evaluate(row, BENCH)
        assert result.verdict == "PASS", (row.slug, result.observed)
        assert result.observed == "none"


def test_pending_rows_stay_pending_with_reason():
    """Pending rows are judged by nobody: PENDING, the manifest's reason."""
    for row in fb.load_benchmark(BENCH):
        if row.expected != "pending":
            continue
        result = fb.evaluate(row, BENCH)
        assert result.verdict == "PENDING"
        assert result.observed == "not-judged"
        assert result.reason == row.reason


# --- CLI contract -----------------------------------------------------------


def test_table_run_exit_zero_with_pendings():
    """The shipped benchmark runs green: exit 0, twelve rows, sorted by slug."""
    out, code = fb.run_benchmark(BENCH, "table")
    lines = out.splitlines()
    assert code == 0
    assert lines[0].split() == ["slug", "rule", "expected", "observed", "verdict"]
    data_rows = lines[1:-1]
    assert len(data_rows) == 12
    slugs = [line.split()[0] for line in data_rows]
    assert slugs == sorted(slugs)
    assert lines[-1] == "12 fixtures: 10 pass, 0 fail, 2 pending"


def test_json_run_shape_and_summary():
    """The json payload lists twelve fixtures with a matching verdict summary."""
    out, code = fb.run_benchmark(BENCH, "json")
    payload = json.loads(out)
    assert code == 0
    assert {fixture["slug"] for fixture in payload["fixtures"]} == ROADMAP_SLUGS
    summary = payload["summary"]
    assert summary["total"] == 12
    assert summary["pass"] == 10 and summary["fail"] == 0 and summary["pending"] == 2


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
    """Every fixture file: under 30 lines, ASCII, no placeholders, no TODO."""
    import re

    for path in sorted(BENCH.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
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
