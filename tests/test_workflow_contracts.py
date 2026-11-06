"""Contract tests for the Master CI workflows.

Mechanically enforces the supply-chain and reliability posture the
project documents, so a regression cannot land silently:

- every external action pinned to a full 40-hex commit SHA with a version
    comment (and reusable-workflow refs only via version tags)
- no `secrets: inherit`, anywhere
- no `${{ }}` expression interpolation inside `run:` blocks (template
    injection); inputs reach scripts only through env vars
- every job declares `timeout-minutes`
- reusable workflows keep top-level `permissions: contents: read` only,
    elevated scopes appear on the jobs that need them, `id-token: write`
    only on the scorecard job
- the AI detector stays advisory by default (`fail_on_ai_slop: false`)
- helper configs referenced by workflows exist in this repository
- automation safety: autofix only opens PRs, stale never closes
    security-labeled issues, meta validates untrusted inputs
- the selftest workflow really exercises pass, enforced-failure and
    input-validation paths of the reusable workflow
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(list(WF_DIR.glob("*.yml")) + list(WF_DIR.glob("*.yaml")))
SHA_RE = re.compile(r"^(?P<action>[\w./-]+)@(?P<ref>[0-9a-f]{40})(?:\s*#\s*(?P<comment>\S.*))?$")
REUSABLE_TAG_RE = re.compile(r"^[\w.-]+/[\w.-]+/\.github/workflows/[\w.-]+@v[\d.]+$")

_ALLOWED_PERMISSIONS = {"contents", "security-events", "pull-requests", "issues", "id-token"}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def iter_steps(jobs: dict):
    for job_id, job in jobs.items():
        for step in job.get("steps", []) or []:
            yield job_id, step


def uses_lines(workflow: Path) -> list[str]:
    data = load(workflow)
    lines = [str(data.get("jobs", {}).get(j, {}).get("uses", "")) for j in data.get("jobs", {})]
    for _, step in iter_steps(data.get("jobs", {})):
        if "uses" in step:
            lines.append(str(step["uses"]))
    return [line for line in lines if line and line != "None"]


def job_permissions(workflow: Path) -> dict[str, dict]:
    data = load(workflow)
    return {job_id: job.get("permissions") or {} for job_id, job in data.get("jobs", {}).items()}


# ---------------------------------------------------------------------------
# Actions and secrets
# ---------------------------------------------------------------------------


def test_every_workflow_file_exists_and_is_named():
    assert len(WORKFLOWS) >= 5
    for wf in WORKFLOWS:
        data = load(wf)
        assert data.get("name"), f"{wf.name}: missing workflow name"


def test_external_actions_are_sha_pinned():
    for wf in WORKFLOWS:
        for line in uses_lines(wf):
            if line.startswith("./"):
                continue
            if "/.github/workflows/" in line:
                assert REUSABLE_TAG_RE.match(line), (
                    f"{wf.name}: reusable workflow ref must use a version tag: {line}"
                )
                continue
            match = SHA_RE.match(line)
            assert match, f"{wf.name}: action not SHA-pinned with version comment: {line}"


def _find_inherit_secrets(data) -> list[str]:
    """Locate `secrets: inherit` structurally in a parsed workflow."""
    found: list[str] = []
    for job_id, job in (data.get("jobs") or {}).items():
        secrets = job.get("secrets")
        if secrets == "inherit":
            found.append(str(job_id))
        elif isinstance(secrets, dict):
            if any(value == "inherit" for value in secrets.values()):
                found.append(str(job_id))
    return found


def test_no_secrets_inherit_anywhere():
    offenders = []
    for path in list(WF_DIR.glob("*.yml")) + [ROOT / "examples" / "ci.yml"]:
        inherit = _find_inherit_secrets(load(path))
        if inherit:
            offenders.append(f"{path.name}: {inherit}")
    assert not offenders, f"secrets: inherit found in: {offenders}"
    # the README documents the prohibition - it must not recommend inherit
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "secrets: inherit" in readme  # documents the absence
    assert "secrets: inherit\n" + "            secrets:" not in readme


def test_no_expression_interpolation_in_run_blocks():
    offenders = []
    for wf in WORKFLOWS:
        data = load(wf)
        for job_id, step in iter_steps(data.get("jobs", {})):
            run = step.get("run")
            if run and "${{" in str(run):
                offenders.append(f"{wf.name}:{job_id}:{step.get('name', '?')}")
    assert not offenders, f"template injection risk in run blocks: {offenders}"


def test_every_job_has_timeout():
    for wf in WORKFLOWS:
        for job_id, job in load(wf).get("jobs", {}).items():
            if job.get("uses"):  # reusable workflow call
                continue
            timeout = job.get("timeout-minutes")
            assert isinstance(timeout, int) and 0 < timeout <= 120, (
                f"{wf.name}:{job_id}: missing or odd timeout-minutes"
            )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_reusable_workflows_are_least_privilege_at_top_level():
    for wf in WF_DIR.glob("*.yml"):
        data = load(wf)
        trigger = data.get(True) or data.get("on") or {}
        if "workflow_call" not in trigger:
            continue
        perms = data.get("permissions") or {}
        assert perms == {"contents": "read"}, (
            f"{wf.name}: top-level permissions must be exactly contents: read, got {perms}"
        )


def test_permission_scopes_are_known():
    for wf in WORKFLOWS:
        data = load(wf)
        all_perms = {"top": data.get("permissions") or {}}
        all_perms.update(job_permissions(wf))
        for scope_map in all_perms.values():
            if scope_map == "read-all" or scope_map == {}:
                continue
            assert set(scope_map) <= _ALLOWED_PERMISSIONS, (
                f"{wf.name}: unknown permission scope(s): {set(scope_map) - _ALLOWED_PERMISSIONS}"
            )


def test_id_token_only_on_scorecard():
    for job_id, perms in job_permissions(WF_DIR / "master-ci.yml").items():
        if perms.get("id-token") == "write":
            assert job_id == "scorecard", (
                f"master-ci.yml:{job_id}: id-token: write is reserved for the scorecard job"
            )


def test_security_events_only_where_sarif_is_uploaded():
    expected = {
        "master-ci.yml": {"security", "scorecard", "ai-integrity"},
        "codeql.yml": {"analyze"},
        "nightly.yml": {"codeql"},
    }
    for wf_name, allowed in expected.items():
        for job_id, perms in job_permissions(WF_DIR / wf_name).items():
            if perms.get("security-events") == "write":
                assert job_id in allowed, f"{wf_name}:{job_id}: unexpected security-events: write"


# ---------------------------------------------------------------------------
# Detector and aggregate gate
# ---------------------------------------------------------------------------


def test_ai_detector_is_advisory_by_default():
    data = load(WF_DIR / "master-ci.yml")
    trigger = data[True] if True in data else data["on"]
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["fail_on_ai_slop"]["default"] is False
    assert inputs["fail_on_ai_slop"]["type"] == "boolean"


def test_ci_success_aggregates_every_job():
    data = load(WF_DIR / "master-ci.yml")
    jobs = data["jobs"]
    agg = set(jobs["ci-success"]["needs"])
    dash = set(jobs["dashboard"]["needs"])
    assert agg == dash, "dashboard and ci-success must watch the same jobs"
    assert agg >= {"changes", "meta", "hygiene", "secrets", "security", "ci-success"} - {
        "ci-success"
    }
    assert "ci-success" not in agg


def test_concurrency_cancels_superseded_pr_runs():
    data = load(WF_DIR / "master-ci.yml")
    concurrency = data.get("concurrency") or {}
    assert concurrency.get("group"), "master-ci.yml needs a concurrency group"
    assert "cancel-in-progress" in str(concurrency)


# ---------------------------------------------------------------------------
# Helper contracts
# ---------------------------------------------------------------------------


def test_helper_configs_referenced_by_workflows_exist():
    refs = set()
    for wf in WORKFLOWS:
        refs.update(re.findall(r"_ci/configs/(\S+)", wf.read_text(encoding="utf-8")))
    assert refs, "expected at least one helper config reference"
    for name in refs:
        assert (ROOT / "configs" / name).is_file(), f"missing helper config: configs/{name}"


def test_automation_autofix_opens_prs_never_pushes():
    data = load(WF_DIR / "autofix.yml")
    text = (WF_DIR / "autofix.yml").read_text(encoding="utf-8")
    assert "git push" not in text
    job = data["jobs"]["autofix"]
    steps = [str(s.get("uses", "")) for s in job["steps"]]
    assert any("create-pull-request" in u for u in steps), "autofix must go through PRs"
    assert all(not u.startswith("git") for u in steps)


def test_stale_never_closes_security_issues():
    text = (WF_DIR / "stale.yml").read_text(encoding="utf-8")
    match = re.search(r"exempt-issue-labels:\s*([^\n]+)", text)
    assert match, "stale.yml needs exempt-issue-labels"
    labels = {label.strip() for label in match.group(1).split(",")}
    assert "security" in labels, "security-labeled issues must be exempt from closing"
    match = re.search(r"exempt-pr-labels:\s*([^\n]+)", text)
    assert match and "security" in {label.strip() for label in match.group(1).split(",")}


def test_meta_validates_untrusted_inputs():
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "Validate caller inputs" in meta, "meta must validate template_repo/template_ref/profile"
    for needle in ("template_repo", "template_ref", "PROFILE"):
        assert needle in meta


def test_master_ci_jobs_gate_on_meta_success():
    """Jobs that fetch helpers must not run when input validation failed."""
    data = load(WF_DIR / "master-ci.yml")
    for job_id in ("hygiene", "security", "ai-integrity", "secrets"):
        condition = str(data["jobs"][job_id].get("if", ""))
        assert "needs.meta.result == 'success'" in condition, (
            f"{job_id} must skip when meta validation failed (untrusted template_ref)"
        )


# ---------------------------------------------------------------------------
# Selftest wiring
# ---------------------------------------------------------------------------


def test_selftest_exercises_pass_failure_and_validation_paths():
    data = load(WF_DIR / "selftest.yml")
    jobs = data["jobs"]
    for job_id in ("happy-path", "enforced-failure", "input-validation", "verify"):
        assert job_id in jobs, f"selftest missing job {job_id}"
    assert jobs["happy-path"]["uses"].startswith("./")
    # expected-failure calls are gated behind the explicit dispatch mode
    for job_id in ("enforced-failure", "input-validation"):
        condition = str(jobs[job_id].get("if", ""))
        assert "inputs.failure_paths == 'true'" in condition, (
            f"{job_id} must only run in the explicit failure-path mode"
        )
    assert "happy-path" in jobs["verify"]["needs"]
    assert "enforced-failure" in jobs["verify"]["needs"]
    assert "input-validation" in jobs["verify"]["needs"]
    verify_text = str(jobs["verify"]["steps"][0].get("run", ""))
    assert '"failed"' in verify_text and '"passed"' in verify_text, (
        "verify must assert verdict outputs for both the pass and fail paths"
    )


def test_master_ci_exposes_verdict_output():
    data = load(WF_DIR / "master-ci.yml")
    trigger = data[True] if True in data else data["on"]
    call = trigger["workflow_call"]
    assert "verdict" in call.get("outputs", {}), (
        "master-ci.yml must expose a verdict output for selftest assertions"
    )
    assert "verdict" in data["jobs"]["ci-success"].get("outputs", {})


def test_master_ci_supports_distinct_concurrency_keys():
    data = load(WF_DIR / "master-ci.yml")
    group = str((data.get("concurrency") or {}).get("group", ""))
    assert "concurrency_key" in group, (
        "callers running master-ci multiple times need distinct concurrency keys"
    )


# ---------------------------------------------------------------------------
# Self-heal loop (heal.yml)
# ---------------------------------------------------------------------------


def test_heal_loop_is_pr_only_and_gated_on_default_branch_failures():
    data = load(WF_DIR / "heal.yml")
    text = (WF_DIR / "heal.yml").read_text(encoding="utf-8")
    # PRs are the whole change; the workflow never pushes
    assert "git push" not in text, "self-heal must never push to a branch"
    steps = [str(s.get("uses", "")) for s in data["jobs"]["heal"]["steps"]]
    assert any("create-pull-request" in u for u in steps), "self-heal must go through PRs"
    # it only reacts to failures on the default branch (never to its own PRs)
    condition = str(data["jobs"]["heal"].get("if", ""))
    assert "conclusion == 'failure'" in condition
    assert "default_branch" in condition, "heal must only react to default-branch failures"
    # circuit breaker: stop when the fix PR is already open
    assert "ci/autofix" in text, "an open fix PR must stop the loop"
    # serialized: one heal at a time
    assert (data.get("concurrency") or {}).get("group") == "self-heal"


def test_heal_and_autofix_share_the_fix_engine():
    """Both loops delegate to the same fix policy: scripts/fix_engine.py.

    Parity is structural now - one engine, one tool registry - instead of
    duplicated inline fixer steps that could drift apart.
    """
    for wf_name in ("autofix.yml", "heal.yml"):
        text = (WF_DIR / wf_name).read_text(encoding="utf-8")
        assert "scripts/fix_engine.py" in text, f"{wf_name}: missing fix engine call"
        assert "--tier safe --tier lint" in text, f"{wf_name}: must apply both tiers"
        assert "peter-evans/create-pull-request@" in text, (
            f"{wf_name}: fixes must land through a PR"
        )


# ---------------------------------------------------------------------------
# Deep audit (audit.yml)
# ---------------------------------------------------------------------------


def test_deep_audit_runs_on_a_clock_never_on_push():
    data = load(WF_DIR / "audit.yml")
    trigger = data[True] if True in data else data["on"]
    assert set(trigger) == {"schedule", "workflow_dispatch"}, (
        "the deep audit is a scheduled/manual sweep, not a push or PR gate"
    )
    assert set(trigger["schedule"][0]["cron"].split()) <= {"*", "/"} | set("0123456789")


def test_deep_audit_never_gates_ci_and_never_fails_on_findings():
    data = load(WF_DIR / "audit.yml")
    perms = data.get("permissions") or {}
    assert perms.get("contents") == "read"
    assert perms.get("issues") == "write"
    assert "security-events" not in perms, "audit reports to issues, not the Security tab"
    steps = data["jobs"]["audit"]["steps"]
    scan_steps = [s for s in steps if s.get("continue-on-error") is True]
    assert len(scan_steps) >= 4, "scan steps must tolerate findings without failing the run"
