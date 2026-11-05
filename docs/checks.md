# Check contracts

Every Master CI job documents what it inspects, what it does not inspect,
what makes it fail, its known false positives, how to disable it and the
approximate runner time it adds. Times are for `ubuntu-latest` on a small
repository; large repositories scale with checkout and install size.

Job-level inputs below are `workflow_call` inputs on `master-ci.yml`.
Profile presets (quick / standard / strict / custom) only change the
*enforcement* of gates listed here; they never add or remove jobs.

## `meta` - input validation, profile resolution and helper repo lookup

- **Inspects:** the `profile` and `template_*` inputs; `GITHUB_WORKFLOW_REF`
  to auto-detect the helper repo on forks.
- **Validates:** `template_repo` must look like `owner/repo`, `template_ref`
  must contain only safe ref characters (no leading dash, no `..`), and
  `profile` must be one of the four presets. Invalid input fails `meta`, and
  every job that consumes helpers is gated on `meta` succeeding - so an
  untrusted ref can never reach a checkout.
- **Does not inspect:** your code.
- **Fails when:** an input fails validation. (The lookup itself can no
  longer fail: the built-in fallback covers it.)
- **False positives:** none.
- **Disable:** not disableable; it produces settings every job reads.
- **Time:** under 10 seconds.

## `changes` - path filtering

- **Inspects:** the git diff of the triggering event against a filter table
  (code, node, python, rust, docs, workflows, docker globs). Generated and
  vendored trees (`dist`, `build`, `target`, `vendor`, `coverage`,
  `node_modules`) are excluded, so rebuilding an artifact never triggers a
  stack run; `fixtures/` is excluded from the Node stack only.
- **Does not inspect:** file contents beyond paths; symlink-heavy trees can
  over-match globs.
- **Fails when:** git operations fail (broken repository).
- **False positives:** rename/delete detection needs full history
  (`fetch-depth: 0`, already set).
- **Tested:** `tests/test_path_filters.py` extracts the live filter table
  from the workflow and pins 85+ synthetic layouts with exact expected
  stacks, plus picomatch-semantics translator checks and output wiring.
- **Disable:** force every stack on with `run_node/run_python/run_rust:
  'true'`; the `changes` job itself always runs (downstream gating depends
  on it).
- **Time:** 5-20 seconds (dominated by full checkout).

## `hygiene` - workflows, YAML, shell, whitespace, duplication

- **Inspects:** `.github/workflows/*.y*ml` (actionlint, zizmor), all YAML
  (yamllint), `.editorconfig` compliance, `*.sh` (shellcheck), files over
  2 MB, duplicated code across TypeScript/JavaScript/Python/Go/Java (jscpd,
  only when code changed).
- **Does not inspect:** Markdown (see `docs`), secrets (see `secrets`),
  runtime behavior, fixtures directories (excluded from duplication).
- **Fails when:** actionlint errors, yamllint errors, editorconfig
  violations, shellcheck errors, jscpd above
  `duplication_threshold_pct` (enforced in standard/strict/custom;
  advisory in quick). zizmor findings are reported as step annotations
  (not the Security tab - that would need `security-events: write` on
  this job).
- **False positives:** YAML anchors flagged by yamllint's default rules;
  jscpd counting idiomatic boilerplate (raise `duplication_threshold_pct`
  or ignore paths via a committed `.jscpd.json`).
- **Disable:** split out per-step by committing your own configs
  (`.editorconfig`, yamllint config); duplication via `profile: quick`.
- **Time:** 2-4 minutes (jscpd and zizmor dominate).

## `secrets` - full-history leak detection

- **Inspects:** every commit in git history with gitleaks' ruleset.
- **Does not inspect:** content not in git history (artifacts, caches).
- **Fails when:** a secret-like match is not in an allowlist.
- **False positives:** test fixtures, example keys, structured IDs that
  resemble tokens. Add committed `.gitleaks.toml` allows.
- **Disable:** nothing can disable it in the reusable workflow; fix the
  leak or allowlist the pattern.
- **Time:** 10-60 seconds depending on history length.

## `ai-integrity` - slop score, structural rules, provenance

- **Inspects:** code (language-aware comment extraction plus structural
  checks per family), documents (prose rhythm, residue) and frontend files
  (UI slop). Blocking-grade rules catch deterministic content defects:
  elided code, placeholder implementations, stub functions, empty tests,
  bare/pass-only exception handlers, empty catches and rescues, ignored Go
  errors, committed debuggers, Rust `todo!()`, chatbot residue in comments.
  Advisory rules cover dependency hygiene (phantom and undeclared imports -
  the slopsquatting guard), suppression abuse, code smells, placeholder
  credentials, UI slop, prose style and provenance.
- **Does not inspect:** authorship, commit metadata, or anything outside
  tracked text files; it never claims code *is* machine-written.
- **Reports:** a slop score 0-100 per run (0.6x worst file + 0.4x mean file
  deficit, grades A-F, verdicts CLEAN / SUSPICIOUS / INFLATED / CRITICAL),
  a "fix these first" brief, per-rule totals, and SARIF uploaded to the
  Security tab (category `ai-integrity`).
- **Fails when:** only the **blocking** score exceeds `ai_slop_max_score`
  AND `fail_on_ai_slop: true`. Blocking signals are the deterministic
  content checks; everything stylistic is advisory.
- **False positives:** domain vocabulary overlapping the phrase list;
  abstract methods that raise NotImplementedError; intentional emoji.
  Opt out per path or per check with a committed `.ai-integrity-ignore`
  (glob lines, `check:<id>` lines), or record existing findings with
  `--write-baseline` and run with `--baseline` so only new slop appears.
- **Disable:** `fail_on_ai_slop` defaults to `false` - the job reports,
  annotates and uploads SARIF but never gates. Provenance and test
  inventory can be turned off with `--no-provenance`.
- **Time:** 10-60 seconds.

## `docs` - markdown lint

- **Inspects:** `*.md` with markdownlint-cli2 and the default config
  (copied into your repo if absent). Fixtures directories are excluded.
- **Does not inspect:** rendered HTML, links (no link checker yet).
- **Fails when:** markdownlint rules fail.
- **False positives:** house styles that conflict with the default config;
  commit a `.markdownlint-cli2.jsonc` to override.
- **Disable:** skip by not touching `*.md`; the job is path-filtered.
- **Time:** 30-60 seconds.

## `node-lint` / `node-test` - Node & TypeScript stack

- **Inspects:** `package.json` at the repository root, TS/JS sources. Lint runs
  your `lint` script if present, else Biome; `tsc --noEmit` when `tsconfig.json`
  exists; depcheck flags unused deps (advisory). Tests run your `test`
  script via npm/pnpm/yarn.
- **Does not inspect:** repositories without a root `package.json` (the Node
  stack covers root-manifest projects; subdirectory-only manifests are on the
  roadmap), lockfile-only changes that don't match the filters, browsers,
  native builds.
- **Fails when:** lint/type errors, test failures. depcheck never fails.
- **False positives:** Biome's defaults differing from your house style
  (commit a `biome.json`); depcheck's dynamic-require blindness.
- **Disable:** `run_node: 'false'`.
- **Time:** 2-6 minutes per job (install-dominated, cached by lockfile).

## `python` - Python stack

- **Inspects:** `*.py` and Python manifests. ruff lint + format, mypy
  (with `--ignore-missing-imports`), vulture (advisory), lizard
  cyclomatic complexity gate (enforced in standard/strict/custom, advisory
  in quick), pytest when tests exist.
- **Does not inspect:** runtime behavior beyond tests, notebook files.
- **Fails when:** ruff/mypy/pytest errors, or complexity above
  `complexity_max` when enforced.
- **False positives:** vulture on dynamically-dispatched code; mypy on
  untyped third parties (mitigated by the flag above).
- **Disable:** `run_python: 'false'`.
- **Time:** 2-5 minutes.

## `rust` - Rust stack

- **Inspects:** the whole cargo workspace: rustfmt, clippy
  (`-D warnings`), cargo-deny (advisories, licenses, sources), tests.
- **Does not inspect:** benches beyond clippy's `--all-targets`,
  no_std targets, MSRV matrix (single toolchain input).
- **Fails when:** fmt/clippy/deny/test failures.
- **False positives:** clippy lints on generated code (add `#[allow]`
  attributes); cargo-deny on unpublishable dev deps.
- **Disable:** `run_rust: 'false'`.
- **Time:** 5-20 minutes cold, 1-5 warm (shared `rust-cache`).

## `security` - Semgrep, Trivy, OSV

- **Inspects:** source with Semgrep packs (default
  `p/default p/security-audit p/secrets`), filesystem with Trivy
  (vulns + secrets, HIGH/CRITICAL), dependency manifests with
  osv-scanner. SARIF goes to the Security tab per tool category.
- **Does not inspect:** running containers, live endpoints, SBOM
  provenance.
- **Fails when:** Semgrep findings (any), Trivy HIGH/CRITICAL only when
  enforced (strict), OSV vulnerable deps when enforced (standard and
  above; advisory in quick).
- **False positives:** Semgrep's regex-level rules on test code
  (commit `.semgrepignore`); Trivy on unfixed-vuln noise
  (`ignore-unfixed` already set).
- **Disable:** trim `semgrep_config`; set `profile: quick` for
  advisory-only OSV.
- **Time:** 2-5 minutes.

## `deps-review` - dependency & license review (PRs only)

- **Inspects:** the dependency-graph diff between the PR base and head.
- **Does not inspect:** anything without a PR event.
- **Fails when:** introduced vulnerabilities at `fail-on-severity`
  (critical in quick, high elsewhere), or incompatible license changes.
- **False positives:** transitive advisories you already mitigated.
- **Disable:** cannot be disabled per-run in the reusable workflow; if the
  repository dependency graph is disabled the job probes first and
  degrades to a warning instead of a red infrastructure failure.
- **Time:** 20-60 seconds.

## `scorecard` - OSSF Scorecard (pushes to default branch)

- **Inspects:** repository-level supply-chain posture (branch protection,
  pinned dependencies, security policy, ...).
- **Does not inspect:** code content.
- **Fails when:** never - it reports into the Security tab. Publishing
  results (badge/API) needs `id-token: write` on this job only; set
  `scorecard_publish: false` if your org forbids OIDC.
- **False positives:** checks that need settings visible to the token.
- **Disable:** out of your control in the reusable workflow; it only runs
  on pushes to the default branch.
- **Time:** 1-3 minutes.

## `dashboard` and `ci-success` - reporting and the single gate

- `dashboard` posts or updates one PR comment summarizing every job; needs
  `pull-requests: write`; PRs only.
- `ci-success` is the only check you should mark required. It fails when
  any needed job failed or was cancelled, and succeeds when jobs were
  merely skipped. It never runs tools itself.

## Profile presets

| Setting | quick | standard | strict | custom |
|---|---|---|---|---|
| Semgrep packs | `p/default` | input (3 packs) | input (3 packs) | input |
| OSV enforcement | advisory | **fail** | **fail** | **fail** |
| Trivy enforcement | advisory | advisory | **fail** | advisory |
| Duplication gate | advisory | **fail** | **fail** | **fail** |
| Complexity gate | advisory | **fail** | **fail** | **fail** |
| deps-review severity | critical | high | high | high |
| AI-integrity | advisory | advisory | advisory | advisory |

`custom` honors the raw `semgrep_config` input; profiles are presets over
the same knobs, not duplicated workflows. AI-integrity is advisory in every
profile: set `fail_on_ai_slop: true` explicitly if you accept the false
positive rate documented above.

## `selftest` - the template tests itself

`selftest.yml` calls the reusable workflow three ways on every push and PR:

1. **happy-path** - quick profile, Python stack only; the `verify` job
   asserts job success and `verdict=passed`.
2. **enforced-failure** - a Semgrep pack that cannot exist; continue-on-error
   keeps the run green while `verify` asserts `verdict=failed`, proving the
   aggregate gate really fails when a check fails.
3. **input-validation** - an unsafe `template_ref`; `verify` asserts
   `verdict=failed`, proving untrusted input is rejected before any checkout.

A regression that makes the pipeline pass when it should fail - or fail for
the wrong reason - turns the selftest red.
