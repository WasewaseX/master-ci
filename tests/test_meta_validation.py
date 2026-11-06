"""Runtime tests for the meta job's caller-input validation.

These tests extract the live `run:` script from master-ci.yml and execute
it under bash with controlled env vars. That proves the validation logic
actually works - accept/reject behavior for every input class - instead
of only proving that the YAML parses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "master-ci.yml"

VALID = {
    "TEMPLATE_REPO_INPUT": "",
    "TEMPLATE_REF": "v1.4.0",
    "PROFILE": "standard",
    "RUN_NODE": "auto",
    "RUN_PYTHON": "true",
    "RUN_RUST": "false",
    "RUN_GO": "auto",
    "SELECT": "",
}

CASES = [
    ("all defaults valid", {}, True),
    ("valid select tokens", {"SELECT": "secrets,python"}, True),
    (
        "every valid select token",
        {
            "SELECT": "hygiene,secrets,ai-integrity,docs,node-lint,node-test,"
            "python,rust,go-lint,go-test,security,deps-review,scorecard"
        },
        True,
    ),
    ("template_ref pinned tag", {"TEMPLATE_REF": "v1"}, True),
    (
        "template_ref commit sha",
        {"TEMPLATE_REF": "51455387833bf80cb39f0afa0e32f02911b4f1ba"},
        True,
    ),
    ("template_ref traversal", {"TEMPLATE_REF": "../not-a-ref"}, False),
    ("template_ref command injection", {"TEMPLATE_REF": "main;rm -rf /"}, False),
    ("profile typo", {"PROFILE": "Standard"}, False),
    ("profile unknown", {"PROFILE": "strict "}, False),
    ("run_node typo", {"RUN_NODE": "ture"}, False),
    ("run_python empty", {"RUN_PYTHON": ""}, False),
    ("run_go yes", {"RUN_GO": "yes"}, False),
    ("run_rust injection", {"RUN_RUST": "true; curl evil"}, False),
    ("select unknown job", {"SELECT": "secrets,bogus-job"}, False),
    ("select with space", {"SELECT": "secrets, python"}, False),
    ("select traversal", {"SELECT": "../x"}, False),
    ("select injection", {"SELECT": "secrets;curl evil"}, False),
    ("bad template_repo", {"TEMPLATE_REPO_INPUT": "owner/"}, False),
    ("good template_repo", {"TEMPLATE_REPO_INPUT": "WasewaseX/master-ci"}, True),
]


def meta_validate_script() -> str:
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = data["jobs"]["meta"]["steps"]
    step = next(s for s in steps if s.get("name") == "Validate caller inputs")
    return str(step["run"])


def run_script(env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", "-c", meta_validate_script()],
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", **VALID, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_every_validation_case_behaves_as_contracted():
    failures: list[str] = []
    for name, overrides, should_pass in CASES:
        code, output = run_script(overrides)
        passed = code == 0
        if passed != should_pass:
            failures.append(f"{name}: exit={code} output={output.strip()[:120]}")
    assert not failures, "validation contract violations:\n" + "\n".join(failures)


def test_validation_script_is_self_contained():
    """The script must not depend on caller-provided state beyond inputs."""
    script = meta_validate_script()
    assert "die()" in script, "validation must fail loudly via ::error"
    assert script.count("${{") == 0, "no expression interpolation inside run"
