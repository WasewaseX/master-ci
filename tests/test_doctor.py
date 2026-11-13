"""Contract tests for the doctor setup diagnostic.

Doctor verifies the consumer's workflow contract offline; these tests
build workflow fixtures in tmp_path and check every severity boundary:
errors fail the exit code, warnings do not, and an unreadable setup
fails closed with exit code 2. The YAML fixtures indent in multiples of
four so the editorconfig contract the template enforces on its own tree
stays honest even inside string literals.
"""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "doctor.py"
spec = importlib.util.spec_from_file_location("doctor", MOD_PATH)
assert spec is not None and spec.loader is not None
doctor = importlib.util.module_from_spec(spec)
sys.modules["doctor"] = doctor
spec.loader.exec_module(doctor)

HEALTHY = """
name: CI

on:
    push:
        branches: [main]
    pull_request:

permissions:
    contents: read
    security-events: write
    pull-requests: write
    id-token: write

jobs:
    master:
        uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@v2.0.0
        with:
            template_repo: WasewaseX/master-ci
            template_ref: v2.0.0
"""


def _workflows(tmp_path, files):
    target = tmp_path / ".github" / "workflows"
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / name).write_text(text, encoding="utf-8")
    return target


def test_healthy_pinned_consumer_has_no_errors(tmp_path):
    target = _workflows(tmp_path, {"ci.yml": HEALTHY})
    notes = doctor.diagnose(target)
    assert not [n for n in notes if n.severity == "error"]
    assert any(n.code == "doctor-pinned" for n in notes)
    assert doctor.main(["--workflows-dir", str(target)]) == 0


def test_mutable_ref_is_an_error(tmp_path):
    target = _workflows(tmp_path, {"ci.yml": HEALTHY.replace("@v2.0.0", "@main")})
    notes = doctor.diagnose(target)
    mutable = [n for n in notes if n.code == "doctor-mutable"]
    assert mutable and mutable[0].severity == "error"
    assert doctor.main(["--workflows-dir", str(target)]) == 1


def test_foreign_helper_without_pin_is_an_error(tmp_path):
    body = HEALTHY.replace("            template_ref: v2.0.0\n", "")
    target = _workflows(tmp_path, {"ci.yml": body})
    notes = doctor.diagnose(target)
    assert any(n.code == "doctor-missing-helper-ref" and n.severity == "error" for n in notes)


def test_implicit_helper_is_a_warning_not_an_error(tmp_path):
    body = HEALTHY.replace(
        "        with:\n            template_repo: WasewaseX/master-ci\n"
        "            template_ref: v2.0.0\n",
        "",
    )
    target = _workflows(tmp_path, {"ci.yml": body})
    notes = doctor.diagnose(target)
    implicit = [n for n in notes if n.code == "doctor-implicit-helper"]
    assert implicit and implicit[0].severity == "warning"
    assert doctor.main(["--workflows-dir", str(target)]) == 0


def test_missing_permissions_block_is_an_error(tmp_path):
    body = HEALTHY.replace(
        "permissions:\n    contents: read\n    security-events: write\n"
        "    pull-requests: write\n    id-token: write\n",
        "",
    )
    target = _workflows(tmp_path, {"ci.yml": body})
    notes = doctor.diagnose(target)
    assert any(n.code == "doctor-perms-missing" and n.severity == "error" for n in notes)


def test_missing_security_events_errors_and_missing_id_token_informs(tmp_path):
    body = HEALTHY.replace("    security-events: write\n", "").replace("    id-token: write\n", "")
    target = _workflows(tmp_path, {"ci.yml": body})
    notes = doctor.diagnose(target)
    sec = [n for n in notes if "security-events" in n.message]
    oidc = [n for n in notes if n.message.startswith("id-token")]
    assert sec and sec[0].severity == "error"
    assert oidc and oidc[0].severity == "info"


def test_coverage_min_range_is_enforced(tmp_path):
    bad = _workflows(tmp_path, {"ci.yml": HEALTHY + "            coverage_min: 150\n"})
    assert any(n.code == "doctor-coverage" and n.severity == "error" for n in doctor.diagnose(bad))
    good = _workflows(tmp_path, {"ci.yml": HEALTHY + "            coverage_min: 30\n"})
    armed = [n for n in doctor.diagnose(good) if n.code == "doctor-coverage"]
    assert armed and armed[0].severity == "info" and "30" in armed[0].message


def test_diff_base_note(tmp_path):
    target = _workflows(tmp_path, {"ci.yml": HEALTHY + "            diff_base: origin/main\n"})
    assert any(n.code == "doctor-new-code" for n in doctor.diagnose(target))


def test_json_output_shape(tmp_path):
    target = _workflows(tmp_path, {"ci.yml": HEALTHY})
    assert doctor.main(["--workflows-dir", str(target), "--json"]) == 0


def test_unparseable_yaml_fails_closed(tmp_path):
    target = _workflows(tmp_path, {"ci.yml": "jobs: [unclosed\n    bad yaml: :"})
    notes = doctor.diagnose(target)
    assert any(n.code == "doctor-internal" for n in notes)
    assert doctor.main(["--workflows-dir", str(target)]) == 2


def test_missing_workflows_dir_fails_closed(tmp_path):
    notes = doctor.diagnose(tmp_path / "nowhere")
    assert any(n.code == "doctor-internal" for n in notes)
    assert doctor.main(["--workflows-dir", str(tmp_path / "nowhere")]) == 2


def test_codeql_job_does_not_trigger_helper_pin_contract(tmp_path):
    codeql = """
name: CodeQL

on:
    push:

permissions:
    contents: read
    security-events: write

jobs:
    codeql:
        uses: WasewaseX/master-ci/.github/workflows/codeql.yml@v2.0.0
"""
    target = _workflows(tmp_path, {"codeql.yml": codeql})
    notes = doctor.diagnose(target)
    assert not [
        n for n in notes if n.code in {"doctor-missing-helper-ref", "doctor-implicit-helper"}
    ]


def test_ci_success_mention_suppresses_the_reminder(tmp_path):
    reminder = HEALTHY + "\n# branch protection: mark ci-success required\n"
    target = _workflows(tmp_path, {"ci.yml": reminder})
    hits = [
        n
        for n in doctor.diagnose(target)
        if n.code == "doctor-required-check" and "no workflow mentions" in n.message
    ]
    assert not hits
    plain = _workflows(tmp_path, {"ci.yml": HEALTHY})
    hits = [
        n
        for n in doctor.diagnose(plain)
        if n.code == "doctor-required-check" and "no workflow mentions" in n.message
    ]
    assert hits


def test_yaml_module_required_for_diagnosis(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("no yaml for you")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    notes = doctor.diagnose(_workflows(tmp_path, {"ci.yml": HEALTHY}))
    assert any(n.code == "doctor-internal" and "PyYAML" in n.message for n in notes)


def test_template_examples_are_doctor_clean_of_errors():
    examples = Path(__file__).resolve().parents[1] / "examples"
    if not (examples / "ci.yml").is_file():
        return
    notes = doctor.diagnose(examples)
    assert not [n for n in notes if n.severity == "error"], [
        n.message for n in notes if n.severity == "error"
    ]
