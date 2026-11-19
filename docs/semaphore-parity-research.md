# Semaphore CI/CD parity research — what it does and how master-ci reverse-engineers it

Research date: 2026-11-19. Source: docs.semaphore.io (Cloud edition, ~32 pages
fetched live: pipeline YAML reference, conditions DSL, workflows, promotions,
secrets, artifacts, test reports, flaky tests, jobs/debugging, machine types,
self-hosted config, tasks, insights, org health, dashboards, monorepo, toolbox,
cache, Docker layer caching, billing, API, CLI). This document is the map for
round-16: each Semaphore mechanic is paired with the master-ci feature that
replicates it, or the reason the repo skips it.

## 1. Pipeline model (v1.0 YAML)

**Semaphore:** `.semaphore/semaphore.yml` = `version: v1.0` + `agent`
(machine + containers), `execution_time_limit` (max 24h), `fail_fast`
(`stop`/`cancel` conditions), `queue` + `auto_cancel`, `global_job_config`
(prologue/epilogue applied to every job), `blocks` with `dependencies`,
`skip`/`run` conditions, `task.jobs` with `matrix` and `parallelism`,
`after_pipeline` jobs that always run, `promotions`.

**master-ci mapping:** GitHub Actions jobs stand for blocks, `needs:` for
dependencies, `timeout-minutes` for `execution_time_limit`,
`concurrency: {group, cancel-in-progress}` for `fail_fast`/`auto_cancel`,
`strategy.matrix` for `matrix`, reusable workflows for `global_job_config`
prologue, `if: always()` summary jobs for `after_pipeline`. The block
`skip: {when}` conditions DSL maps to `if:` expressions. The repo's
19 workflow files (ci.yml, required-quality.yml, nightly.yml, selftest*.yml,
mutation.yml, flaky.yml, ...) already form the block DAG.

## 2. Workflows and the start API

**Semaphore:** workflows are trees of pipelines; `POST
/api/v1alpha/plumber-workflows` starts one with `parameters` exported as
env vars; forked PRs run secret-less and need `/sem-approve` with a pinned
SHA; `[skip ci]` is honored; a YAML-lint endpoint (`POST /api/v1alpha/yaml`)
validates definitions before submit.

**master-ci mapping:** `workflow_dispatch`/`repository_dispatch` inputs =
parameters; PR gate jobs run on `pull_request_target` with explicit secret
scoping; the repo's `test_workflow_contracts.py` pins workflow YAML shape in
unit tests, which is the lint-endpoint equivalent (fail before submit, not
after).

## 3. Promotions (the signature feature)

**Semaphore:** `promotions:` lists named targets with `pipeline_file`,
`auto_promote: {when: "result = 'passed' and branch = 'main'"}` and
parameterized `parameters: {env_vars: [{name, description, required,
default_value, options}]}` with `regex_pattern` validation. Manual
promotion works while the parent still runs; parameters become env vars in
every job of the promoted pipeline; `result`/`result_reason` are only legal
inside `auto_promote`.

**master-ci (round-16):** `scripts/sem_promotions.py` implements the model
as repo data + a validator + a dispatch simulator. `.semaphore/promotions.yml`
declares the two real promotions this repo has — `quality-store-pin` (the
quality-store branch snapshot after a green main run) and `release-tag`
(the tag promotion). The validator enforces: unique names, existing target
files, the conditions-DSL subset (`result`, `result_reason`, `branch`,
`tag`, operators `=`/`!=`/`=~`/`and`/`or`, `result` only inside
`auto_promote`), required parameters carrying a `default_value` when the
promotion auto-promotes, and `regex_pattern` validation of supplied values.
`promote` prints the exact `gh workflow run` command after validating every
parameter — the manual-promotion path with no secrets printed.

## 4. Secrets

**Semaphore:** org/project/environment scopes, write-only values, import
via `secrets:`, a nonexistent secret name **fails the pipeline at compile
time**, forked-PR runs are secret-less unless allow-listed.

**master-ci (round-16):** `scripts/sem_secrets_lint.py` replicates the
compile-time contract: every `${{ secrets.X }}` used under
`.github/workflows/` must exist in `docs/secrets-manifest.yml`; an unknown
name is a hard failure (exit 1). It also flags secret use inside workflows
that trigger on forked pull requests, and raw `${{ secrets.X }}`
interpolation directly into an `echo`/`run:` string. The manifest records
`GITHUB_TOKEN` (implicit) and `AUTOFIX_TOKEN` (autofix/flaky/heal) with
their consumer workflows.

## 5. Artifacts

**Semaphore:** `artifact push/pull` with three namespaces — job (private),
workflow (shared across the pipeline tree incl. promotions), project
(long-lived deliverables, 400-day default retention).

**master-ci mapping:** `actions/upload-artifact`/`download-artifact` covers
job + workflow namespaces; the quality-store branch is the project
namespace (the pinned snapshot every round must survive). No additional
code needed; the naming in upload steps keeps the same taxonomy.

## 6. Test reports and flaky detection

**Semaphore:** each job emits JUnit XML (`pytest --junitxml`), publishes in
an `epilogue.always`, and an `after_pipeline` job runs `test-results
gen-pipeline-report` — failed-first, slowest-first aggregation with pass
rates. Flaky detection turns on with reports: same-commit differing
results; the dashboard exposes pass_rate/age/disruptions filters.

**master-ci (round-16):** `scripts/sem_test_reports.py` is the
compile/combine/gen-pipeline-report triad in one tool: it parses one or
more JUnit XML files, produces the canonical JSON (suite/class/name →
status, duration, message) and the failed-first, slowest-first markdown
report for `GITHUB_STEP_SUMMARY`. The repo's existing flaky_*.py pipeline
(quarantine + aggregate) remains the flaky side; the report tool feeds it
stable JUnit input from any workflow, not just pytest runs.

## 7. Jobs, debugging, agents

**Semaphore:** SSH debug (`sem debug job`), attach, port-forward, machine
generations (F1/R1/A2), self-hosted agents with pre-job/post-job/shutdown
hooks and `upload-job-logs`.

**master-ci mapping:** SSH debug is not replicable on hosted runners
without tmate; deliberately skipped (documented here so the skip is
honest). The pre-job/post-job hook semantics already exist as the
`hook_gate.py`/`hook_install.py` local hook pair. Machine sizing maps to
GitHub runner labels.

## 8. Scheduling (Tasks)

**Semaphore:** Tasks = named crontab schedulers with parameters,
`validate_input_format` regex validation, and notification-suppression for
scheduled runs so cron noise does not bury PR checks.

**master-ci mapping:** `nightly.yml` `schedule:` + `workflow_dispatch`
covers it. Round-16 wiring runs the semaphore tools inside the nightly
(insights + test-report aggregation), which mirrors Semaphore's
after-pipeline reporting job on a cron.

## 9. Insights

**Semaphore:** per-project metrics over the last 30 days — performance
(median duration), reliability (pass rate **excluding** user-stopped and
auto-cancelled runs), frequency (runs/week), MTTR; dashboards YAML;
an API returning the same time series.

**master-ci (round-16):** `scripts/sem_insights.py` computes the same four
metrics from `gh run list --json` (or a provided JSON dump): median + p95
duration per workflow/branch, pass rate excluding cancelled runs (the
exclusion rule is reproduced exactly), runs/week over a 7-day window, and
mean-time-to-recovery from failure to next success. Output: JSON + a
markdown table for `GITHUB_STEP_SUMMARY`. `--fail-under RELIABILITY` makes
the nightly gate actionable.

## 10. Change detection / monorepo

**Semaphore:** `change_in('/lib/', {default_branch: 'main'})` inside
conditions; merge-base ranges per trigger type; `pipeline_file: track` so
touching the YAML reruns everything.

**master-ci mapping:** `scripts/path_filters.py` (path filtering with
documented fallbacks) is the repo's change_in; the `test_path_filters.py`
contract pins the merge-base behavior. No new code.

## 11. Cache, Docker layer caching, billing

**Semaphore:** `cache restore/store` with key chains (exact → branch →
trunk) and keys never overwritten; DLC as registry `--cache-from` pattern;
per-minute billing.

**master-ci mapping:** round-13's pip cache slice (d698f4d) is the key-chain
cache; Docker builds are out of scope for a pure-Python scanner repo
(release-docker builds one image; the registry cache pattern applies when
its size justifies it). Billing has no equivalent on GitHub-hosted runners
worth replicating beyond the insights duration metrics.

## 12. AI surface

**Semaphore:** sem-ai CLI, an MCP server, self-healing CI, and
`test-results --generate-mcp-summary` (report JSON "optimized for AI agent
consumption").

**master-ci mapping:** the repo already ships an MCP server
(mcp_server.py/mcp_tools.py) exposing the detector; `sem_test_reports.py`
emits the same machine-readable summary shape (`--json`), so agent
consumption is already the default output path.

## Adoption summary (round-16)

| Semaphore feature | master-ci artifact | Status |
| --- | --- | --- |
| Promotions + parameters | sem_promotions.py + .semaphore/promotions.yml | added |
| Compile-time secret check | sem_secrets_lint.py + docs/secrets-manifest.yml | added |
| Test reports aggregation | sem_test_reports.py | added |
| Insights (perf/reliability/frequency/MTTR) | sem_insights.py | added |
| Pipeline model, matrix, fail-fast | existing workflow DAG + concurrency | pre-existing |
| Change detection | path_filters.py | pre-existing |
| Cache key chain | pip cache slice (round-13) | pre-existing |
| Artifact namespaces | actions/artifact + quality-store branch | pre-existing |
| Flaky detection | flaky_*.py pipeline | pre-existing |
| MCP/AI surface | mcp_server.py | pre-existing |
| SSH debug, machine sizing | not applicable on hosted runners | skipped (honest) |
| Billing | no equivalent worth building | skipped (honest) |

## Source pages

docs.semaphore.io: /reference/pipeline-yaml, /reference/conditions-dsl,
/reference/env-vars, /reference/api, /reference/toolbox,
/reference/semaphore-cli, /reference/machine-types,
/reference/self-hosted-config, /reference/agent-yaml,
/reference/secret-yaml, /reference/dashboard-yaml,
/using-semaphore/workflows, /using-semaphore/promotions,
/using-semaphore/secrets, /using-semaphore/artifacts,
/using-semaphore/tests/test-reports, /using-semaphore/tests/flaky-tests,
/using-semaphore/tests/markdown-reports, /using-semaphore/jobs,
/using-semaphore/self-hosted, /using-semaphore/tasks,
/using-semaphore/insights, /using-semaphore/org-health,
/using-semaphore/monorepo, /using-semaphore/cache,
/using-semaphore/containers/docker, /using-semaphore/billing,
/getting-started/about-semaphore, /getting-started/faq,
/getting-started/migration-overview, /sitemap.xml (2026-11-19).
