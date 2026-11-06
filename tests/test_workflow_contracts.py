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
- release integrity: the dependency-review probe is authenticated, Node
    installs never fall back past a lockfile, stack toggles and select
    tokens are validated, Python project installs fail loudly
- the selftest workflow really exercises pass, enforced-failure and
    input-validation paths of the reusable workflow
- event filters are explicit, clean pattern lists and the self-loop
    workflows push-target exactly [main] (trigger regression net)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(list(WF_DIR.glob("*.yml")) + list(WF_DIR.glob("*.yaml")))
SHA_RE = re.compile(r"^(?P<action>[\w./-]+)@(?P<ref>[0-9a-f]{40})(?:\s*#\s*(?P<comment>\S.*))?$")
REUSABLE_TAG_RE = re.compile(r"^[\w.-]+/[\w.-]+/\.github/workflows/[\w.-]+@v[\d.]+$")

_ALLOWED_PERMISSIONS = {
    "contents",
    "security-events",
    "pull-requests",
    "issues",
    "id-token",
    "actions",  # the expected-failure harness dispatches selftest and reads its runs
}


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


# ---------------------------------------------------------------------------
# Release integrity (dependency-review auth, helper pinning, lockfiles,
# input validation, install failure handling) - regression-proofed after
# an external review found these gaps.
# ---------------------------------------------------------------------------


def test_dependency_review_probe_is_authenticated():
    """The dependency-graph probe must carry the automatic token.

    Without the env mapping the probe runs unauthenticated, gets HTTP 401
    on private repositories, and dependency review silently never runs -
    a security gate that is dead code.
    """
    data = load(WF_DIR / "master-ci.yml")
    probe = next(s for s in data["jobs"]["deps-review"]["steps"] if s.get("id") == "dg")
    env = probe.get("env") or {}
    assert env.get("GITHUB_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
        "dependency-graph probe must map secrets.GITHUB_TOKEN into its env"
    )


def test_node_installs_never_fall_back_past_a_lockfile():
    """A lockfile is a contract: no silent `||` fallback to mutable installs."""
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    for forbidden in (
        "pnpm install --frozen-lockfile ||",
        "yarn install --immutable ||",
        "npm ci || npm install",
    ):
        assert forbidden not in text, f"silent lockfile fallback forbidden: {forbidden}"
    # frozen installs exist, gated explicitly on lockfile presence, in both
    # Node jobs (node-lint and node-test)
    assert text.count("[ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile") == 2
    assert text.count("[ -f yarn.lock ]; then yarn install --immutable") == 2
    assert text.count("[ -f package-lock.json ]; then npm ci") == 2


def test_meta_validates_stack_toggles_and_select():
    """Invalid run_* toggles or select tokens must fail the run, not skip."""
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    for needle in ("RUN_NODE", "RUN_PYTHON", "RUN_RUST", "RUN_GO", "SELECT"):
        assert needle in meta, f"meta must validate {needle}"
    assert "auto|true|false) ;;" in meta, "stack toggles must be auto/true/false"
    # select tokens are checked against the full known job list
    for job in (
        "hygiene",
        "secrets",
        "ai-integrity",
        "docs",
        "node-lint",
        "node-test",
        "python",
        "rust",
        "go-lint",
        "go-test",
        "security",
        "deps-review",
        "scorecard",
    ):
        assert job in meta, f"select allowlist must contain {job}"


def test_python_project_install_failure_fails_the_job():
    """A packaging failure must never degrade to testing a bare environment."""
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    assert "pip install -e . || pip install . || true" not in text
    assert "pip install -e . || pip install ." in text


def test_mutable_template_ref_warns_loudly():
    """Helpers fetched from a moving branch must announce themselves."""
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "::warning::template_ref=" in meta
    assert "main|master" in meta, "warning must cover the mutable branch defaults"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "template_ref" in readme and "reproducible" in readme, (
        "README must document helper pinning"
    )


def test_meta_verifies_helper_presence_before_fallback():
    """GITHUB_WORKFLOW_REF points at the caller, so a foreign consumer's
    auto-detected template_repo is their own project. Meta must probe for
    the helper module and fall back loudly (or die with instructions)
    instead of letting every job fail on a cryptic clone of the caller."""
    text = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "raw.githubusercontent.com" in meta, "meta must probe the helper file"
    assert "scripts/ai_slop_detector.py" in meta, "probe must target a helper module"
    assert "::warning::template_repo auto-detected" in meta, (
        "the foreign-caller fallback must announce itself"
    )
    assert "::error::no master-ci helpers found" in meta, (
        "an unresolvable setup must fail meta with instructions, not a red fetch"
    )


def test_scorecard_publish_false_skips_the_job():
    """scorecard_publish: false must skip the scorecard job entirely so
    callers can drop id-token: write, as the README promises. Without the
    gate the job still runs on pushes and startup-fails every caller that
    took the documented path."""
    data = load(WF_DIR / "master-ci.yml")
    condition = str(data["jobs"]["scorecard"].get("if", ""))
    assert "inputs.scorecard_publish" in condition, (
        "scorecard job must be gated on inputs.scorecard_publish"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scorecard_publish: false" in readme and "id-token" in readme, (
        "README must keep documenting the id-token opt-out"
    )


def test_default_biome_policy_excludes_the_helper_checkout():
    """The shipped default biome.jsonc sits next to a _ci/ checkout of the
    template itself. Biome treats every discovered config as a root, so a
    nested _ci/configs/biome.jsonc aborts the scan outright. The helper
    checkout is template infrastructure, not consumer code - it must stay
    out of the policy's file set."""
    config = json.loads(
        re.sub(r"//[^\n]*", "", (ROOT / "configs" / "biome.jsonc").read_text(encoding="utf-8"))
    )
    includes = config["files"]["includes"]
    assert any("_ci" in pattern for pattern in includes), (
        "configs/biome.jsonc must exclude the _ci helper checkout"
    )
    node_lint = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")
    assert "[ -f biome.jsonc ] || cp _ci/configs/biome.jsonc ." in node_lint, (
        "node-lint must keep offering the overridable default"
    )


def test_check_jobs_can_be_selected_individually():
    """Every check job carries a select clause so callers can run subsets."""
    data = load(WF_DIR / "master-ci.yml")
    trigger = data[True] if True in data else data["on"]
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["select"]["default"] == "", "select must default to all jobs"
    gated = [
        "hygiene",
        "secrets",
        "ai-integrity",
        "docs",
        "node-lint",
        "node-test",
        "python",
        "rust",
        "go-lint",
        "go-test",
        "security",
        "deps-review",
        "scorecard",
    ]
    for job_id in gated:
        condition = str(data["jobs"][job_id].get("if", ""))
        assert "inputs.select" in condition, f"{job_id} must honor the select input"
    # infrastructure jobs must never be skippable via select
    for job_id in ("meta", "changes", "dashboard", "ci-success"):
        condition = str(data["jobs"][job_id].get("if", ""))
        assert "inputs.select" not in condition, f"{job_id} is infrastructure and must always run"


def test_readme_documents_current_inputs():
    """Documentation must describe the actual implementation, not lag it."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("run_go", "go_version", "`select`", "template_ref"):
        assert needle in readme, f"README config table must document {needle}"
    assert "Rust, Node/TypeScript, Python and Go" in readme, (
        "README must list Go as a full CI stack"
    )


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
    for job_id in (
        "happy-path",
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "select-subset",
        "verify",
    ):
        assert job_id in jobs, f"selftest missing job {job_id}"
    assert jobs["happy-path"]["uses"].startswith("./")
    # expected-failure calls are gated behind the explicit dispatch mode
    for job_id in (
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
    ):
        condition = str(jobs[job_id].get("if", ""))
        assert "inputs.failure_paths == 'true'" in condition, (
            f"{job_id} must only run in the explicit failure-path mode"
        )
    # select-subset proves job selection in both modes and must never be gated
    assert "if" not in jobs["select-subset"], (
        "select-subset must run in both modes so selection is always exercised"
    )
    assert "select" in str(jobs["select-subset"].get("with", {}))
    for wanted in (
        "happy-path",
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "select-subset",
    ):
        assert wanted in jobs["verify"]["needs"], f"verify must watch {wanted}"
    verify_text = str(jobs["verify"]["steps"][0].get("run", ""))
    assert '"failed"' in verify_text and '"passed"' in verify_text, (
        "verify must assert verdict outputs for both the pass and fail paths"
    )
    assert "TOGGLE_RESULT" in verify_text and "BADSELECT_RESULT" in verify_text, (
        "verify must assert the new validation failure paths"
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


# ---------------------------------------------------------------------------
# Benchmark (benchmark.yml)
# ---------------------------------------------------------------------------


def test_benchmark_is_scheduled_report_only_and_uploads_results():
    data = load(WF_DIR / "benchmark.yml")
    trigger = data[True] if True in data else data["on"]
    assert set(trigger) == {"schedule", "workflow_dispatch"}, (
        "benchmark is a scheduled/manual report, not a push or PR gate"
    )
    assert (data.get("permissions") or {}).get("contents") == "write", (
        "benchmark needs contents: write to commit benchmarks/history.jsonl"
    )
    assert "concurrency" in data, "history appends must be serialized"
    job = data["jobs"]["bench"]
    run_blocks = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "scripts/benchmark.py" in run_blocks
    assert "--json" in run_blocks, "results must be written as JSON for the artifact"
    assert "--append benchmarks/history.jsonl" in run_blocks, (
        "every hosted run must append a line to the committed history file"
    )
    assert "git push origin benchmarks" in run_blocks, (
        "history must be committed to the dedicated benchmarks branch"
    )
    assert "HEAD:main" not in run_blocks and "push origin main" not in run_blocks, (
        "main is protected and automation never pushes it"
    )
    assert "git diff --cached --quiet" in run_blocks, "no empty history commits"
    steps = [str(s.get("uses", "")) for s in job["steps"]]
    assert any("upload-artifact" in u for u in steps), "results must be uploaded"
    assert "$GITHUB_STEP_SUMMARY" in run_blocks, "results must reach the step summary"


def test_benchmark_history_lines_parse_when_present():
    """Every committed history line is a valid record with the required keys."""
    history = ROOT / "benchmarks" / "history.jsonl"
    if not history.exists():
        return  # first hosted run creates it
    for i, line in enumerate(history.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("timestamp", "scale", "metrics"):
            assert key in record, f"history line {i} missing {key}"
        assert isinstance(record["metrics"], dict) and record["metrics"], (
            f"history line {i} has empty metrics"
        )


# ---------------------------------------------------------------------------
# Expected-failure harness (selftest-negative.yml)
# ---------------------------------------------------------------------------


def test_expected_failure_harness_runs_the_negative_paths_routinely():
    data = load(WF_DIR / "selftest-negative.yml")
    trigger = data[True] if True in data else data["on"]
    assert {"push", "schedule", "workflow_dispatch"} <= set(trigger), (
        "negative paths must run routinely, not only on manual dispatch"
    )
    assert (data.get("permissions") or {}).get("actions") == "write"
    job = data["jobs"]["negative-harness"]
    script = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "failure_paths=true" in script, "harness must dispatch the negative selftest"
    for job_name in (
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
    ):
        assert f'"{job_name}"' in script, f"harness must assert {job_name} failed"
    for job_name in ("happy-path", "select-subset", "verify"):
        assert f'"{job_name}"' in script, f"harness must assert {job_name} succeeded"
    assert "conclusion" in script and '"failure"' in script, (
        "harness must treat a red child run as the expected outcome"
    )
    assert "--paginate" in script, (
        "the child run has 100+ jobs - the jobs endpoint must be paginated"
    )


def test_selftest_negative_jobs_stay_gated_for_humans_only_when_direct():
    """The manual gate remains in selftest.yml; the harness is the routine caller."""
    text = (WF_DIR / "selftest.yml").read_text(encoding="utf-8")
    for job in ("enforced-failure", "input-validation-stack-toggle", "input-validation-select"):
        assert job in text
        assert "failure_paths == 'true'" in text


# ---------------------------------------------------------------------------
# Release integrity (one stable baseline across code and docs)
# ---------------------------------------------------------------------------


def _load_mcp_server():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mcp_server_contracts", ROOT / "scripts" / "mcp_server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_server_version_is_semver():
    mcp = _load_mcp_server()
    assert re.fullmatch(r"\d+\.\d+\.\d+", mcp.SERVER_VERSION), (
        f"SERVER_VERSION {mcp.SERVER_VERSION!r} must be X.Y.Z"
    )


def test_skill_and_readme_pin_the_release_baseline():
    """One version everywhere: MCP server, SKILL.md, README, docs/mcp.md."""
    mcp = _load_mcp_server()
    baseline = f"v{mcp.SERVER_VERSION}"
    for doc in ("SKILL.md", "README.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert f"--branch {baseline}" in text, (
            f"{doc} must pin the skill install to the release baseline {baseline}"
        )
        stale = re.findall(r"--branch (v\d+\.\d+\.\d+)", text)
        assert set(stale) == {baseline}, f"{doc} has mixed version pins: {stale}"


def test_modern_mcp_protocol_is_supported():
    """The 2026-07-28 stateless era must stay supported alongside the legacy eras."""
    mcp = _load_mcp_server()
    assert "2026-07-28" in mcp.PROTOCOL_VERSIONS
    assert mcp.MODERN_PROTOCOL_VERSION == "2026-07-28"
    assert mcp.DEFAULT_PROTOCOL_VERSION in mcp.PROTOCOL_VERSIONS
    assert mcp.UNSUPPORTED_PROTOCOL_VERSION_ERROR == -32022
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "2026-07-28" in skill, "SKILL.md must document modern protocol support"


def test_apply_fixes_write_lock_is_enforced_in_source():
    """The mutating tool must require MASTER_CI_MCP_ROOT - not just document it."""
    text = (ROOT / "scripts" / "mcp_server.py").read_text(encoding="utf-8")
    apply_body = text.split("def tool_apply_fixes", 1)[1].split("\ndef ", 1)[0]
    assert "_require_write_root" in apply_body, (
        "apply_fixes must call the mandatory write-root guard first"
    )


def test_scan_diff_discovers_untracked_files():
    """Untracked discovery is the whole point of the change-set scan."""
    text = (ROOT / "scripts" / "mcp_server.py").read_text(encoding="utf-8")
    changed_body = text.split("def _changed_files", 1)[1].split("\ndef ", 1)[0]
    assert "ls-files" in changed_body and "--exclude-standard" in changed_body, (
        "_changed_files must run git ls-files --others --exclude-standard"
    )


# ---------------------------------------------------------------------------
# Trigger filters (explicit, auditable event configuration)
# ---------------------------------------------------------------------------


def test_event_filters_are_clean_pattern_lists():
    """Every branches/tags/paths filter must be a list of clean strings.

    A review pass misread a trigger filter and sent this repository
    chasing a `branches: ain]` corruption that was never there: the
    filter was always a proper [main] list (codepoint-verified).
    A wrong reading is exactly how trigger regressions slip through
    human review, though - the wrong thing is valid YAML and invisible
    to yamllint and actionlint - so the expectation is now mechanical:
    filters are lists, patterns carry no stray brackets, and the
    self-loop workflows push-target exactly the default branch.
    """
    filter_keys = (
        "branches",
        "branches-ignore",
        "tags",
        "tags-ignore",
        "paths",
        "paths-ignore",
    )
    for wf in WORKFLOWS:
        data = load(wf)
        trigger = data[True] if True in data else data.get("on") or {}
        if not isinstance(trigger, dict):
            continue
        for event, spec in trigger.items():
            if not isinstance(spec, dict):
                continue
            for key in filter_keys:
                value = spec.get(key)
                if value is None:
                    continue
                assert isinstance(value, list) and all(
                    isinstance(p, str) for p in value
                ), f"{wf.name}: {event}.{key} must be a list of strings, got {value!r}"
                for pattern in value:
                    assert "[" not in pattern and "]" not in pattern, (
                        f"{wf.name}: {event}.{key} pattern {pattern!r} contains a stray "
                        "bracket - this repository does not use character-class globs"
                    )


def test_self_loop_workflows_push_target_main():
    """The self-loop workflows must explicitly push-target [main] and nothing else."""
    for name in ("ci.yml", "selftest.yml", "selftest-negative.yml"):
        data = load(WF_DIR / name)
        trigger = data[True] if True in data else data["on"]
        assert trigger["push"]["branches"] == ["main"], (
            f"{name}: push.branches must be exactly ['main'], got {trigger['push']['branches']!r}"
        )
