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
- `# zizmor:` suppression comments only appear where zizmor can parse
    them as YAML comments (standalone line or trailing a key line), never
    buried inside `run: |` block scalars
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

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
# The reusable workflow source, read once for the whole module: ten
# tests assert on it and the boilerplate detector is right - one
# helper, not ten copies of the same line.
MASTER_CI_TEXT = (WF_DIR / "master-ci.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def master_ci_text() -> str:
    """The reusable workflow source, shared module-wide."""
    return MASTER_CI_TEXT


def _on(data: dict) -> dict:
    """The `on:` mapping - PyYAML 1.1 parses the key as boolean True."""
    return data[True] if True in data else data["on"]


WORKFLOWS = sorted(list(WF_DIR.glob("*.yml")) + list(WF_DIR.glob("*.yaml")))
SHA_RE = re.compile(r"^(?P<action>[\w./-]+)@(?P<ref>[0-9a-f]{40})(?:\s*#\s*(?P<comment>\S.*))?$")
REUSABLE_TAG_RE = re.compile(r"^[\w.-]+/[\w.-]+/\.github/workflows/[\w.-]+@v[\d.]+$")

_ALLOWED_PERMISSIONS = {
    "contents",
    "security-events",
    "pull-requests",
    "issues",
    "id-token",
    "packages",  # release-docker.yml pushes images to GHCR
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
    jobs = data.get("jobs") or {}
    lines = [str((jobs.get(j) or {}).get("uses") or "") for j in jobs]
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
        "master-ci.yml": {"security", "scorecard", "ai-integrity", "docker"},
        "release-docker.yml": {"publish"},
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
    trigger = _on(data)
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
    text = MASTER_CI_TEXT
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "Validate caller inputs" in meta, "meta must validate template_repo/template_ref/profile"
    for needle in ("template_repo", "template_ref", "PROFILE"):
        assert needle in meta


# Release integrity (dependency-review auth, helper pinning, lockfiles,
# input validation, install failure handling), regression-proofed.


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
    text = MASTER_CI_TEXT
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
    text = MASTER_CI_TEXT
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    for needle in (
        "RUN_NODE",
        "RUN_PYTHON",
        "RUN_RUST",
        "RUN_GO",
        "RUN_JAVA",
        "RUN_CPP",
        "RUN_CSHARP",
        "RUN_RUBY",
        "RUN_PHP",
        "RUN_DOCKER",
        "SELECT",
    ):
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
        "java",
        "cpp",
        "csharp",
        "ruby",
        "php",
        "docker",
        "security",
        "deps-review",
        "scorecard",
    ):
        assert job in meta, f"select allowlist must contain {job}"


def test_python_project_install_failure_fails_the_job():
    """A packaging failure must never degrade to testing a bare environment."""
    text = MASTER_CI_TEXT
    assert "pip install -e . || pip install . || true" not in text
    assert "pip install -e . || pip install ." in text


def test_mutable_template_ref_warns_loudly():
    """Helpers fetched from a moving branch must announce themselves."""
    text = MASTER_CI_TEXT
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "::warning::template_ref=" in meta
    assert "main|master" in meta, "warning must cover the mutable branch defaults"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "template_ref" in readme and "reproducible" in readme, (
        "README must document helper pinning"
    )


def test_template_ref_fails_closed_for_foreign_callers():
    """Empty template_ref must resolve only when helpers live in the caller.

    github.workflow_sha is the CALLER's commit even for cross-repo calls -
    the canary proved it by fetching "not our ref" for its own HEAD
    against the template repo (canary run 42283700844, master-ci v1.8.5).
    So the resolution chain is: an explicit input wins; empty falls back
    to the caller's own commit only when the helper repo IS the caller
    (self/fork, probed for the helper module at that exact SHA); and
    everything else fails meta with pin instructions instead of silently
    fetching a mutable branch.
    """
    text = MASTER_CI_TEXT
    assert "default: main" not in text, "template_ref must not default to a mutable branch"
    block = text.split("template_ref:")[1].split("concurrency_key:")[0]
    assert "default: ''" in block, "template_ref input must default to the empty string"
    meta = text.split("meta:")[1].split("\n  changes:")[0]
    assert "WORKFLOW_SHA: ${{ github.workflow_sha }}" in meta, "meta must read the caller's SHA"
    assert 'caller_slug="${caller_ref%%/.github/*}"' in meta, "meta must parse the caller slug"
    assert "raw.githubusercontent.com/$caller_slug/${WORKFLOW_SHA}/$helper" in meta, (
        "the self/fork default must be probed at the caller's exact commit"
    )
    assert '[ "$TEMPLATE_REPO_INPUT" = "$caller_slug" ]' in meta, (
        "the SHA default must not fire for an explicit foreign helper repo"
    )
    assert "::error::template_ref is empty" in meta, "foreign callers must fail closed"
    assert "template_ref: ${{ steps.repo.outputs.template_ref }}" in meta, (
        "meta must expose the resolved ref as a job output"
    )
    assert "ref: ${{ inputs.template_ref }}" not in text, (
        "helper checkouts must consume the resolved ref, not the raw input"
    )
    helper_checkouts = text.count("ref: ${{ needs.meta.outputs.template_ref }}")
    assert helper_checkouts == 15, (
        f"all {helper_checkouts} helper checkouts must use the resolved ref "
        "(bump the count when a job gains one - every checkout consumes "
        "the resolved ref, never the raw input)"
    )


def test_meta_verifies_helper_presence_before_fallback():
    """GITHUB_WORKFLOW_REF points at the caller, so a foreign consumer's
    auto-detected template_repo is their own project. Meta must probe for
    the helper module and fall back loudly (or die with instructions)
    instead of letting every job fail on a cryptic clone of the caller."""
    text = MASTER_CI_TEXT
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
    trigger = _on(data)
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
        "java",
        "cpp",
        "csharp",
        "ruby",
        "php",
        "docker",
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
    for needle in (
        "run_go",
        "run_java",
        "run_cpp",
        "run_csharp",
        "run_ruby",
        "run_php",
        "run_docker",
        "go_version",
        "java_version",
        "dotnet_version",
        "ruby_version",
        "php_version",
        "`select`",
        "template_ref",
    ):
        assert needle in readme, f"README config table must document {needle}"
    assert (
        "Rust, Node/TypeScript, Python, Go, JVM (Java/Kotlin), C/C++, "
        ".NET (C#/F#), Ruby, PHP and Docker"
    ) in readme, "README must list every full CI stack"


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


def test_selftest_stays_green_and_exercises_the_pass_paths():
    """selftest.yml is the routine integration test and must be permanently green.

    Red in CI may only ever mean broken. The five designed-failure calls
    cannot carry continue-on-error (GitHub gives reusable-workflow caller
    jobs no such key), so they must NOT live here - a red dispatch would be
    indistinguishable from a real defect. They live in
    selftest-guardrails.yml, isolated behind the watchdog.
    """
    data = load(WF_DIR / "selftest.yml")
    jobs = data["jobs"]
    for job_id in ("happy-path", "select-subset", "verify"):
        assert job_id in jobs, f"selftest missing job {job_id}"
    assert jobs["happy-path"]["uses"].startswith("./")
    # no designed-failure callers in the green workflow, in any shape
    designed = {
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "input-validation-template-ref",
    }
    assert not designed & set(jobs), (
        "selftest.yml must stay green - designed-failure callers belong in selftest-guardrails.yml"
    )
    # no failure_paths flag: a manual dispatch must behave exactly like a push
    assert "failure_paths" not in (WF_DIR / "selftest.yml").read_text(encoding="utf-8"), (
        "selftest.yml must not carry a failure_paths flag - it has only one, green mode"
    )
    # select-subset proves job selection on every run and must never be gated
    assert "if" not in jobs["select-subset"], (
        "select-subset must run on every selftest run so selection is always exercised"
    )
    assert "select" in str(jobs["select-subset"].get("with", {}))
    assert set(jobs["verify"]["needs"]) == {"happy-path", "select-subset"}, (
        "verify must watch exactly the two green calls"
    )
    verify_text = str(jobs["verify"]["steps"][0].get("run", ""))
    assert '"passed"' in verify_text, "verify must assert the verdict outputs"
    assert "HAPPY_VERDICT" in verify_text and "SUBSET_VERDICT" in verify_text, (
        "verify must assert both calls' verdicts"
    )


def test_selftest_guardrails_isolate_the_designed_failures():
    """The five designed-failure calls live in a dispatch-only, red-by-construction file.

    Isolation is the only way a designed failure can exist without reddening
    a routine run (caller jobs have no continue-on-error). The file must be
    unreachable from push/schedule and must prove, via verify, that every
    guardrail fired - so the watchdog can tell designed red from broken red.
    """
    data = load(WF_DIR / "selftest-guardrails.yml")
    trigger = _on(data)
    assert set(trigger) == {"workflow_dispatch"}, (
        "the guardrails suite must be dispatch-only - it must never run on "
        "push or schedule and redden a routine run"
    )
    text = (WF_DIR / "selftest-guardrails.yml").read_text(encoding="utf-8")
    assert "designed" in text.lower() and "red" in text.lower(), (
        "the guardrails header must say the red runs are the expected outcome"
    )
    jobs = data["jobs"]
    for job_id in (
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "input-validation-template-ref",
    ):
        assert job_id in jobs, f"guardrails missing job {job_id}"
        assert jobs[job_id]["uses"].startswith("./"), (
            f"{job_id} must call the local reusable workflow"
        )
        assert "if" not in jobs[job_id], (
            f"{job_id} must run unconditionally - this file has exactly one, red mode"
        )
    # the five designed inputs must still be the wrong-on-purpose values
    assert jobs["input-validation"]["with"]["template_ref"].startswith("../"), (
        "input-validation must pass a traversal template_ref"
    )
    assert jobs["input-validation-stack-toggle"]["with"]["run_python"] == "ture", (
        "stack-toggle must pass the typo'd toggle"
    )
    assert "bogus-job" in jobs["input-validation-select"]["with"]["select"], (
        "select must pass an unknown select token"
    )
    assert set(jobs["verify"]["needs"]) == {
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "input-validation-template-ref",
    }, "verify must watch exactly the five designed calls"
    assert str(jobs["verify"].get("if", "")) == "always()", (
        "verify must run even when the designed calls fail - it is the "
        "designed-vs-broken discriminator inside the red run"
    )
    verify_text = str(jobs["verify"]["steps"][0].get("run", ""))
    assert '"failure"' in verify_text and '"failed"' in verify_text, (
        "verify must assert result=failure and verdict=failed for every guardrail"
    )
    assert "TOGGLE_RESULT" in verify_text and "BADREF_RESULT" in verify_text, (
        "verify must assert all five guardrails, not just the first two"
    )


def test_master_ci_exposes_verdict_output():
    data = load(WF_DIR / "master-ci.yml")
    trigger = _on(data)
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
    trigger = _on(data)
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
    trigger = _on(data)
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
    trigger = _on(data)
    assert {"push", "schedule", "workflow_dispatch"} <= set(trigger), (
        "negative paths must run routinely, not only on manual dispatch"
    )
    assert (data.get("permissions") or {}).get("actions") == "write"
    job = data["jobs"]["negative-harness"]
    script = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "selftest-guardrails.yml" in script, (
        "the watchdog must dispatch the isolated guardrails suite"
    )
    assert "failure_paths" not in script, (
        "the selftest failure_paths flag is gone - the watchdog dispatches "
        "selftest-guardrails.yml directly"
    )
    for job_name in (
        "enforced-failure",
        "input-validation",
        "input-validation-stack-toggle",
        "input-validation-select",
        "input-validation-template-ref",
    ):
        assert f'"{job_name}"' in script, f"harness must assert {job_name} failed"
    assert '"verify"' in script, "harness must assert verify succeeded"
    # the child run is the designed red: the watchdog requires conclusion
    # == failure and treats any other outcome as a broken gate
    assert "conclusion" in script and '"failure"' in script, (
        "harness must require the designed red - a guardrails run that does "
        "not fail means a guardrail is gone"
    )
    assert '"failure" not in inner' in script, (
        "harness must still assert the designed inner jobs failed"
    )
    assert "--paginate" in script, (
        "the child run has 100+ jobs - the jobs endpoint must be paginated"
    )


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
    text = (ROOT / "scripts" / "mcp_tools.py").read_text(encoding="utf-8")
    apply_body = text.split("def tool_apply_fixes", 1)[1].split("\ndef ", 1)[0]
    assert "_require_write_root" in apply_body, (
        "apply_fixes must call the mandatory write-root guard first"
    )


def test_scan_diff_discovers_untracked_files():
    """Untracked discovery is the whole point of the change-set scan."""
    text = (ROOT / "scripts" / "mcp_tools.py").read_text(encoding="utf-8")
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

    def assert_clean_filter(wf_name: str, event: str, key: str, value) -> None:
        assert isinstance(value, list) and all(isinstance(p, str) for p in value), (
            f"{wf_name}: {event}.{key} must be a list of strings, got {value!r}"
        )
        for pattern in value:
            assert "[" not in pattern and "]" not in pattern, (
                f"{wf_name}: {event}.{key} pattern {pattern!r} contains a stray "
                "bracket - this repository does not use character-class globs"
            )

    def assert_trigger_filters(wf) -> None:
        data = load(wf)
        trigger = data[True] if True in data else data.get("on") or {}
        if not isinstance(trigger, dict):
            return
        filter_keys = (
            "branches",
            "branches-ignore",
            "tags",
            "tags-ignore",
            "paths",
            "paths-ignore",
        )
        for event, spec in trigger.items():
            if not isinstance(spec, dict):
                continue
            for key in filter_keys:
                if spec.get(key) is not None:
                    assert_clean_filter(wf.name, event, key, spec[key])

    for wf in WORKFLOWS:
        assert_trigger_filters(wf)
    assert WORKFLOWS, "the workflow inventory must not be empty"


def test_self_loop_workflows_push_target_main():
    """The self-loop workflows must explicitly push-target [main] and nothing else."""
    for name in ("ci.yml", "selftest.yml", "selftest-negative.yml"):
        data = load(WF_DIR / name)
        trigger = _on(data)
        assert trigger["push"]["branches"] == ["main"], (
            f"{name}: push.branches must be exactly ['main'], got {trigger['push']['branches']!r}"
        )


# ---------------------------------------------------------------------------
# Language stacks, Docker and AWS (v1.9.0)
# ---------------------------------------------------------------------------


def test_new_language_stacks_are_wired_like_the_existing_ones():
    """java/cpp/csharp/ruby/php must behave exactly like the older stacks:
    gated on meta success, honored by select, auto-detected through the
    changes filter, timeout-declared. Since v2.5 java fetches helpers for
    workspace-aware project discovery; the rest stay zero-config (the
    helper-ref contract test pins the checkout count below)."""
    data = load(WF_DIR / "master-ci.yml")
    jobs = data["jobs"]
    for job_id in ("java", "cpp", "csharp", "ruby", "php"):
        job = jobs[job_id]
        condition = str(job.get("if", ""))
        assert "needs.meta.result == 'success'" in condition, f"{job_id} must gate on meta"
        assert f"inputs.run_{job_id}" in condition, f"{job_id} must honor its run_ toggle"
        assert f"needs.changes.outputs.{job_id}" in condition, (
            f"{job_id} must auto-detect through the changes filter"
        )
        assert isinstance(job.get("timeout-minutes"), int), f"{job_id} needs a timeout"
        detect = [s for s in job["steps"] if s.get("id") == "detect"]
        assert detect, f"{job_id} must detect its stack before running tools"
        fetchers = [
            s for s in job["steps"] if isinstance(s.get("with"), dict) and "repository" in s["with"]
        ]
        if job_id == "java":
            assert fetchers, "java must fetch helpers: discovery needs the inventory script"
        else:
            assert not fetchers, f"{job_id} must not fetch helpers (zero-config detection)"


def test_docker_job_builds_and_scans_the_image_it_builds():
    """The Docker stack must do three real things: lint the Dockerfile
    (checksum-verified hadolint), build the image, and scan that exact
    image with the same enforce_trivy profile flag the filesystem scan
    uses - a scan of some other tag would be theater."""
    data = load(WF_DIR / "master-ci.yml")
    jobs = data["jobs"]
    docker = jobs["docker"]
    condition = str(docker.get("if", ""))
    assert "needs.changes.outputs.docker" in condition, "docker must auto-detect"
    assert "inputs.run_docker" in condition, "docker must honor run_docker"
    docker_perms = docker.get("permissions") or {}
    assert docker_perms.get("security-events") == "write"
    text = "\n".join(str(s.get("run", "")) for s in docker["steps"])
    assert "hadolint-linux-x86_64" in text, "hadolint must be downloaded from its release"
    assert "sha256sum -c -" in text, "the hadolint download must be checksum-verified"
    build = next(
        s for s in docker["steps"] if s.get("name", "").startswith("Build every Dockerfile")
    )
    assert build["env"]["IMAGE_TAG"] == "${{ github.sha }}", (
        "the built image must be tagged with the commit SHA"
    )
    assert 'docker build --file "$file" --tag "master-ci-ci:$IMAGE_TAG"' in build["run"], (
        "every tracked Dockerfile must be built; the first keeps the sha tag the scan targets"
    )
    scan = next(s for s in docker["steps"] if str(s.get("uses", "")).startswith("aquasecurity"))
    with_block = scan.get("with", {})
    assert with_block.get("scan-type") == "image"
    assert with_block.get("image-ref") == "master-ci-ci:${{ github.sha }}", (
        "trivy must scan exactly the image the build step produced"
    )
    assert with_block.get("exit-code") == "${{ env.TRIVY_EXIT }}", (
        "image scan enforcement must follow the profile's enforce_trivy flag"
    )
    upload = next(
        s
        for s in docker["steps"]
        if str(s.get("uses", "")).startswith("github/codeql-action/upload-sarif")
    )
    upload_with = upload.get("with") or {}
    assert upload_with.get("category") == "trivy-image"


def test_release_docker_publishes_with_oidc_and_signing():
    """The container release pipeline must authenticate without static
    credentials (GHCR token, Docker Hub secrets, AWS OIDC), sign keylessly
    with cosign, and verify the signature before reporting success."""
    data = load(WF_DIR / "release-docker.yml")
    trigger = _on(data)
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["registry"]["required"] is True
    assert inputs["image_name"]["required"] is True
    perms = data.get("permissions") or {}
    assert perms == {"contents": "read"}
    job = data["jobs"]["publish"]
    job_perms = job.get("permissions") or {}
    assert job_perms.get("packages") == "write", "GHCR push needs packages: write"
    assert job_perms.get("id-token") == "write", "cosign keyless signing needs OIDC"
    assert job_perms.get("security-events") == "write"
    text = (WF_DIR / "release-docker.yml").read_text(encoding="utf-8")
    for forbidden in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "aws_access_key_id"):
        assert forbidden not in text, f"static AWS credentials are forbidden: {forbidden}"
    steps = [str(s.get("name", "")) for s in job["steps"]]
    assert any("cosign" in name.lower() for name in steps), "must install cosign"
    run_blocks = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "cosign sign --yes" in run_blocks, "must sign keylessly"
    assert "cosign verify" in run_blocks, "must verify the signature it just created"
    assert "--certificate-identity-regexp" in run_blocks, (
        "verification must pin the OIDC identity to this repository"
    )
    validate = next(s for s in job["steps"] if "Validate" in str(s.get("name", "")))
    validate_run = str(validate.get("run", ""))
    assert "ghcr|dockerhub|ecr" in validate_run, "registry must be validated"
    assert "role_arn is required for the ecr target" in validate_run, (
        "ECR must fail closed without an OIDC role"
    )
    scan = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("aquasecurity"))
    scan_with = scan.get("with") or {}
    assert scan_with.get("scan-type") == "image"
    publish_concurrency = data.get("concurrency") or {}
    assert publish_concurrency.get("group"), "publishes must be serialized"


def test_deploy_aws_is_oidc_first_and_fails_closed():
    """AWS deployments must use OIDC federation only - there is no input or
    secret for a static access key - and must validate the deployment
    contract before any credentials are assumed."""
    data = load(WF_DIR / "deploy-aws.yml")
    trigger = _on(data)
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["role_arn"]["required"] is True, "OIDC role is the only auth path"
    assert inputs["target"]["required"] is True
    perms = data.get("permissions") or {}
    assert perms == {"contents": "read"}
    job = data["jobs"]["deploy"]
    job_perms = job.get("permissions") or {}
    assert job_perms.get("id-token") == "write", "federation needs id-token: write"
    assert "packages" not in job_perms and "security-events" not in job_perms
    text = (WF_DIR / "deploy-aws.yml").read_text(encoding="utf-8")
    for forbidden in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "aws-access-key-id"):
        assert forbidden not in text, f"static AWS credentials are forbidden: {forbidden}"
    steps = job["steps"]
    validate_idx = next(i for i, s in enumerate(steps) if "Validate" in str(s.get("name", "")))
    cred_idx = next(
        i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("aws-actions/configure")
    )
    assert validate_idx < cred_idx, "inputs must be validated before any role is assumed"
    validate_run = str(steps[validate_idx].get("run", ""))
    for target in ("s3", "lambda", "ecs", "cloudformation"):
        assert target in validate_run, f"validate must know the {target} target"
        deploy_steps = [s for s in steps if f"env.TARGET == '{target}'" in str(s.get("if", ""))]
        assert deploy_steps, f"{target} must have a deploy step"
    configure = steps[cred_idx]
    configure_with = configure.get("with") or {}
    assert configure_with.get("role-to-assume") == "${{ inputs.role_arn }}", (
        "credentials must come from role assumption, never from secrets"
    )
    assert "verdict" in (job.get("outputs") or {}), "the deployment must report a verdict"
    deploy_concurrency = data.get("concurrency") or {}
    assert deploy_concurrency.get("group"), "deploys must be serialized"


# v1.11.1: the pins resolver, the license-free secrets scan and the
# shipped defaults that keep zero-config consumers green.


def test_pin_resolver_contract():
    text = (WF_DIR / "master-ci.yml").read_text()
    assert (
        "pip install --quiet \\\n            $(python3 _ci/scripts/tool_versions.py"
        " pip ruff mypy vulture lizard pytest tree-sitter tree-sitter-language-pack)"
    ) in text, "the multi-tool pin install must stay unquoted so pins word-split"
    assert "markdownlint-cli2@markdownlint-cli2" not in text, "double-prefixed npm package name"
    assert "markdownlint-cli2@" not in text, "docs job must take the resolver output verbatim"
    # The resolver already emits <name>@<version>; prefixing it again
    # produces jscpd@jscpd@5.4.2-style invalid tags. The pattern must
    # never appear anywhere - it is how three npm sites broke at once.
    assert "@$(python3 _ci/scripts/tool_versions.py" not in text, (
        "double-prefixed resolver output - npx must take the resolver's "
        "name@version output verbatim"
    )
    assert "gitleaks/gitleaks-action" not in text, (
        "gitleaks-action v3 demands a paid license; the pinned CLI replaces it"
    )
    assert "gitleaks detect --source ." in text, "secrets scan runs the checksum-pinned CLI"
    assert "GITLEAKS_LICENSE" not in text


def test_rust_job_ships_default_deny_policy():
    data = load(WF_DIR / "master-ci.yml")
    rust = data["jobs"]["rust"]
    assert any("cargo-deny policy" in str(s.get("name", "")) for s in rust["steps"]), (
        "cargo-deny's empty default allow list must be overridden by a shipped policy"
    )
    deny = (ROOT / "configs" / "deny.toml").read_text()
    assert '"MIT"' in deny, "MIT must be explicitly allowed"
    assert "version = 2" in deny, "cargo-deny config schema v2"


def test_default_editorconfig_grants_go_tabs():
    text = (ROOT / "configs" / ".editorconfig").read_text()
    go_section = text.split("[*.go]")[1].split("[")[0] if "[*.go]" in text else ""
    assert "indent_style = tab" in go_section, "gofmt mandates tabs; the default must allow them"


def test_rust_job_discovery_gate_covers_config_only_triggers():
    """deny.toml and rust-toolchain* sit in the rust path filter, so a
    config-only change can trigger the Rust job on a repo with no Rust
    code. Since v2.5 the discovery step owns that gate (zero projects =
    an explicit, reasoned skip) and every cargo step is gated on the
    discovery verdict - including the per-project cargo-deny run."""
    data = load(WF_DIR / "master-ci.yml")
    rust = data["jobs"]["rust"]
    names = [str(s.get("name", s.get("uses", ""))) for s in rust["steps"]]
    assert any("Discover Rust projects" in n for n in names)
    gated = [
        s for s in rust["steps"] if str(s.get("if", "")).startswith("steps.detect.outputs.manifest")
    ]
    gated_names = [str(s.get("name", s.get("uses", ""))) for s in gated]
    for must in (
        "Check formatting (rustfmt)",
        "Lint (clippy)",
        "Advisories, licenses, sources (cargo-deny, per project)",
        "Test",
    ):
        assert any(must in n for n in gated_names), f"{must} must be gated on the discovery verdict"


def test_new_code_gate_job_contract():
    """Roadmap item 7: the new-code dimension ceilings run as their own
    job, gated on diff_base, sharing the diff-base fetch helper with the
    coverage gate so the two code paths cannot drift."""
    data = load(WF_DIR / "master-ci.yml")
    job = data["jobs"]["new-code-gate"]
    text = MASTER_CI_TEXT
    assert "needs.meta.outputs.diff_base != ''" in job["if"], (
        "the gate must not run without a diff base"
    )
    assert "inputs.new_code_gates" in job["if"], "the gate must honor its off switch"
    steps = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "new_code_gate.py" in steps, "the job must evaluate the new-code gate"
    assert "--max-slop-score" in steps and "--max-cognitive" in steps, (
        "the job must pass the dimension ceilings"
    )
    assert text.count("fetch_diff_base.sh") >= 2, "both gate jobs must share the fetch helper"
    uses_refs = []
    for step in job["steps"]:
        with_block = step.get("with")
        if isinstance(with_block, dict):
            uses_refs.append(str(with_block.get("ref", "")))
    assert "${{ needs.meta.outputs.template_ref }}" in uses_refs, (
        "the helper checkout must use the resolved ref"
    )
    # the aggregation surfaces must know the job
    assert "new-code-gate" in text.split("ci-success:")[1].split("jobs:")[0], (
        "ci-success must aggregate the new-code gate"
    )
    meta_block = text.split("\n  meta:")[1].split("\n  changes:")[0]
    assert "new_code_max_cognitive" in meta_block, "meta must expose the ceilings"


# ---------------------------------------------------------------------------
# The flake hunt and its quarantine loop (flaky.yml, v2.3/v2.4)
# ---------------------------------------------------------------------------


def test_quarantine_job_exists_and_gates_three_ways():
    """The loop acts only when the hunt's data verdict says new-flakes, a
    committed baseline exists (enforcement mode) and the run targets the
    default branch - a dispatch on a feature branch hunts, never
    quarantines."""
    data = load(WF_DIR / "flaky.yml")
    jobs = data["jobs"]
    assert "hunt" in jobs and "quarantine" in jobs
    job = jobs["quarantine"]
    assert job.get("needs") == "hunt", "quarantine must consume the hunt's verdict"
    condition = str(job.get("if", ""))
    assert "needs.hunt.outputs.status == 'new-flakes'" in condition
    assert "needs.hunt.outputs.enforcement == 'true'" in condition, (
        "report-only repos must never get a quarantine PR"
    )
    assert "default_branch" in condition, "quarantine must be reserved for default-branch hunts"


def test_quarantine_holds_the_write_scopes_and_the_hunt_stays_read_only():
    """The job that runs the repository's test suite must never hold write
    access; only the PR-opening job does."""
    data = load(WF_DIR / "flaky.yml")
    top = data["permissions"]
    assert top.get("contents") == "read", "workflow-level least privilege"
    for step in data["jobs"]["hunt"]["steps"]:
        perms = step.get("permissions")
        assert not perms or perms.get("contents") == "read", (
            "the hunt executes the repo's tests and must stay read-only"
        )
    perms = data["jobs"]["quarantine"]["permissions"]
    assert perms.get("contents") == "write"
    assert perms.get("pull-requests") == "write"


def test_quarantine_is_pr_only_and_bounded_to_the_baseline_file():
    data = load(WF_DIR / "flaky.yml")
    text = (WF_DIR / "flaky.yml").read_text(encoding="utf-8")
    assert "git push" not in text, "the quarantine loop must never push"
    steps = [str(s.get("uses", "")) for s in data["jobs"]["quarantine"]["steps"]]
    assert any("create-pull-request" in u for u in steps), "changes land through PRs"
    pr_step = next(
        s
        for s in data["jobs"]["quarantine"]["steps"]
        if "create-pull-request" in str(s.get("uses", ""))
    )
    assert str(pr_step["with"].get("add-paths", "")) == ".flaky-baseline.json", (
        "even a compromised worktree can only ever offer the baseline file"
    )
    assert "ci/flake-quarantine" in text


def test_quarantine_verifies_the_candidate_independently():
    """The write job re-checks the proposal against the default branch -
    it never trusts the hunt's artifact blindly."""
    data = load(WF_DIR / "flaky.yml")
    steps = data["jobs"]["quarantine"]["steps"]
    verify = [s for s in steps if "flaky_quarantine.py verify" in str(s.get("run", ""))]
    assert verify, "the verify step is mandatory before any PR"
    run_block = str(verify[0].get("run", ""))
    assert ".flaky-baseline.json" in run_block, (
        "verification must compare against the committed floor, not the artifact"
    )


def test_quarantine_proposal_stays_outside_the_worktree():
    """The candidate is built into RUNNER_TEMP so it cannot leak into the
    PR branch through the hunt job's checkout."""
    data = load(WF_DIR / "flaky.yml")
    steps = data["jobs"]["hunt"]["steps"]
    propose = [s for s in steps if "flaky_quarantine.py propose" in str(s.get("run", ""))]
    assert propose, "the hunt must build the proposal"
    assert "$RUNNER_TEMP" in str(propose[0].get("run", ""))


def test_quarantine_serializes_with_the_hunt():
    data = load(WF_DIR / "flaky.yml")
    concurrency = data["concurrency"]
    assert concurrency["group"] == "flake-hunt"
    assert concurrency["cancel-in-progress"] is False


def test_flake_report_carries_a_machine_verdict():
    """The workflow gates on report status - the detector must produce it."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import flaky_detect as flaky_detector

    verdicts = [
        flaky_detector.TestVerdict("a", ("passed", "failed")),
        flaky_detector.TestVerdict("b", ("passed", "passed")),
    ]
    report = flaky_detector.build_report(verdicts, flaky_detector.BaselineDelta((), ("a",), ()), 2)
    assert report["status"] == "new-flakes"
    known = flaky_detector.build_report(verdicts, flaky_detector.BaselineDelta(("a",), (), ()), 2)
    assert known["status"] == "quarantined-only"
    clean = flaky_detector.build_report(
        [flaky_detector.TestVerdict("b", ("passed", "passed"))],
        flaky_detector.BaselineDelta((), (), ()),
        2,
    )
    assert clean["status"] == "clean"


def test_zizmor_suppressions_sit_where_zizmor_parses_them():
    """`# zizmor: ignore[...]` only works as a YAML comment: standalone on
    its own line (covers the next line) or trailing a key/value line. Inside
    a `run: |` block scalar it is shell text - the suppression never
    registers and the hygiene gate fails (the v2.4.0 escape)."""
    for wf in WORKFLOWS:
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            if "# zizmor:" not in line:
                continue
            prefix = line.split("#", 1)[0].rstrip()
            on_key_line = re.search(r"^\s*(?:-\s+)*(?:uses|run|working-directory|shell):\s", prefix)
            assert prefix.strip() == "" or on_key_line, (
                f"{wf.name}:{lineno}: `# zizmor:` comment is buried in script "
                "text where zizmor cannot see it - put it on the YAML key line "
                "(`run: | # zizmor: ignore[...]`) or on a standalone line above"
            )


def test_parser_dependent_jobs_install_and_demand_parsers(master_ci_text: str):
    """The v2.5 parser guarantee: every job that promises parse-backed
    metrics installs the exact pinned analyzers, the scan jobs demand
    them (--require-parsers -> exit 2), and the python stack's lizard
    gate refuses a missing binary."""
    text = master_ci_text
    install = (
        "pip install --quiet $(python3 _ci/scripts/tool_versions.py pip lizard "
        "tree-sitter tree-sitter-language-pack)"
    )
    assert text.count(install) == 3, (
        "ai-integrity, new-code-gate and quality-ratchet must install the pinned parsers"
    )
    assert "--require-parsers \\\n            --report-file ai_report.md" in text, (
        "ai-integrity must demand the analyzers (exit 2 on a missing analyzer)"
    )
    assert "--require-parsers | tee ratchet-report.md" in text, (
        "quality-ratchet must demand the analyzers (exit 2 on a missing analyzer)"
    )
    assert "command -v lizard" in text, (
        "the python stack's lizard gate must refuse a missing binary"
    )


def test_go_rust_jvm_stacks_discover_projects(master_ci_text: str):
    """Discovery is wired into every project family: the go, rust and jvm
    jobs loop over the discovered inventory, so nested projects can never
    fall back to root-manifest-only analysis (the v2.5 monorepo fix)."""
    text = master_ci_text
    expected = {"go": 2, "rust": 1, "jvm": 1}  # go splits lint/test; rust and jvm are one job
    for family, count in expected.items():
        assert text.count(f"discover_projects.py --family {family} --list") == count, (
            f"{family}: every stack job must discover projects"
        )
    assert "root manifests only" not in text
    assert "root builds only" not in text


def test_go_and_cargo_tool_pins_exist():
    """New toolchain families are pinned like every other analyzer: a
    silent latest can never sneak back in."""
    pins = yaml.safe_load((ROOT / "configs" / "tool-versions.yml").read_text(encoding="utf-8"))
    assert re.fullmatch(r"v\d+\.\d+\.\d+", pins["go"]["golangci-lint"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["cargo"]["cargo-deny"])


def test_assurance_summary_reports_armed_and_off_layers(master_ci_text: str):
    """Opt-in test assurance must be visible, never silently absent: the
    PR dashboard and the ci-success summary both carry a Test assurance
    section driven by the meta profile outputs."""
    text = master_ci_text
    assert text.count("### Test assurance") == 2
    for needle in (
        "COVERAGE_MIN: ${{ needs.meta.outputs.coverage_min }}",
        "NEW_CODE_GATES: ${{ needs.meta.outputs.new_code_gates }}",
        "RATCHET: ${{ needs.meta.outputs.ratchet }}",
    ):
        assert text.count(needle) >= 2


# ---------------------------------------------------------------------------
# v2.6: the wider net - coverage and mutation across the whole map,
# dependency hygiene on every stack, the quality trend history
# ---------------------------------------------------------------------------


def test_coverage_gate_consumes_every_measured_slice():
    """The gate downloads all four stacks' artifacts and waits on the
    stacks that can produce them - a slice that ships no report can
    never silently opt itself out of the floor."""
    data = load(WF_DIR / "master-ci.yml")
    gate = data["jobs"]["coverage-gate"]
    assert {"meta", "python", "go-test", "node-test", "rust", "java"} <= set(gate["needs"])
    text = MASTER_CI_TEXT
    assert "pattern: coverage-*" in text, "artifact download must keep the wildcard"


def test_rust_stack_measures_coverage_when_armed(master_ci_text: str):
    """COVERAGE_MIN arms the Rust slice: the pinned cargo-llvm-cov emits
    a cobertura report per discovered crate, and the artifact carries
    them to the gate."""
    assert "cargo llvm-cov --workspace --all-features" in master_ci_text
    assert "tool_versions.py cargo cargo-llvm-cov" in master_ci_text, "pinned install"
    assert "--cobertura --output-path" in master_ci_text
    assert "name: coverage-rust" in master_ci_text
    assert "rustup component add llvm-tools-preview" in master_ci_text


def test_jvm_stack_injects_the_pinned_jacoco_agent(master_ci_text: str):
    """Maven receives JaCoCo from the command line (prepare-agent +
    report goals, no pom edit); Gradle projects with the plugin get
    their report task invoked; the collected jacoco.xml files ride the
    coverage-jvm artifact."""
    assert "tool_versions.py maven jacoco" in master_ci_text
    assert "jacoco-maven-plugin:${jacoco_version}:prepare-agent" in master_ci_text
    assert "jacoco-maven-plugin:${jacoco_version}:report" in master_ci_text
    assert "test jacocoTestReport" in master_ci_text
    assert "*/target/site/jacoco/jacoco.xml" in master_ci_text
    assert "*/build/reports/jacoco/*jacocoTestReport.xml" in master_ci_text
    assert "name: coverage-jvm" in master_ci_text


def test_dependency_hygiene_spans_every_stack(master_ci_text: str):
    """Unused-dependency analysis is the coordinator pattern: depcheck
    (Node), vulture (Python), cargo-machete (Rust) and `go mod tidy
    -diff` (Go) - every one advisory, every install pinned."""
    assert "go mod tidy -diff" in master_ci_text
    assert "tool_versions.py cargo cargo-machete" in master_ci_text
    assert "cargo-machete flagged unused dependencies" in master_ci_text
    assert "go mod tidy would change go.mod/go.sum" in master_ci_text
    assert "depcheck flagged unused or missing dependencies" in master_ci_text


def test_pins_file_covers_the_new_families():
    pins = yaml.safe_load((ROOT / "configs" / "tool-versions.yml").read_text(encoding="utf-8"))
    assert pins["cargo"]["cargo-llvm-cov"]
    assert pins["cargo"]["cargo-mutants"]
    assert pins["cargo"]["cargo-machete"]
    assert pins["go"]["gremlins"]
    assert pins["maven"]["jacoco"]
    out = subprocess_run_tool_versions(["maven", "jacoco"])
    assert out == "0.8.16"


def subprocess_run_tool_versions(argv: list[str]) -> str:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "tool_versions.py"), *argv],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_mutation_workflow_knows_four_ecosystems(master_ci_text: str):
    """Opt-in by declaration: mutmut/stryker keep their signals and
    cargo-mutants (.cargo-mutants.toml) and gremlins (.gremlins.yaml)
    join - both installs pinned through the pins file. Since v2.11 the
    declarations are independent: every declared tool runs, not just
    the first one the old chain matched."""
    text = (WF_DIR / "mutation.yml").read_text(encoding="utf-8")
    assert ".cargo-mutants.toml" in text
    assert ".gremlins.yaml" in text
    assert "tool_versions.py cargo cargo-mutants" in text
    assert "go-gremlins/gremlins/cmd/gremlins@" in text
    assert "gremlins unleash" in text
    assert "cargo mutants" in text
    # the summary must advertise the widening, not keep the old lie
    assert "Python, Node, Go and Rust" in master_ci_text
    # v2.11: every declared tool runs, independently - the old first-match
    # chain let a [tool.mutmut] declaration mute the other three forever
    assert 'echo "tools=$tools" >> "$GITHUB_OUTPUT"' in text
    for tool in ("mutmut", "stryker", "cargo-mutants", "gremlins"):
        assert f'declared+=("{tool}")' in text
        cond = f"contains(fromJSON(steps.detect.outputs.tools || '[]'), '{tool}')"
        assert cond in text, f"the {tool} steps must key on the declared tools array"


def test_mutation_declaration_chain_has_no_first_match():
    """The v2.10 hosted mutation exercise exposed the structural gap the
    detect chain carried since v2.6: the four declarations were an
    if/elif first-match chain, so the canary's [tool.mutmut] muted
    gremlins, cargo-mutants and Stryker forever - three of the four
    ecosystems had no hosted evidence anywhere, and no ten-stack repo
    could ever mutate more than its first-declared stack. The chain is
    dead: each declaration appends independently and the canonical
    ecosystem order filters what runs."""
    text = (WF_DIR / "mutation.yml").read_text(encoding="utf-8")
    detect = text.split("- name: Detect mutation configs", 1)[1].split("- uses:", 1)[0]
    assert "declared=()" in detect
    assert "elif" not in detect, "a first-match chain would mute every later declaration"
    for tool in ("mutmut", "stryker", "cargo-mutants", "gremlins"):
        assert f'declared+=("{tool}")' in detect
    assert '"mutmut", "stryker", "cargo-mutants", "gremlins"' in detect, (
        "canonical ecosystem order, filtered by declaration"
    )


def test_gremlins_gate_needs_declared_thresholds():
    """Verified against gremlins v0.6.0: without thresholds the run
    exits 0 no matter how many mutants live, so the workflow comment
    must say the .gremlins.yaml is the gate."""
    text = (WF_DIR / "mutation.yml").read_text(encoding="utf-8")
    assert "threshold" in text, "the gate contract must be documented in place"


def test_ci_success_records_the_quality_trend(master_ci_text: str):
    """SonarQube's lesson, wired: the cache rolls the history forward on
    the ref, record and save run under always() (failures are exactly
    the rows the trend exists to mark), and the trend table lands in
    the summary."""
    assert "actions/cache/restore@" in master_ci_text
    assert "actions/cache/save@" in master_ci_text
    assert "quality-history.jsonl" in master_ci_text
    assert "restore-keys:" in master_ci_text
    assert "quality_history.py record" in master_ci_text
    assert "quality_history.py trend" in master_ci_text
    # both record and save must survive red runs
    restore_idx = master_ci_text.index("Restore quality history")
    record_idx = master_ci_text.index("Record and render quality trend")
    save_idx = master_ci_text.index("Save quality history")
    for name, idx in (("record", record_idx), ("save", save_idx)):
        block = master_ci_text[idx : idx + 600]
        assert "if: always()" in block, f"{name} step must run under always()"
    assert restore_idx < record_idx < save_idx, "restore, record, save in order"


def test_quality_history_module_is_the_only_history_writer():
    text = MASTER_CI_TEXT
    assert text.count("quality-history.jsonl") >= 4
    assert ">> quality-history.jsonl" not in text, "append only through the module"


# ---------------------------------------------------------------------------
# v2.7: the semantic eye - CodeQL auto-detection and the verified
# Semantic analysis row in the assurance tables
# ---------------------------------------------------------------------------


def test_codeql_defaults_to_zero_config_detection():
    """languages: auto is the default: CodeQL joins the template's
    zero-config surface instead of asking every caller to enumerate
    dialects. The detector rides the template checkout so the manifest
    map is versioned with its tests, explicit languages short-circuit
    the detection steps, and forks can point template_repo elsewhere."""
    data = load(WF_DIR / "codeql.yml")
    inputs = _on(data)["workflow_call"]["inputs"]
    assert inputs["languages"]["default"] == "auto"
    assert inputs["template_repo"]["default"] == "WasewaseX/master-ci"
    assert inputs["template_ref"]["default"] == "v2"
    text = (WF_DIR / "codeql.yml").read_text(encoding="utf-8")
    assert "python3 _ci/scripts/codeql_detect.py --root . --format gh-output" in text
    detect_steps = (
        "Check out the calling repository",
        "Fetch the language detector from the template",
        "Auto-detect CodeQL languages",
    )
    names = [s.get("name", "") for s in data["jobs"]["prepare"]["steps"]]
    for name in detect_steps:
        assert name in names, f"prepare is missing the {name!r} step"
    for step in data["jobs"]["prepare"]["steps"]:
        if step.get("name") in detect_steps:
            assert step.get("if") == "inputs.languages == 'auto'", (
                f"{step.get('name')}: explicit languages must short-circuit detection"
            )


def test_codeql_language_sets_cannot_drift():
    """The detector's SUPPORTED set and the prepare job's allowed set are
    one contract in two places - the equality test turns drift into a
    red test instead of a runtime surprise."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from codeql_detect import SUPPORTED

    text = (WF_DIR / "codeql.yml").read_text(encoding="utf-8")
    block = text[text.index("const allowed = new Set([") :]
    block = block[: block.index("]);")]
    allowed = set(re.findall(r"'([\w-]+)'", block))
    assert set(SUPPORTED) == allowed


def test_assurance_verifies_codeql_wiring(master_ci_text: str):
    """The Semantic analysis row is verified, not declared: dashboard and
    ci-success both check out the caller and grep its own workflow files
    for the codeql.yml or required-quality.yml call before claiming the
    layer is armed."""
    text = master_ci_text
    # two tables x three states (aggregate, own workflow, off): the row
    # exists in both the PR dashboard and the ci-success summary, each
    # with all three branches.
    assert text.count("Semantic analysis (CodeQL)") == 6
    assert text.count('"workflows/codeql.yml@" in text') == 2
    assert text.count('"workflows/required-quality.yml@" in text') == 2
    data = load(WF_DIR / "master-ci.yml")
    for job_id in ("dashboard", "ci-success"):
        first = data["jobs"][job_id]["steps"][0]
        assert first.get("name") == "Check out the calling repository", job_id
        assert first["with"]["persist-credentials"] is False


def test_required_quality_aggregate_forwards_every_master_input():
    """Audit P0 F: the aggregate is the check callers REQUIRE, so it must
    expose the whole master-ci input surface - a new master input without
    an aggregate twin is a silent feature loss for aggregate users. The
    contract makes the drift equality explicit."""
    master = load(WF_DIR / "master-ci.yml")
    aggregate = load(WF_DIR / "required-quality.yml")
    master_inputs = set(_on(master)["workflow_call"]["inputs"])
    aggregate_inputs = set(_on(aggregate)["workflow_call"]["inputs"])
    missing = master_inputs - aggregate_inputs
    assert not missing, f"aggregate lacks master-ci inputs: {sorted(missing)}"
    master_with = aggregate["jobs"]["master"]["with"]
    for name in master_inputs:
        assert master_with.get(name) == f"${{{{ inputs.{name} }}}}", name
    codeql_with = aggregate["jobs"]["codeql"]["with"]
    for name in ("languages", "queries", "config_file"):
        assert codeql_with.get(name) == f"${{{{ inputs.{name} }}}}", name
    for name in ("template_repo", "template_ref"):
        assert codeql_with.get(name) == f"${{{{ inputs.{name} }}}}", name


def test_required_quality_pin_moves_with_the_release():
    """The aggregate's uses: lines are fixed strings (expressions may not
    read inputs there), so the release commit must bump them; the
    contract ties the pins to SERVER_VERSION so they cannot go stale."""
    mcp = _load_mcp_server()
    text = (WF_DIR / "required-quality.yml").read_text(encoding="utf-8")
    assert f".github/workflows/master-ci.yml@v{mcp.SERVER_VERSION}" in text
    assert f".github/workflows/codeql.yml@v{mcp.SERVER_VERSION}" in text


def test_required_quality_gate_fails_closed():
    """The gate job is the single required check: green only when both
    children succeeded - skipped counts as red, never as a pass."""
    aggregate = load(WF_DIR / "required-quality.yml")
    gate = aggregate["jobs"]["gate"]
    assert gate["needs"] == ["master", "codeql"]
    assert gate["if"] == "always()"
    run = gate["steps"][0]["run"]
    assert '[ "$master" != "success" ]' in run
    assert '[ "$codeql" != "success" ]' in run
    assert "exit $fail" in run


def test_required_quality_check_name_matches_the_docs():
    """Branch protection sees '<caller job id> / <job id>' for a job-called
    reusable workflow - the ci-canary's hosted runs report 'required /
    gate' and 'master / ci-success', never a bare 'ci-success' and never
    the callee's workflow name. The docs' required-check strings must
    therefore name the same job id their own examples give the call: the
    README's aggregate example names its job 'required-quality-check',
    the examples/ci.yml aggregate comment names its job 'required'.
    Keeping the workflow's own name in sync stays a cosmetic contract."""
    aggregate = load(WF_DIR / "required-quality.yml")
    assert aggregate["name"] == "required-quality-check"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "jobs:\n    required-quality-check:" in readme, (
        "the README's aggregate example must give the call the job id its requirement string names"
    )
    assert "required-quality-check / gate" in readme
    example = (ROOT / "examples/ci.yml").read_text(encoding="utf-8")
    assert "required / gate" in example, (
        "examples/ci.yml must require the check its own job id produces"
    )


def test_doc_workflow_refs_move_with_the_release():
    """An exact pin in the docs is a promise about that tag's behavior:
    the README hands consumers required-quality.yml@v2.8.0 while the fix
    for that tag's stale check name lived unreleased on main, so every
    reader kept being pointed at the mismatched tag. Any exact vX.Y.Z
    workflow ref in README, SKILL.md, docs/ or examples/ must be the
    release baseline; the @v2 major tag stays the documented default."""
    mcp = _load_mcp_server()
    baseline = f"v{mcp.SERVER_VERSION}"
    docs = [ROOT / "README.md", ROOT / "SKILL.md"]
    docs += sorted((ROOT / "docs").glob("*.md"))
    docs += sorted((ROOT / "examples").glob("*.yml"))
    stale = []
    for path in docs:
        text = path.read_text(encoding="utf-8")
        pattern = r"WasewaseX/master-ci/\.github/workflows/[\w-]+\.yml@(v\d+\.\d+\.\d+)"
        for ref in re.findall(pattern, text):
            if ref != baseline:
                stale.append(f"{path.name}: {ref}")
    assert not stale, (
        f"docs pin an exact release other than the baseline {baseline} "
        f"(a stale exact pin sends consumers to a tag whose contract may "
        f"differ): {stale}"
    )


# ---------------------------------------------------------------------------
# v2.9: the callable proof - mutation.yml becomes a reusable workflow and
# the Mutation testing row is verified, not declared
# ---------------------------------------------------------------------------


def test_mutation_workflow_is_callable():
    """A scheduled surface a foreign repository cannot call is a
    capability the consumer must copy by hand. mutation.yml joins
    codeql.yml as a reusable workflow: the caller owns the schedule
    trigger (a called workflow cannot carry one), the call is one pinned
    line, and the helper defaults follow the codeql contract -
    template_repo explicit, because GITHUB_WORKFLOW_REF names the
    caller's workflow on a cross-repo call and auto-detection would
    fetch helpers from the wrong repo. The self-run triggers keep their
    own inputs: the weekly schedule passes none, so the resolve step's
    auto-detection still serves the template's own runs."""
    data = load(WF_DIR / "mutation.yml")
    on = _on(data)
    assert set(on) >= {"schedule", "workflow_call", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "0 3 * * 1"}]
    call = on["workflow_call"]["inputs"]
    assert call["template_repo"]["default"] == "WasewaseX/master-ci"
    assert call["template_ref"]["default"] == "v2"
    dispatch = on["workflow_dispatch"]["inputs"]
    assert dispatch["template_repo"]["default"] == ""
    assert dispatch["template_ref"]["default"] == "main"
    assert data["permissions"] == {"contents": "read"}
    text = (WF_DIR / "mutation.yml").read_text(encoding="utf-8")
    # one job serves every trigger; the helper inputs reach it through
    # the same env passthrough the self-run uses
    assert text.count("TEMPLATE_REPO_INPUT: ${{ inputs.template_repo }}") == 1
    assert text.count("TEMPLATE_REF: ${{ inputs.template_ref }}") == 1
    assert "reads the caller's checkout" in text, (
        "opt-in stays a declaration in the caller's checkout"
    )


def test_assurance_verifies_mutation_wiring(master_ci_text: str):
    """The Mutation testing row is verified, not declared: dashboard and
    ci-success grep the caller's workflow files for the mutation.yml
    call before claiming the layer is armed - two states, both tables.
    The scan must reach every workflow file: the old loop stopped at the
    aggregate's file, which would hide a mutation call in a file sorted
    after it."""
    text = master_ci_text
    assert text.count("armed via own workflow - `{mutation_wf}` calls `mutation.yml`") == 2
    assert text.count("off - add a scheduled caller of `mutation.yml`") == 2
    assert text.count('"workflows/mutation.yml@" in text') == 2
    assert text.count('mutation_wf = ""') == 2
    # the declared row that claimed a scheduled workflow unconditionally
    # is gone from both tables
    assert "scheduled workflow (`mutation.yml`)" not in text


def test_python_stack_cannot_see_the_helper_checkout(master_ci_text: str):
    """The transient _ci helper checkout is the template's own code,
    versioned and linted by the template's battery; a consumer's scan
    must never judge it. Vulture and lizard already exclude it; the
    default ruff config - copied when the consumer ships none - and the
    mypy invocation must too. The ci-canary caught the gap as an I001
    on _ci/scripts/discover_projects.py, the first time its python
    stack ever ran beside a helper checkout (every earlier canary
    exercise touched only workflows, and the path filter skipped the
    stack)."""
    text = (ROOT / "configs" / "ruff.toml").read_text(encoding="utf-8")
    assert 'extend-exclude = ["_ci"]' in text
    assert "--exclude '(^|/)_ci/'" in master_ci_text, (
        "the mypy scan must exclude the helper checkout the way its own file probe does"
    )


# ---------------------------------------------------------------------------
# v2.10: the reusable hunt - flaky.yml joins the callable surface and the
# Flake hunt row is verified, not declared
# ---------------------------------------------------------------------------


def test_flaky_workflow_is_callable():
    """The last copy-whole surface becomes one pinned uses: line.
    flaky.yml joins codeql.yml and mutation.yml as a reusable workflow:
    the caller owns the schedule trigger (a called workflow cannot carry
    one) and the quarantine loop it drives, while the helper defaults
    follow the codeql contract - template_repo explicit, because
    GITHUB_WORKFLOW_REF names the caller's workflow on a cross-repo call
    and auto-detection would fetch helpers from the wrong repo. The
    self-run triggers keep their own inputs: the weekly schedule passes
    none, so the resolve step's auto-detection still serves the
    template's own runs."""
    data = load(WF_DIR / "flaky.yml")
    on = _on(data)
    assert set(on) >= {"schedule", "workflow_call", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "0 4 * * 1"}]
    call = on["workflow_call"]["inputs"]
    assert call["iterations"]["default"] == "3"
    assert call["template_repo"]["default"] == "WasewaseX/master-ci"
    assert call["template_ref"]["default"] == "v2"
    dispatch = on["workflow_dispatch"]["inputs"]
    assert dispatch["iterations"]["default"] == "3"
    assert dispatch["template_repo"]["default"] == ""
    assert dispatch["template_ref"]["default"] == "main"
    assert data["permissions"] == {"contents": "read"}
    text = (WF_DIR / "flaky.yml").read_text(encoding="utf-8")
    # both jobs serve every trigger through the same env passthrough the
    # self-run uses - the call boundary adds no second plumbing path
    assert text.count("TEMPLATE_REPO_INPUT: ${{ inputs.template_repo }}") == 2
    assert text.count("TEMPLATE_REF: ${{ inputs.template_ref }}") == 2
    # the quarantine loop survives the call boundary unchanged
    assert "create-pull-request" in text


def test_assurance_verifies_flaky_wiring(master_ci_text: str):
    """The Flake hunt row is verified, not declared: dashboard and
    ci-success grep the caller's workflow files for the flaky.yml call
    before claiming the layer is armed - two states, both tables. The
    declared row that named a scheduled workflow unconditionally is
    gone: a caller without the committed call must read 'off', not a
    promise about a file that is not wired."""
    text = master_ci_text
    assert text.count("armed via own workflow - `{flaky_wf}` calls `flaky.yml`") == 2
    assert text.count("off - add a scheduled caller of `flaky.yml`") == 2
    assert text.count('"workflows/flaky.yml@" in text') == 2
    assert text.count('flaky_wf = ""') == 2
    # the declared row that claimed a scheduled workflow unconditionally
    # is gone from both tables
    assert "scheduled workflow (`flaky.yml`)" not in text


def test_flaky_hunt_names_every_toolchain_version():
    """Every language-setup step that demands an explicit version input
    carries one. ruby/setup-ruby fails closed without `ruby-version`
    when the consumer commits neither .ruby-version nor .tool-versions -
    the ci-canary was the first hosted consumer with a Gemfile and no
    version file, and the setup died before the hunt could run (the
    template itself has no Gemfile, so self-runs never reach the step).
    setup-python, -node, -go, -java and -php already pass versions; the
    assertions pin the whole set so a new setup step cannot join the
    hunt without one. dtolnay/rust-toolchain rides its branch pin and
    setup-dotnet deliberately uses the runner's SDK."""
    text = (WF_DIR / "flaky.yml").read_text(encoding="utf-8")
    assert "ruby-version: '3.4'" in text, (
        "ruby/setup-ruby fails closed without ruby-version or a consumer version file"
    )
    assert "python-version: '3.13'" in text
    assert "node-version: '22'" in text
    assert "go-version: stable" in text
    assert "java-version: '21'" in text
    assert "php-version: '8.4'" in text


def test_flaky_call_grant_rule_is_documented():
    """The write scopes the called quarantine job declares must be granted
    on the CALLING job, or the call never starts - the ci-canary's first
    read-only dispatch died as a startup_failure with zero jobs, before
    any step could explain itself. The scopes let the run start; the
    committed baseline decides whether the loop ever fires. The docs must
    teach the rule the hosted run proved, not the guess that failed."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    checks = (ROOT / "docs" / "checks.md").read_text(encoding="utf-8")
    assert "pull-requests: write" in readme, "the README example must grant the scopes"
    assert "contents: write" in readme
    for doc, name in ((readme, "README.md"), (checks, "checks.md")):
        lowered = doc.lower()
        assert "never starts" in lowered or "never start" in lowered, (
            f"{name} must state that an under-granted call never starts"
        )
