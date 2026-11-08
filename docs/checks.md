# Check contracts

Every Master CI job documents what it inspects, what it does not inspect,
what makes it fail, its known false positives, how to disable it and the
approximate runner time it adds. Times are for `ubuntu-latest` on a small
repository; large repositories scale with checkout and install size.

Job-level inputs below are `workflow_call` inputs on `master-ci.yml`.
Profile presets (quick / standard / strict / custom) only change the
*enforcement* of gates listed here; they never add or remove jobs.

## `meta` - input validation, profile resolution and helper repo lookup

- **Inspects:** the `profile`, `select` and `template_*` inputs;
  `GITHUB_WORKFLOW_REF` to auto-detect the helper repo on forks.
- **Validates:** `template_repo` must look like `owner/repo`, `template_ref`
  must contain only safe ref characters (no leading dash, no `..`),
  `profile` must be one of the four presets, every `run_*` stack toggle
  must be `auto`, `true` or `false`, and every `select` token must be a
  known job name (unknown names fail the run - a typo can never silently
  skip a check). Mutable helper refs (`main`/`master`) print a pinning
  warning. Invalid input fails `meta`, and every job that consumes helpers
  is gated on `meta` succeeding - so an untrusted ref can never reach a
  checkout.
- **Does not inspect:** your code.
- **Fails when:** an input fails validation. (The lookup itself can no
  longer fail: the built-in fallback covers it.)
- **False positives:** none.
- **Disable:** not disableable; it produces settings every job reads.
- **Time:** under 10 seconds.

## `changes` - path filtering

- **Inspects:** the git diff of the triggering event against a filter table
  (code, node, python, rust, go, java, cpp, csharp, ruby, php, docs,
  workflows, docker globs). Generated and vendored trees (`dist`, `build`,
  `target`, `vendor`, `coverage`, `node_modules`, `bin`, `obj`) are
  excluded, so rebuilding an artifact never triggers a stack run;
  `fixtures/` is excluded from the Node stack only.
- **Does not inspect:** file contents beyond paths; symlink-heavy trees can
  over-match globs.
- **Fails when:** git operations fail (broken repository).
- **False positives:** rename/delete detection needs full history
  (`fetch-depth: 0`, already set).
- **Tested:** `tests/test_path_filters.py` extracts the live filter table
  from the workflow and pins 140+ synthetic layouts with exact expected
  stacks, plus picomatch-semantics translator checks and output wiring.
- **Disable:** force every stack on with `run_node/run_python/run_rust/
  run_go/run_java/run_cpp/run_csharp/run_ruby/run_php/run_docker: 'true'`,
  or pick individual jobs with `select`; the `changes` job itself always
  runs (downstream gating depends on it).
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
  the slopsquatting guard; manifests read include `requirements*.txt`,
  pyproject `[project] dependencies`, PEP 735 `[dependency-groups]` and
  `[project.optional-dependencies]`, plus `package.json`), suppression
  abuse, code smells, placeholder credentials, UI slop, prose style and
  provenance.
- **Does not inspect:** authorship, commit metadata, or anything outside
  tracked text files; it never claims code *is* machine-written.
- **Reports:** a slop score 0-100 per run (0.6x worst file + 0.4x mean file
  deficit, grades A-F, verdicts CLEAN / SUSPICIOUS / INFLATED / CRITICAL),
  a "fix these first" brief, per-rule totals, and SARIF uploaded to the
  Security tab (category `ai-integrity`).
- **Fails when:** the **blocking** score exceeds `ai_slop_max_score`, or
  the project slop score exceeds `ai_slop_max_deficit` (default 30, the
  CLEAN/SUSPICIOUS boundary). The gate is armed by the profile: `standard`
  and `strict` enforce it, `quick` reports only, `custom` follows the raw
  `fail_on_ai_slop` input. Blocking signals are the deterministic content
  checks; `strict` additionally passes `--enforce-bloat`, escalating the
  structural-budget family (`bloat-long-file`, `bloat-param-count`,
  `bloat-comment-ratio`, `bloat-banner-comments`, `god-function`,
  `deep-nesting`, `chained-dict-get`) from advisory to blocking - file
  size, parameter and comment budgets fail the PR outright. `strict`
  also tightens the caps to `ai_slop_max=4` / ceiling `20`.
- **False positives:** domain vocabulary overlapping the phrase list;
  abstract methods that raise NotImplementedError; intentional emoji.
  Tests asserting via a raises-style context (`with pytest.raises(...)`,
  `self.assertRaises(...)`) count as assertions, not empty tests.
  Opt out per path or per check with a committed `.ai-integrity-ignore`
  (glob lines, `check:<id>` lines, `check:<id>:<glob>` for a scoped
  disable), or record existing findings with `--write-baseline` and run
  with `--baseline` so only new slop appears.
- **Bloat budget (advisory):** the anti-bloatware guard for machine-written
  padding - code that does 100 lines of work in 300, aligned with
  scanaislop.com's quality bar. `bloat-long-file` (over 400 non-blank
  lines, test files exempt), `god-function` (a function over 80 lines,
  enforced across every language family: Python through its AST, brace
  languages through a conservative scanner, Ruby through def/end pairing),
  `deep-nesting` (over 5 levels in every family), `bloat-param-count`
  (over 6 parameters), `bloat-comment-ratio` (over half the file is
  comments in substantial files) and `bloat-banner-comments` (decorative
  banner blocks bunched together - spread-out section dividers are
  navigation, not padding). Every bloat check is structurally advisory:
  `ADVISORY_ONLY_CHECKS` forces the flag, so no call site can make one
  fail a build.
- **Structural correctness (v1.11):** the scanner and AST views obey
  real language tokenization. Block comments (`/* */`, Rust/Swift nested,
  Ruby `=begin/=end` pods) are blanked with a string-aware tokenizer
  before brace analysis, so braces inside comments can no longer merge
  or swallow function blocks. Parameter counting tracks generic angle
  brackets (`Map<String, Pair<A, B>>`), Rust `->` arrows and JS
  destructuring, so signature commas count parameters, not type
  arguments. Every function over a budget is reported in file order -
  not just the worst one per file. Python nesting counts `match`,
  `async for` and `async with`, and nested `def`/lambda bodies are
  measured on their own instead of contaminating the enclosing
  function's depth.
- **Workspace discovery (v1.11):** `scripts/discover_projects.py`
  inventories independent projects per family (root manifests with
  workspace semantics - npm/pnpm/yarn workspaces, `go.work`, Cargo
  `[workspace]` - collapse to the root; every other manifest directory
  is its own project). The Node jobs run install, lint, type-check and
  tests per discovered project, and `--fail-empty` turns a
  triggered-but-empty family into a loud failure instead of a green
  no-op. Go, JVM, .NET, Ruby and PHP jobs still analyze root manifests
  only, but emit a `::warning::` listing nested manifests they skip, so
  no stack can silently do nothing.
- **Pinned analyzers (v1.11):** `configs/tool-versions.yml` pins every
  analyzer the template installs (ruff, mypy, vulture, lizard, pytest,
  yamllint, semgrep, Biome, depcheck, markdownlint-cli2, jscpd);
  `scripts/tool_versions.py` resolves the pins and errors on any
  unpinned tool name. A new analyzer release can never change CI
  behavior silently - bumps are reviewable PRs, and project-local tool
  installs always win over template fallbacks.
- **Agent-tell checks (advisory):** `chained-dict-get` flags chained
  `.get(..., {})` lookups in Python - a brittle agent shortcut that hides
  missing-data cases; normalize at the boundary instead. The design-sign
  checks port tenex.studio's "8 signs of AI slop" and noqode's "design IA
  générique" into deterministic rules for frontend files:
  `ui-eyebrow-label` (eyebrow/overline/kicker labels), `ui-italic-serif-hero`
  (italic word inside a headline), `ui-fake-stats` (unsourced 10,000+
  figures), `ui-generic-testimonial` (first name + role only),
  `ui-not-just-x` ("not just X, it's Y"), `ui-triple-slogan` ("Simple.
  Fast. Efficient."), `ui-glass-glow` (2+ backdrop-blur effects per file)
  and `ui-decorative-numbering` (3+ decorative 01/02/03 markers).
- **Round-2 scanaislop adoption (advisory):** the second tranche of
  scanaislop.com's 58-pattern catalog plus the comment doctrine from its
  writing-comments guide. Dead residue: `unreachable-code` (statements
  after return/raise/break/continue), `constant-condition` (`if True:`,
  `while False:` - `while True:` stays legal), `duplicate-import` (two
  top-level imports of one module). Pythonic-shape debts:
  `range-len-loop` (`for i in range(len(x))`), `isinstance-ladder` and
  `repetitive-dispatch` (four-plus branches on one subject),
  `redundant-try-catch` (a handler that only rethrows), `silent-recovery`
  (logs without the caught error and carries on). Test integrity:
  `tautological-test` (`assert True`, `assert x == x` - test files only).
  Naming: `generic-name` (top-level data/result/value/temp/obj/...).
  Commentary: `meta-comment` (task-history narration), `prose-block`
  (five-plus consecutive narrating comments), `hardcoded-url`
  (localhost/staging/internal URLs baked into non-test source). The
  TypeScript escape hatch covers `as any` and `as unknown as` via
  `ts-suppression`.
- **Round-3 scanaislop adoption (advisory):** the third tranche from
  scanaislop.com's pattern catalog. Wrappers and bodies: `thin-wrapper`
  (a function whose body only forwards its arguments unchanged - Python
  AST-verified, brace families via a conservative scanner; decorated
  surfaces and dunders are exempt) and `empty-function` (a named
  brace-family function with a literally empty body; documented no-ops
  are exempt). Environment leakage: `hardcoded-id` (a tenant/project/
  account identifier or provider-shaped token - Stripe-style prefixes,
  UUIDs on id-named bindings - baked into non-test source; placeholders
  and digit-only strings are filtered). Invented suppressions:
  `unknown-directive` (a comment that suppresses in the name of a lint
  tool the repo does not configure, or an inline `master-ci-ignore` -
  the real opt-out is the ignore file, never an inline comment; tools
  count as configured when a root config file or the manifests/
  workflows name them). The doc phrase table also grew the
  marketing-catchphrase set from noqode's copy signs: urgency filler
  ("look no" + "further"), power verbs paired with "your", the
  "not just X" pivot, and hustle-culture scene setting.
- **Scoring refinements (scanaislop-aligned):** advisory pressure is
  tier-weighted - maintainability debts (bloat, god-function, deep
  nesting, dispatch ladders) press at 0.75x, mechanical residue
  (phantom imports, dead residue, debug prints) at 0.5x, style and UI
  findings at 0.5x, traceability notes at 0.25x - and every check's
  contribution saturates at 40 points per file (`MAX_PER_RULE`), so one
  noisy rule can never dominate a deficit. Blocking weight is never
  tiered: strict signals stay strict.
- **Disable:** `profile: quick` keeps the whole job advisory; with
  `profile: custom` set `fail_on_ai_slop: false` explicitly. Provenance
  and test inventory can be turned off with `--no-provenance`; the
  CLI-level `--enforce-bloat` is what the strict profile passes.
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

## `go-lint` / `go-test` - Go stack

- **Inspects:** the module rooted at the repository `go.mod`: gofmt
  formatting, `go vet`, golangci-lint, `go build ./...`, and the test suite
  under the race detector (`go test -race -shuffle=on ./...`).
- **Does not inspect:** nested modules (root manifest only), vendored
  trees (`vendor/` is excluded from path filtering like every stack).
- **Fails when:** formatting drift, vet findings, golangci-lint findings,
  build or test failures.
- **False positives:** golangci-lint defaults on legacy code - add a
  `.golangci.yml` to tune; repos with no tests still pass.
- **Disable:** `run_go: 'false'`; force with `run_go: 'true'`, pin the
  toolchain with `go_version` (default `stable`).
- **Time:** 1-5 minutes warm (module cache), 5-10 cold.

## `java` - JVM stack (Java, Kotlin, Scala via Maven or Gradle)

- **Inspects:** the root build (`pom.xml`/`mvnw` or `build.gradle*`/
  `settings.gradle*`): Gradle wrapper checksums are validated against the
  recorded distributions before any code runs; Maven projects run the full
  `mvn verify` lifecycle (compile, unit + integration tests, packaging),
  Gradle projects run `gradle build` (compile + test).
- **Does not inspect:** nested modules (root build only), `build/` and
  `target/` outputs (excluded from path filtering).
- **Fails when:** wrapper checksum mismatch, compile, test, or packaging
  failures - whatever the build tool's own lifecycle fails on.
- **False positives:** none beyond the build tool itself; lint plugins
  (Checkstyle, SpotBugs, ktlint) ride the lifecycle you already configured.
- **Disable:** `run_java: 'false'`; force with `run_java: 'true'`, pin the
  JDK with `java_version` (default `21`, Temurin).
- **Time:** 3-8 minutes warm (dependency cache), 10+ cold.

## `cpp` - C/C++ stack

- **Inspects:** C/C++ sources with cppcheck (warning, style, performance -
  advisory in every profile), formatting with clang-format (enforced only
  when the repo commits a `.clang-format`), then builds with CMake
  (`-DCMAKE_BUILD_TYPE=Release`) and runs CTest when tests are registered,
  or `make` for Makefile projects.
- **Does not inspect:** vendored trees; a bare `Makefile` without C/C++
  sources does not trigger this stack (non-C Makefile projects stay
  untouched).
- **Fails when:** clang-format drift (only with `.clang-format`), build or
  ctest failures. cppcheck findings are reported, never enforced.
- **False positives:** cppcheck style rules on legacy code - tune with
  inline suppressions (`--inline-suppr` is enabled).
- **Disable:** `run_cpp: 'false'`; force with `run_cpp: 'true'`.
- **Time:** 2-10 minutes depending on project size.

## `csharp` - .NET stack (C#, F#)

- **Inspects:** the root solution or project (`*.sln`, `*.slnx`,
  `*.csproj`, `*.fsproj`): `dotnet build -c Release` (implicit restore),
  `dotnet test -c Release --no-build`, and `dotnet format
  --verify-no-changes` when the repo commits an `.editorconfig` (advisory
  in every profile).
- **Does not inspect:** `bin/` and `obj/` outputs (excluded), private
  NuGet feeds (no feed credentials are configured).
- **Fails when:** build or test failures.
- **False positives:** `dotnet format` on repos whose `.editorconfig`
  predates their formatting habits - it is advisory, tune by fixing or
  updating `.editorconfig`.
- **Disable:** `run_csharp: 'false'`; force with `run_csharp: 'true'`,
  pin the SDK with `dotnet_version` (default `10.0`; a committed
  `global.json` always wins).
- **Time:** 2-6 minutes.

## `ruby` - Ruby stack

- **Inspects:** the root project (`Gemfile`, `*.gemspec`, `Rakefile`):
  bundle install with dependency caching (Gemfile projects), RuboCop
  (advisory), bundler-audit against the Ruby Advisory Database (advisory),
  and the test suite - `rspec` when `spec/` exists, `rake test` when
  `test/` exists.
- **Does not inspect:** vendored gems (`vendor/` excluded), gems without a
  `Gemfile.lock` (bundler-audit needs one).
- **Fails when:** rspec or rake test failures.
- **False positives:** RuboCop defaults on legacy code (advisory); rake
  projects whose `test` task name differs - the task is looked up, not
  assumed.
- **Disable:** `run_ruby: 'false'`; force with `run_ruby: 'true'`, pin the
  interpreter with `ruby_version` (default `3.4`).
- **Time:** 2-5 minutes.

## `php` - PHP stack

- **Inspects:** the root `composer.json`: `composer install`
  (lockfile-faithful - it never rewrites `composer.lock`), `php -l` over
  every tracked PHP file, PHPStan (enforced only when the repo commits a
  `phpstan.neon`), and tests via the `composer test` script or PHPUnit
  (`phpunit.xml`).
- **Does not inspect:** `vendor/` (excluded from path filtering and from
  the syntax sweep).
- **Fails when:** syntax errors, install failures, PHPStan (when opted
  in), PHPUnit failures.
- **False positives:** PHPStan on untyped legacy code - the analysis runs
  only when the project itself ships a config, at the level the project
  chose.
- **Disable:** `run_php: 'false'`; force with `run_php: 'true'`, pin the
  runtime with `php_version` (default `8.4`).
- **Time:** 2-5 minutes.

## `docker` - Docker stack

- **Inspects:** every tracked `Dockerfile*` with hadolint (advisory; the
  binary is checksum-verified at download; commit `.hadolint.yaml` to
  tune), builds the first tracked Dockerfile (the root one in the standard
  layout) with plain `docker build`, then scans that exact image with
  Trivy (vulns + secrets, HIGH/CRITICAL, SARIF to the Security tab under
  `trivy-image`).
- **Does not inspect:** docker-compose files beyond path detection; image
  push (that is release-docker.yml's job).
- **Fails when:** the build fails; Trivy HIGH/CRITICAL only when enforced
  (strict profile, same flag as the filesystem scan).
- **False positives:** hadolint DL rules on legacy Dockerfiles (advisory);
  unfixed-vuln noise (ignored).
- **Disable:** `run_docker: 'false'`; force with `run_docker: 'true'`.
- **Time:** 2-15 minutes depending on base images (Docker layer cache
  helps).

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
  repository dependency graph is disabled the job probes first (with the
  automatic `GITHUB_TOKEN`, so the probe is authenticated on private repos
  too) and degrades to a warning instead of a red infrastructure failure.
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

`selftest.yml` is the routine integration test and is permanently green.
It calls the reusable workflow two ways:

1. **happy-path** - quick profile; `verify` asserts job success and
   `verdict=passed`. Runs on every push and PR.
2. **select-subset** - `select: secrets`; most jobs skip cleanly and the
   aggregate gate still reports `verdict=passed`, proving job selection
   works end-to-end. Runs on every push and PR.

A GitHub limitation shapes the rest of the story: a job that calls a
reusable workflow has no `continue-on-error`, so a failing call reddens
the calling run no matter what - and red in CI may only ever mean broken.
The five designed-failure calls therefore live in their own file,
`selftest-guardrails.yml`:

1. **enforced-failure** - a Semgrep pack that cannot exist.
2. **input-validation** - an unsafe `template_ref` (path traversal).
3. **input-validation-stack-toggle** - `run_python: 'ture'`, a typo that
   must fail meta instead of silently skipping the Python stack.
4. **input-validation-select** - `select: 'secrets,bogus-job'`, an unknown
   token that must fail meta instead of silently skipping a check.
5. **input-validation-template-ref** - a foreign helper repo with an empty
   `template_ref`; a helper cannot infer a ref safely for callers other
   than the template itself, so this must fail meta (the fail-closed rule
   introduced in v1.8.6).

`selftest-guardrails.yml` is dispatch-only: it never runs on push or
schedule by itself, its run name says the red is the expected outcome, and
its `verify` job asserts every one of the five reported result=failure and
`verdict=failed` - if any guardrail stops firing, that red is a real
defect signal, not the designed red.

That designed red is exercised ROUTINELY by `selftest-negative.yml`, the
**expected-failure watchdog**: it dispatches the guardrails suite (weekly
schedule, plus pushes that touch the reusable workflow, the selftest, the
watchdog or the test bed), waits for the child run, and asserts the red is
exactly the designed red - the five negative calls failed on their inner
jobs and verify succeeded. A green child run or a mis-shaped outcome fails
the watchdog.

A regression that makes the pipeline pass when it should fail - or fail for
the wrong reason - turns the selftest or the watchdog red.

## `benchmark` - the hosted performance history

`benchmark.yml` runs `scripts/benchmark.py` on a deterministic corpus
(Sundays 04:00 UTC and on demand) and publishes the numbers three ways:
the step summary, a 90-day artifact, and - the durable one - one JSON line
per run appended to `benchmarks/history.jsonl` on the dedicated
`benchmarks` branch. The branch is unprotected on purpose: main requires
the `ci-success` status check, which a GITHUB_TOKEN push cannot satisfy,
and the project rule "automation never pushes main" stands. The history
survives artifact expiry, is diffable week over week with plain git, and
every line is schema-checked by the pytest suite. It is report-only:
shared runners are too noisy to gate on timings, but a week-over-week
jump is immediately visible in the history diff.

## `heal` and `audit` - the reactive loop, the fix engine and the whole-repo sweep

`audit.yml` is the whole-repository deep audit: scheduled (Saturdays 06:00
UTC) and on demand, never on push or PR. It re-examines what the
incremental gates only sample - full git history (gitleaks), every file in
the tree (integrity engine), every dependency manifest (osv-scanner), Go
modules (govulncheck), SQL sources (sqlfluff) - and appends the result to
one tracking issue labeled `audit`. Every scan is `continue-on-error` by
design: findings inform, they never gate.

What happens to the findings: everything with a deterministic fix is
handled by the **fix engine** (`scripts/fix_engine.py`), the single fix
policy shared by `autofix.yml` (weekly drift sweep plus an automatic
response that runs right after every Deep Audit) and `heal.yml` (reactive:
when CI fails on the default branch it applies the same engine and opens
or updates the `ci/autofix` PR). The engine applies two tiers:

- **safe** - cannot change program behavior: rustfmt, gofmt,
  `ruff format`, Biome safe fixes, shfmt, markdownlint `--fix`, and a
  generic normalizer (trailing whitespace, final newline; Markdown
  hard line breaks are preserved, binaries and vendored trees untouched).
- **lint** - tool-verified transforms: `ruff check --fix`, `go mod tidy`,
  `sqlfluff fix`, and `semgrep --autofix` (only rules that ship a
  machine-applicable fix).

The engine emits a manifest, a step-summary table and the PR body from one
run, so every fix PR documents exactly which tool changed what. Missing
tools are recorded as `skipped:tool-missing`, never fatal.

Never auto-fixed, by design: secrets (rotate them), dependency
vulnerabilities (upgrade decisions), AI-integrity prose findings (rewriting
meaning is not mechanical), and any finding whose tool ships no machine
fix. Those stay in the audit tracking issue where a human owns them.

Circuit breakers: heal reacts to default-branch failures only, one loop
runs at a time (fixed concurrency groups), and it stops while a fix PR is
already open - a failing fix PR needs a human, not another iteration.
Nothing is ever pushed to a branch; the PR is the whole change, and
closing it is the rollback.
