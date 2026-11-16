"""Contract tests for the structured metrics document (v2.18, audit finding G).

The pre-v2.18 snapshot bridge parsed metric values out of SARIF finding
MESSAGES, so a metric below its threshold - a healthy measurement -
produced no finding and the store lost the value. The structured
document carries the actual value with a measured/not-measured state
per metric, plus the corpus's analysis-coverage states. These tests
pin: healthy values travel (the 0.0 block density and the grade-A
reliability score are real data), not-measured is omitted by the
store mapping (never zero-filled), the document is fail-closed about
its schema, the CLI writes it, and the extractor prefers it while the
SARIF path stays the fallback - now reading the writer's rule-metadata
severity stamp that per-result reading silently missed.
"""

import json

import pytest
from slop_metrics_doc import (
    SCHEMA,
    build_document,
    metrics_from_document,
    read_document,
)
from slop_model import Finding
from slop_snapshot_extract import extract, main as extract_main


def _bundle(density=None, coverage=None, coupling=None, churn=None, defects=None):
    return {
        "density": density,
        "coverage": coverage,
        "coupling": coupling,
        "churn": churn or {},
        "defects": defects or {},
    }


def test_healthy_values_travel_not_just_failures():
    findings: list[Finding] = []
    doc = build_document(findings, _bundle())
    metrics = metrics_from_document(doc)
    # The G headline: a clean scan's real values reach the store.
    assert metrics["reliability-grade"] == "A"
    assert metrics["reliability-score"] == 0
    assert metrics["blocking-findings"] == 0.0
    assert metrics["advisory-findings"] == 0.0


def test_not_measured_is_omitted_never_zero_filled():
    findings = [Finding(path="a.py", line=1, check="low-cohesion", message="m", weight=1)]
    doc = build_document(findings, _bundle(density=None, coupling=None))
    metrics = metrics_from_document(doc)
    assert metrics["low-cohesion-classes"] == 1.0
    for absent in ("complexity-density", "debt-ratio-pct", "hotspot-top-risk", "max-fan-out"):
        assert absent not in metrics  # not-measured, not zero


def test_document_schema_and_coverage_section():
    from slop_density import DensityCoverage

    coverage = DensityCoverage(
        eligible=96,
        measured=94,
        parse_failed=("broken.py",),
        unsupported=1,
        unsupported_names=("scripts/tool.sh",),
        excluded_test=38,
    )
    doc = build_document([], _bundle(coverage=coverage))
    assert doc["schema"] == SCHEMA
    assert doc["coverage"]["measured"] == 94
    assert doc["coverage"]["parse_failed"] == ["broken.py"]
    assert doc["coverage"]["unsupported"] == ["scripts/tool.sh"]
    assert doc["coverage"]["excluded_by_rule"] == 38


def test_read_document_rejects_foreign_schema(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"schema": "other-v9", "metrics": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_document(path)
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError):
        read_document(path)


def test_cli_writes_the_document_end_to_end(tmp_path):
    import slop_cli
    from slop_files import load_phrases

    line = "VALUE = 1\n"
    for i in range(20):
        (tmp_path / f"m{i}.py").write_text(line * 16, encoding="utf-8")
    out = tmp_path / "metrics.json"
    argv = [str(tmp_path), "--metrics-json", str(out)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(slop_cli, "load_phrases", load_phrases)
        slop_cli.main(argv)
    assert out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA
    assert doc["metrics"]["complexity-density"]["state"] == "measured"
    assert doc["coverage"]["measured"] == 20


def test_extractor_prefers_the_document(tmp_path):
    doc_path = tmp_path / "metrics.json"
    doc_path.write_text(json.dumps(build_document([], _bundle())), encoding="utf-8")
    code = extract_main(["--metrics", str(doc_path)])
    assert code == 0


def test_extractor_requires_a_source():
    assert extract_main([]) == 2


def test_sarif_rule_metadata_severity_is_the_blocking_source():
    # The writer stamps security-severity on the RULE descriptor; the
    # bridge read it per result and silently counted zero blocking
    # findings since it shipped. The rule table is the source now.
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "rules": [
                            {
                                "id": "arch-forbidden-import",
                                "properties": {"security-severity": "8.0"},
                            },
                            {
                                "id": "low-cohesion",
                                "properties": {"security-severity": "3.0"},
                            },
                        ]
                    }
                },
                "results": [
                    {"ruleId": "arch-forbidden-import", "message": {"text": "x"}},
                    {"ruleId": "low-cohesion", "message": {"text": "y"}},
                    {"ruleId": "low-cohesion", "message": {"text": "z"}},
                ],
            }
        ]
    }
    metrics = extract(sarif)
    assert metrics["blocking-findings"] == 1.0
    assert metrics["advisory-findings"] == 2.0
    # Result-level stamps (the historical fixture shape) still win.
    sarif["runs"][0]["tool"]["driver"]["rules"] = []
    sarif["runs"][0]["results"][0]["properties"] = {"security-severity": "8.0"}
    assert extract(sarif)["blocking-findings"] == 1.0
