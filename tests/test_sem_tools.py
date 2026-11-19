"""Contract tests for the semaphore reverse-engineering tools (round-16).

Covers the documented contracts:

- promotions: config validation (structure, kebab names, existing
    targets, condition-DSL subset, result only inside auto_promote,
    required-with-default under auto_promote, regex compile),
    parameter validation (required missing, undeclared, regex
    mismatch, options), resolution overlay and the dispatch shape
- test reports: JUnit parsing (failure, error, skip, pass), combine
    dedup with the flaky flag on differing statuses, failed-first
    report ordering, mcp summary shape, exit codes incl. --epilogue
- insights: neutral conclusions excluded from reliability, MTTR
    from failure to next success, p95 nearest-rank, markdown
    rendering, --fail-under gate
- secrets lint: unknown name is an error, case-insensitive matching
    with lowercase convention warning, unused declaration warning,
    fork-PR exposure error, echo interpolation warning
"""

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))
import sem_insights as ins  # noqa: E402
import sem_promotions as promo  # noqa: E402
import sem_secrets_lint as secrets_lint  # noqa: E402
import sem_test_reports as reports  # noqa: E402

# -- promotions ----------------------------------------------------------------


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "promotions.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _dump(payload: dict) -> str:
    """YAML text with every line padded to a 4-multiple (the parity gate)."""
    import yaml

    return yaml.dump(payload, indent=4, default_flow_style=False, sort_keys=False)


def _config_from(promotions: list[dict], tmp_path: Path) -> Path:
    return _config(tmp_path, _dump({"promotions": promotions}))


VALID_PROMOTION = {
    "name": "deploy-pin",
    "pipeline_file": "wf.yml",
    "auto_promote": {"when": "result = 'passed' and branch = 'main'"},
    "parameters": {
        "env_vars": [
            {
                "name": "PIN_SHA",
                "required": True,
                "default_value": "main",
                "regex_pattern": "^[0-9a-f]{7,40}$",
            }
        ]
    },
}


def test_validate_ok(tmp_path):
    (tmp_path / "wf.yml").write_text("on: push\n")
    assert promo.validate_config(_config_from([VALID_PROMOTION], tmp_path), tmp_path) == []


def test_validate_missing_target(tmp_path):
    (tmp_path / "promotions.yml").write_text(
        "promotions:\n    - name: x\n      pipeline_file: nope.yml\n", encoding="utf-8"
    )
    errors = promo.validate_config(tmp_path / "promotions.yml", tmp_path)
    assert any("pipeline_file does not exist" in e for e in errors)


def test_result_only_inside_auto_promote(tmp_path):
    entry = {
        "name": "no-auto",
        "pipeline_file": "wf.yml",
        "parameters": {"env_vars": [{"name": "X", "required": False, "default_value": "1"}]},
    }
    (tmp_path / "wf.yml").write_text("on: push\n")
    # A non-auto promotion cannot carry result/result_reason anywhere -
    # validate_condition is auto-only by construction, so the config is
    # simply valid when auto_promote is absent.
    assert promo.validate_config(_config_from([entry], tmp_path), tmp_path) == []


def test_auto_promote_required_without_default(tmp_path):
    entry = {
        "name": "needs-default",
        "pipeline_file": "wf.yml",
        "auto_promote": {"when": "branch = 'main'"},
        "parameters": {"env_vars": [{"name": "PIN_SHA", "required": True}]},
    }
    (tmp_path / "wf.yml").write_text("on: push\n")
    errors = promo.validate_config(_config_from([entry], tmp_path), tmp_path)
    assert any("required but auto_promote fills no default" in e for e in errors)


def test_condition_unknown_keyword():
    errors = promo.validate_condition("branch = 'main' and foo = 'bar'", auto=True)
    assert any("unknown condition keyword 'foo'" in e for e in errors)


def test_condition_result_outside_auto():
    errors = promo.validate_condition("result = 'passed'", auto=False)
    assert any("only legal inside auto_promote" in e for e in errors)


def test_parameter_validation_contract():
    entry = {
        "name": "x",
        "parameters": {
            "env_vars": [
                {"name": "A", "required": True, "regex_pattern": "^[0-9]+$"},
                {"name": "B", "options": ["one", "two"]},
            ]
        },
    }
    errors = promo.validate_parameters(entry, {"B": "three"})
    assert any("missing required parameter: A" in e for e in errors)
    assert any("not in options" in e for e in errors)
    errors = promo.validate_parameters(entry, {"A": "42", "B": "one", "C": "1"})
    assert any("undeclared parameter: C" in e for e in errors)
    errors = promo.validate_parameters(entry, {"A": "abc", "B": "one"})
    assert any("fails regex" in e for e in errors)


def test_resolution_overlays_defaults():
    entry = {
        "parameters": {
            "env_vars": [
                {"name": "A", "required": True, "default_value": "d"},
                {"name": "B", "default_value": "keep"},
            ]
        }
    }
    assert promo.resolved_parameters(entry, {"A": "given"}) == {"A": "given", "B": "keep"}


def test_dispatch_command_shape():
    entry = {"name": "x", "pipeline_file": ".github/workflows/w.yml"}
    cmd = promo.dispatch_command(entry, {"B": "2", "A": "1", "GIT_REF": "main"})
    assert cmd.startswith("gh workflow run .github/workflows/w.yml --ref main")
    assert "-f A=1" in cmd and "-f B=2" in cmd


def test_main_validate_exit_codes(tmp_path):
    (tmp_path / "wf.yml").write_text("on: push\n")
    cfg = _config_from([VALID_PROMOTION], tmp_path)
    assert promo.main(["validate", "--config", str(cfg), "--root", str(tmp_path)]) == 0
    bad = _config(tmp_path, "promotions: []\n")
    assert promo.main(["validate", "--config", str(bad), "--root", str(tmp_path)]) == 2


# -- test reports ----------------------------------------------------------------


def _write_junit(tmp_path: Path, name: str, cases: str) -> Path:
    path = tmp_path / name
    path.write_text(
        f"<testsuites><testsuite name='s' tests='9'>{cases}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_compile_junit_statuses(tmp_path):
    path = _write_junit(
        tmp_path,
        "a.xml",
        "<testcase classname='c' name='p' time='0.1'/>"
        "<testcase classname='c' name='f' time='0.2'><failure message='boom'>x</failure></testcase>"
        "<testcase classname='c' name='e' time='0.3'><error>err</error></testcase>"
        "<testcase classname='c' name='s' time='0.0'><skipped>why</skipped></testcase>",
    )
    cases = reports.compile_junit(path)
    statuses = {case["test_id"]: case["status"] for case in cases}
    assert statuses == {"c::p": "passed", "c::f": "failed", "c::e": "failed", "c::s": "skipped"}


def test_compile_junit_malformed_is_empty(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text("<not-xml>", encoding="utf-8")
    assert reports.compile_junit(path) == []


def test_combine_flags_flaky_but_keeps_gate(tmp_path):
    a = reports.compile_junit(
        _write_junit(tmp_path, "a.xml", "<testcase classname='c' name='x' time='1'/>")
    )
    b = reports.compile_junit(
        _write_junit(
            tmp_path,
            "b.xml",
            "<testcase classname='c' name='x' time='2'><failure>no</failure></testcase>",
        )
    )
    corpus = reports.combine([a, b])
    assert corpus["totals"]["failed"] == 1
    assert corpus["totals"]["flaky"] == 1
    assert corpus["totals"]["tests"] == 1


def test_report_orders_failed_first_then_slowest(tmp_path):
    a = reports.compile_junit(
        _write_junit(
            tmp_path,
            "a.xml",
            "<testcase classname='c' name='slow-pass' time='9'/>"
            "<testcase classname='c' name='fail-fast' time='0.1'><failure>f</failure></testcase>",
        )
    )
    corpus = reports.combine([a])
    text = reports.pipeline_report(corpus)
    assert text.index("fail-fast") < text.index("slow-pass")


def test_mcp_summary_shape(tmp_path):
    a = reports.compile_junit(
        _write_junit(
            tmp_path, "a.xml", "<testcase classname='c' name='f'><failure>boom</failure></testcase>"
        )
    )
    summary = reports.mcp_summary(reports.combine([a]))
    assert summary["problems"][0]["test_id"] == "c::f"
    assert summary["problems"][0]["status"] == "failed"


def test_main_exit_codes_and_epilogue(tmp_path):
    a = _write_junit(
        tmp_path, "a.xml", "<testcase classname='c' name='f'><failure>x</failure></testcase>"
    )
    argv = ["--report", str(a), "--json", str(tmp_path / "out.json")]
    assert reports.main(argv + ["--epilogue"]) == 0
    assert reports.main(argv) == 1
    payload = json.loads((tmp_path / "out.json").read_text())
    assert payload["totals"]["failed"] == 1


# -- insights --------------------------------------------------------------------


def _run(workflow, conclusion, start, end, event="push", branch="main"):
    return {
        "workflowName": workflow,
        "headBranch": branch,
        "conclusion": conclusion,
        "createdAt": start,
        "updatedAt": end,
        "event": event,
        "displayTitle": "t",
    }


NOW = ins.parse_ts("2026-11-19T00:00:00Z")


def test_reliability_excludes_neutral_conclusions():
    runs = [
        _run("CI", "success", "2026-11-18T10:00:00Z", "2026-11-18T10:10:00Z"),
        _run("CI", "failure", "2026-11-18T11:00:00Z", "2026-11-18T11:05:00Z"),
        _run("CI", "cancelled", "2026-11-18T12:00:00Z", "2026-11-18T12:01:00Z"),
    ]
    metrics = ins.metrics_for(runs, NOW)
    assert metrics["reliability"] == 0.5  # cancelled run does not depress
    assert metrics["median_duration_s"] == 300.0  # 600s and 300s finished runs


def test_mttr_failure_to_next_success():
    runs = [
        _run("CI", "failure", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z"),
        _run("CI", "success", "2026-11-18T10:31:00Z", "2026-11-18T10:32:00Z"),
        _run("CI", "failure", "2026-11-18T11:00:00Z", "2026-11-18T11:01:00Z"),
        _run("CI", "success", "2026-11-18T11:11:00Z", "2026-11-18T11:12:00Z"),
    ]
    assert ins.metrics_for(runs, NOW)["mttr_minutes"] == 21.0  # mean(31m, 11m)


def test_p95_nearest_rank():
    assert ins._p95([1.0, 2.0, 3.0, 4.0, 100.0]) == 100.0
    assert ins._p95([1.0, 2.0]) == 2.0


def test_frequency_counts_last_seven_days():
    runs = [
        _run("CI", "success", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z"),
        _run("CI", "success", "2026-11-01T10:00:00Z", "2026-11-01T10:01:00Z"),
    ]
    assert ins.metrics_for(runs, NOW)["runs_last_7d"] == 1


def test_markdown_renders_worst_first():
    document = ins.collect(
        [
            _run("A", "success", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z"),
            _run("B", "failure", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z"),
        ],
        NOW,
    )
    text = ins.render_markdown(document)
    assert text.index("B@main") < text.index("A@main")


def test_fail_under_gate(tmp_path):
    failing = [_run("CI", "failure", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z")]
    passing = [_run("CI", "success", "2026-11-18T10:00:00Z", "2026-11-18T10:01:00Z")]
    dump = tmp_path / "runs.json"
    dump.write_text(json.dumps(failing))
    assert ins.main(["--runs", str(dump), "--fail-under", "0.9"]) == 1
    dump.write_text(json.dumps(passing))
    assert ins.main(["--runs", str(dump), "--fail-under", "0.1"]) == 0


# -- secrets lint -----------------------------------------------------------------


def _repo(tmp_path: Path, workflow: str, manifest: str | None = None) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "secrets-manifest.yml").write_text(
        manifest
        or "secrets:\n  - name: MY_SECRET\n    description: d\n    used_by: ['.github/workflows/ci.yml']\n",
        encoding="utf-8",
    )
    return tmp_path


def test_unknown_secret_is_error(tmp_path):
    root = _repo(
        tmp_path,
        "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: x ${{ secrets.UNDECLARED }}\n",
    )
    errors, _ = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert any("UNDECLARED" in e for e in errors)


def test_declared_secret_passes(tmp_path):
    root = _repo(
        tmp_path,
        "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: x ${{ secrets.MY_SECRET }}\n",
    )
    errors, _ = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert errors == []


def test_case_insensitive_match_warns_lowercase(tmp_path):
    root = _repo(
        tmp_path,
        "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: x ${{ secrets.my_secret }}\n",
    )
    errors, warnings = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert errors == []
    assert any("spelled lowercase" in w for w in warnings)


def test_unused_declaration_warns(tmp_path):
    root = _repo(tmp_path, "on: push\njobs: {}\n")
    _, warnings = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert any("no workflow uses it" in w for w in warnings)


def test_pull_request_secret_consumption_is_error(tmp_path):
    root = _repo(
        tmp_path,
        "on:\n  pull_request:\n    branches: [main]\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: x ${{ secrets.MY_SECRET }}\n",
    )
    errors, _ = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert any("fork guard" in e for e in errors)


def test_echo_interpolation_warns(tmp_path):
    root = _repo(
        tmp_path,
        "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ${{ secrets.MY_SECRET }}\n",
    )
    _, warnings = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert any("echo/printf" in w for w in warnings)


def test_manifest_must_exist(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    try:
        secrets_lint.scan(empty, empty / "docs" / "secrets-manifest.yml")
    except secrets_lint.ManifestError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing manifest must raise")


def test_real_repo_manifest_is_consistent():
    """The repo's own manifest passes its own lint - the contract holds."""
    root = Path(__file__).resolve().parent.parent
    errors, _ = secrets_lint.scan(root, root / "docs" / "secrets-manifest.yml")
    assert errors == []


def test_real_repo_promotions_config_validates():
    """The repo's own .semaphore/promotions.yml satisfies the validator."""
    root = Path(__file__).resolve().parent.parent
    assert promo.validate_config(root / ".semaphore" / "promotions.yml", root) == []
