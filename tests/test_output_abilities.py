"""Round-11 output abilities: --json, --badge, --list-checks.

The aislop-ci parity contract: a machine-readable findings document and
a score badge that can never disagree with the gate, plus the catalog
that keeps the registry inspectable without a scan. Determinism is the
law for all three: two runs over the same tree produce identical bytes.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from detector_module import scan
from slop_catalog import build_catalog, render_markdown
from slop_model import BLOCKING_CHECKS, REMEDIES, is_blocking
from slop_outputs import badge_markdown, write_badge, write_json

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "slop_cli.py"


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *argv], capture_output=True, text=True
    )


# ---------------------------------------------------------------------------
# --json: the findings document
# ---------------------------------------------------------------------------


def test_json_document_schema_and_scores(tmp_path):
    (tmp_path / "svc.py").write_text(
        "def run():\n    try:\n        work()\n    except Exception:\n        pass\n"
    )
    out = tmp_path / "out.json"
    proc = _run_cli(str(tmp_path), "--json", str(out), "--max-score", "99")
    assert proc.returncode in (0, 1), proc.stderr[-400:]
    data = json.loads(out.read_text())
    assert data["version"] == "findings-v1"
    assert data["scores"]["blocking"] >= 2
    assert {"baseline", "inline"} <= set(data["suppressed"])
    finding = data["findings"][0]
    assert {"check", "path", "line", "severity", "weight", "message", "remedy"} <= set(finding)


def test_json_is_deterministic(tmp_path):
    (tmp_path / "app.py").write_text("def helper():\n    return 42\n")
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    _run_cli(str(tmp_path), "--json", str(first), "--max-score", "99")
    _run_cli(str(tmp_path), "--json", str(second), "--max-score", "99")
    assert first.read_bytes() == second.read_bytes()


def test_json_severity_follows_the_registry(tmp_path):
    (tmp_path / "svc.py").write_text(
        "def run():\n    try:\n        work()\n    except Exception:\n        pass\n"
    )
    out = tmp_path / "out.json"
    _run_cli(str(tmp_path), "--json", str(out), "--max-score", "99")
    data = json.loads(out.read_text())
    swallow = next(f for f in data["findings"] if f["check"] == "swallowed-exception")
    assert swallow["severity"] == "blocking"
    assert swallow["check"] in BLOCKING_CHECKS


def test_write_json_reports_enforced_severity(tmp_path):
    """--enforce-bloat escalates the structural family; --json must say so."""
    from slop_model import enforce_structural_budget

    findings = scan(tmp_path, {"app.py": "def helper():\n    return 42\n"})
    enforce_structural_budget()
    try:
        out = tmp_path / "out.json"
        write_json(str(out), str(tmp_path), findings)
        data = json.loads(out.read_text())
        assert all(
            f["severity"] == ("blocking" if is_blocking(f["check"]) else "advisory")
            for f in data["findings"]
        )
    finally:
        from slop_model import ENFORCED_CHECKS

        ENFORCED_CHECKS.clear()


# ---------------------------------------------------------------------------
# --badge: the score badge
# ---------------------------------------------------------------------------

GRADES = ["A", "B", "C", "D", "F"]


@pytest.mark.parametrize("grade", GRADES)
def test_badge_renders_every_grade(grade, tmp_path):
    out = tmp_path / f"badge-{grade}.svg"
    snippet = write_badge(str(out), 0, grade)
    svg = out.read_text()
    assert "<svg" in svg and "slop 0/100" in svg
    assert grade in snippet and "shields.io" in snippet


def test_badge_is_deterministic_and_grade_colored(tmp_path):
    one = tmp_path / "a.svg"
    two = tmp_path / "b.svg"
    first = write_badge(str(one), 12, "B")
    second = write_badge(str(two), 12, "B")
    assert one.read_bytes() == two.read_bytes()
    assert first == second == badge_markdown(12, "B")
    assert "#97ca00" in one.read_text()


def test_cli_badge_matches_the_gate_score(tmp_path):
    (tmp_path / "app.py").write_text("value = compute()\n")
    out = tmp_path / "badge.svg"
    proc = _run_cli(str(tmp_path), "--badge", str(out))
    assert proc.returncode == 0
    data = proc.stdout
    # The badge and the summary line print the same slop score.
    summary = next(line for line in data.splitlines() if "slop=" in line)
    slop = int(summary.split("slop=")[1].split("/")[0])
    assert f"slop {slop}/100" in out.read_text()


# ---------------------------------------------------------------------------
# --list-checks: the registry catalog
# ---------------------------------------------------------------------------


def test_catalog_covers_the_whole_registry():
    rows = build_catalog()
    ids = [row["check"] for row in rows]
    assert sorted(ids) == sorted(REMEDIES)
    assert len(ids) == len(set(ids))


def test_catalog_severity_agrees_with_the_registry():
    for row in build_catalog():
        expected = "blocking" if is_blocking(row["check"]) else "advisory"
        assert row["severity"] == expected


def test_catalog_rows_carry_taxonomy_and_remedy():
    for row in build_catalog():
        assert row["category"]
        assert row["remedy"] == REMEDIES[row["check"]]


def test_catalog_enforced_escalation_is_reflected():
    rows = build_catalog(enforced={"bloat-long-file"})
    row = next(r for r in rows if r["check"] == "bloat-long-file")
    assert row["severity"] == "blocking"


def test_markdown_catalog_is_a_table():
    text = render_markdown(build_catalog())
    assert text.startswith("# Master CI check catalog")
    assert "| `ai-prose` |" in text
    assert text.count("\n|") == len(REMEDIES) + 2  # header rows + body


def test_cli_list_checks_json_and_md(tmp_path):
    proc = _run_cli("--list-checks", "--catalog-format", "json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["version"] == "catalog-v1"
    proc_md = _run_cli("--list-checks", "--catalog-format", "md")
    assert proc_md.returncode == 0
    assert proc_md.stdout.startswith("# Master CI check catalog")


def test_cli_without_root_fails_closed():
    proc = _run_cli("--max-score", "8")
    assert proc.returncode == 2
    assert "repo path" in proc.stderr
