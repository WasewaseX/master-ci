# master-ci

Reusable GitHub Actions CI for **Rust, Node/TypeScript, Python, Go, JVM
(Java/Kotlin), C/C++, .NET (C#/F#), Ruby, PHP and Docker**, with structural
AI-slop detection for **13+ languages**, OIDC-first AWS deployments and a
cosign-signed container release pipeline. One file per project gives you
100+ checks, path-aware skipping, aggressive caching, pretty reporting and
a set of automation loops that keep the whole thing updated.

![CI](https://github.com/WasewaseX/master-ci/actions/workflows/ci.yml/badge.svg)
![Selftest](https://github.com/WasewaseX/master-ci/actions/workflows/selftest.yml/badge.svg)
![Selftest Negative Harness](https://github.com/WasewaseX/master-ci/actions/workflows/selftest-negative.yml/badge.svg)
![Benchmark](https://github.com/WasewaseX/master-ci/actions/workflows/benchmark.yml/badge.svg)
![Nightly Deep Scan](https://github.com/WasewaseX/master-ci/actions/workflows/nightly.yml/badge.svg)

- Runs entirely on free `ubuntu-latest` runners for public repositories
- Detects which stacks changed, skips generated trees, skips everything else
- Pushes findings to the **Security** tab (code scanning) and inline PR annotations
- Aggregates into a single `ci-success` check you mark as required
- Least-privilege per job, no `secrets: inherit`, every action pinned by commit SHA
- Validates untrusted caller inputs before they can reach a checkout
- The template tests itself: path-filter regression suite, workflow contract
  tests, a six-path integration selftest and an expected-failure harness that
  runs the negative paths routinely
- Every check has a documented contract: [docs/checks.md](docs/checks.md)

## Setup: one line, any project

Create `.github/workflows/ci.yml` in any repository with this content:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  master:
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@v2
    permissions:
      contents: read
      security-events: write
      pull-requests: write
      id-token: write   # only for Scorecard publishing (see "Permissions")

  codeql:
    uses: WasewaseX/master-ci/.github/workflows/codeql.yml@v2
    permissions:
      contents: read
      security-events: write
```

CodeQL needs no `with:` block: `languages` defaults to `auto` and is
read from the manifests the repo already commits (v2.7). Pin dialects
explicitly only when you want to override detection.

Or fetch the ready-made caller with one shell line:

```bash
mkdir -p .github/workflows && curl -o .github/workflows/ci.yml https://raw.githubusercontent.com/WasewaseX/master-ci/main/examples/ci.yml
```

How the helpers find their way here: Master CI jobs fetch `scripts/` and
default configs from a helper repo (`template_repo`, at `template_ref`).
When you leave both unset, the workflow detects its own repository -
which is correct when you call it from the same repo or a fork, and wrong
for a foreign project. Since v1.7.0 meta probes for the helper module:
a foreign caller automatically falls back to `WasewaseX/master-ci` with a
warning, and an unresolvable setup fails `meta` with clear instructions.

Since v1.8.6 an empty `template_ref` resolves to the calling
repository's own commit - but only when the helpers live in that
repository (self or fork; meta probes for the helper module at that
exact SHA). For any foreign helper repo the empty ref fails `meta` with
pin instructions instead of guessing: `github.workflow_sha` is the
caller's commit even for cross-repo calls, so a silent fallback would
fetch a ref that does not exist there - or worse, a mutable one. The
[ci-canary](https://github.com/WasewaseX/ci-canary) consumer pins both,
which keeps its baseline reproducible across template releases:

```yaml
    with:
      template_repo: WasewaseX/master-ci
      template_ref: v2.12.0
```

### Required check

Reusable-workflow jobs surface in branch protection under the caller's
own job id: a job named `master` that calls master-ci.yml reports its
gate as `master / ci-success`, and a job named `codeql` calling
codeql.yml reports `codeql / analyze (python)` - the [ci-canary]
consumer's hosted runs show exactly these names. Set the full
`<job id> / <check>` string as the required status check (Settings >
Branches > Branch protection rules); a bare `ci-success` entry would
wait forever on a name that never reports.

`ci-success` aggregates the master-ci jobs only. CodeQL rides a separate
reusable workflow, so a branch protection rule on a `ci-success` check
alone cannot fail on a red CodeQL analysis (audit finding F). Two honest
shapes:

- **One required check (recommended):** call
  `WasewaseX/master-ci/.github/workflows/required-quality.yml@v2.12.0`
  instead of `master-ci.yml` and require **`required-quality-check / gate`**.
  The first segment is the job id the caller gives the call, so the
  example below names its job `required-quality-check`; a caller naming
  the job differently must require its own prefix (`<job id> / gate`).
  The aggregate calls master-ci.yml and codeql.yml itself and its gate is
  green only when both succeeded - a skipped or failed CodeQL run is a red
  gate, never a silent pass. Every master-ci input is forwarded 1:1, plus
  the CodeQL `languages`/`queries`/`config_file` inputs.

  ```yaml
  jobs:
    required-quality-check:
      uses: WasewaseX/master-ci/.github/workflows/required-quality.yml@v2.12.0
      permissions:
        contents: read
        security-events: write
        pull-requests: write
        id-token: write   # only for Scorecard publishing inside the template
  ```

- **Two required checks:** keep calling `master-ci.yml` and `codeql.yml`
  separately and require both `master / ci-success` **and**
  `codeql / analyze (<language>)` - the caller job ids of the
  quick-start example. The PR
  dashboard's Semantic analysis row shows which shape it detects in the
  caller's own workflow files - verified, not declared.

### Doctor: verify your setup offline

`scripts/doctor.py` checks the workflow you wrote against this
contract (pin style, the helper-pin pair, permissions, gate inputs)
without touching the network. Errors (a mutable `@main` ref,
`template_repo` without `template_ref`, missing
`security-events: write`, a non-integer `coverage_min`) exit 1;
warnings (a floating major tag, no pins on a foreign caller) exit 0;
an unreadable setup fails closed with exit 2:

```bash
pip install pyyaml
python scripts/doctor.py                       # reads .github/workflows
python scripts/doctor.py --json                # machine-readable
python scripts/doctor.py --workflows-dir path  # any checkout
```

## Versioning (pick one strategy)

| Reference | Behavior | Use when |
|---|---|---|
| `@v2` | Tracks the latest `v2.x` release tag | Default recommendation; Dependabot PRs keep it fresh |
| `@v1` | Tracks the latest `v1.x` release tag | The v1 line, frozen at v1.22.2; never jumps majors |
| `@vX.Y.Z` | Exact release | Reproducibility without surprises |
| `@<commit-sha>` | Immutable commit | Strict supply-chain posture (zizmor audit-clean) |
| `@main` | Live edge | Testing template changes before a release |

Releases are tagged `vX.Y.Z`; each major has its own moving tag (`v1`,
`v2`) updated on every release within that line, so `@v1` consumers are
never silently upgraded across a major. Copy
[`dependabot.yml`](.github/dependabot.yml) into your repo and Dependabot
opens update PRs for the `uses:` lines automatically - the self-update loop
works for consumers too.

## What runs, and when

| Job | Trigger | Purpose |
|---|---|---|
| `changes` | always | Path filtering for all downstream jobs |
| `meta` | always | Validates caller inputs, resolves helper repo and enforcement profile |
| `hygiene` | meta ok | Workflow lint, YAML, shell, whitespace, duplication |
| `secrets` | meta ok | Full-history secret scanning |
| `ai-integrity` | meta ok | AI slop + structural slop detection - blocking under `standard` (the default profile) and `strict`; advisory under `quick` or `custom` with `fail_on_ai_slop: false` |
| `docs` | docs changed | Markdown lint |
| `node-lint` | Node changed | Lint, format, types, unused deps |
| `node-test` | Node changed | Test suite |
| `python` | Python changed | Lint, format, types, dead code, complexity, tests |
| `rust` | Rust changed | Format, clippy, advisories, licenses, tests |
| `go-lint` | Go changed | gofmt, go vet, golangci-lint |
| `go-test` | Go changed | Build + race-tested, shuffled test suite |
| `java` | JVM changed | Gradle wrapper validation, Maven/Gradle build + verify (Java, Kotlin, Scala) |
| `cpp` | C/C++ changed | cppcheck, opt-in clang-format, CMake/Make build + ctest |
| `csharp` | .NET changed | Release build, tests, advisory `dotnet format` (C#, F#) |
| `ruby` | Ruby changed | bundler install, advisory RuboCop + bundler-audit, rake/rspec tests |
| `php` | PHP changed | composer install, `php -l`, opt-in PHPStan, PHPUnit/composer tests |
| `docker` | Docker changed | hadolint, image build, image-level Trivy scan |
| `security` | meta ok | Semgrep, Trivy, OSV scanning |
| `deps-review` | PRs | Dependency and license review |
| `scorecard` | pushes to default branch, unless `scorecard_publish: false` | OSSF Scorecard |
| `coverage-gate` | `coverage_min > 0` and a producing stack ran | Measured coverage floors, overall and on the added lines |
| `new-code-gate` | `diff_base` set and `new_code_gates` true | Dimension ceilings on the merge-base diff: blocking findings, cognitive, cyclomatic |
| `quality-ratchet` | a `.quality-baseline.json` is committed (or `ratchet: true`) | The committed floor no check may regress from |
| `dashboard` | PRs | Results table comment on the PR |
| `ci-success` | always | Aggregate gate (exposes a `verdict` output) |

## The check catalog (100+ checks)

Repo hygiene:

| # | Check | Tool |
|---|---|---|
| 1 | Workflow syntax and semantics | actionlint |
| 2 | Workflow security audit | zizmor |
| 3 | YAML lint across the repo | yamllint |
| 4 | Editorconfig compliance | editorconfig-checker |
| 5 | Shell script lint | shellcheck |
| 6 | Oversized file audit (>2MB) | find-based audit |
| 7 | Duplicated code | jscpd |
| 8 | Markdown lint | markdownlint-cli2 |

Secrets and supply chain:

| # | Check | Tool |
|---|---|---|
| 9 | Secret leaks in full git history | gitleaks |
| 10 | Static security rules | Semgrep `p/default` |
| 11 | Security audit pack | Semgrep `p/security-audit` |
| 12 | Secrets pack | Semgrep `p/secrets` |
| 13 | Filesystem vulnerabilities + secrets | Trivy |
| 14 | Known vulnerabilities in dependencies | osv-scanner |
| 15 | PR dependency and license review | dependency-review-action |
| 16 | Repository security posture | OSSF Scorecard |
| 17 | Rust dependency advisories | cargo-deny |
| 18 | Rust license compliance | cargo-deny |
| 19 | Rust source registry policy | cargo-deny |

Deep analysis (CodeQL, separate workflow):

| # | Check | Tool |
|---|---|---|
| 20 | Extended security queries | CodeQL `security-extended` |
| 21 | Security + quality + maintainability | CodeQL `security-and-quality` |

Node / TypeScript:

| # | Check | Tool |
|---|---|---|
| 22 | Lint and format | Biome |
| 23 | Project lint script | `npm run lint` |
| 24 | Type safety | tsc --noEmit |
| 25 | Unused dependencies | depcheck (advisory) |
| 26 | Test suite | npm / pnpm / yarn test |

Python:

| # | Check | Tool |
|---|---|---|
| 27 | Lint | ruff check |
| 28 | Format drift | ruff format --check |
| 29 | Static types | mypy |
| 30 | Dead code | vulture (advisory) |
| 31 | Cyclomatic complexity gate | lizard |
| 32 | Test suite | pytest |

Rust:

| # | Check | Tool |
|---|---|---|
| 33 | Format drift | rustfmt --check |
| 34 | Lint gate | clippy -D warnings |
| 35 | Tests | cargo test |

Go:

| # | Check | Tool |
|---|---|---|
| 36 | Format drift | gofmt |
| 37 | Static analysis | go vet |
| 38 | Lint gate | golangci-lint |
| 39 | Build | go build ./... |
| 40 | Test suite (race detector, shuffled) | go test -race |

JVM (Java, Kotlin, Scala via Maven or Gradle):

| # | Check | Tool |
|---|---|---|
| 41 | Gradle wrapper checksum validation | gradle/actions/wrapper-validation |
| 42 | Full Maven lifecycle (compile, test, package) | `mvn verify` |
| 43 | Gradle build (compile + test) | `gradle build` |

C/C++:

| # | Check | Tool |
|---|---|---|
| 44 | Static analysis | cppcheck (advisory) |
| 45 | Format drift (only when `.clang-format` exists) | clang-format --dry-run -Werror |
| 46 | Build + test suite | CMake + CTest, or Make |

.NET (C#, F#):

| # | Check | Tool |
|---|---|---|
| 47 | Release build | dotnet build -c Release |
| 48 | Test suite | dotnet test |
| 49 | Format drift (only when `.editorconfig` exists) | dotnet format (advisory) |

Ruby:

| # | Check | Tool |
|---|---|---|
| 50 | Lint | RuboCop (advisory) |
| 51 | Dependency advisories | bundler-audit (advisory) |
| 52 | Test suite | rspec or `rake test` |

PHP:

| # | Check | Tool |
|---|---|---|
| 53 | Syntax check over every tracked file | php -l |
| 54 | Static analysis (only when `phpstan.neon` exists) | PHPStan |
| 55 | Test suite | composer test script or PHPUnit |

Docker:

| # | Check | Tool |
|---|---|---|
| 56 | Dockerfile lint | hadolint (advisory, checksum-verified binary) |
| 57 | Image build | docker build |
| 58 | Image vulnerabilities + secrets | Trivy image scan (SARIF to the Security tab) |

Integrity engine (the detector in this repo, `scripts/ai_slop_detector.py`).
It checks code, documents and frontend files for machine-written filler and
for the structural tells AI agents leave behind. It reports a **slop score
0-100** (grade A-F, verdict CLEAN / SUSPICIOUS / INFLATED / CRITICAL), a
"fix these first" brief, per-rule totals and SARIF for the Security tab.
Its model borrows from flamehaven01/ai-slop-detector (deficit scoring,
conservative aggregation), FixMySlop (rule + remedy + baseline + SARIF
flow) and slopdetector.org (explainable prose evidence).

**Since v1.18 the scores are separated into quality dimensions** - AI
slop and code health are different questions, and blending them makes
both answers less credible. Every finding belongs to exactly one
bucket, and each bucket is scored independently with the same deficit
model:

| Dimension | Contents |
|---|---|
| Architecture | `arch-*` - the repo's declared import law (opt-in, fail-closed config) |
| Code health | exception handling, debug residue, dead code, structural bloat |
| Maintainability | design debt, naming, suppression habits |
| Security | `eval-usage`, placeholder credentials |
| Test assurance | empty/tautological tests, missing test inventory |
| Dependency health | undeclared imports, unused dependencies |
| AI integrity | authorship and machine-prose signals - what `--max-slop-score` gates |

The CLI prints every dimension grade on its summary line and the
`--report-file` markdown carries a "Quality dimensions" table, so a
bloat-heavy-but-slop-free repo grades A on the AI-integrity ceiling -
and swallowed exceptions can never hide behind a clean slop score.
`--max-score` still gates every blocking weight in every dimension.

Blocking-grade rules (deterministic content defects; only fail a build when
`fail_on_ai_slop: true`):

| Check | Catches | Languages |
|---|---|---|
| `ai-vocab` | telltale vocabulary in comments | all |
| `chatbot-residue` | assistant-voice phrases in comments | all |
| `emoji-comments` | decorative emoji in comments | all |
| `todo-density` | TODO/FIXME density above 5% | all |
| `trivial-comments` | comments that restate the code | all |
| `boilerplate-duplication` | identical lines repeated 10x+ | all |
| `elided-code` | `// ... existing code ...` - edits that silently delete half a file | all |
| `placeholder-impl` | "in a real app...", "to be implemented" | all |
| `stub-function` | bodies that only pass / raise NotImplementedError | Python (AST) |
| `empty-test` | tests that assert nothing | Python (AST) |
| `bare-except` / `swallowed-exception` | `except:` / pass-only handlers | Python (AST) |
| `empty-catch` | `catch {}` | JS/TS, Java, Kotlin, C#, C/C++, PHP, Swift, Dart |
| `empty-rescue` | rescue with no handling | Ruby |
| `go-ignored-error` | `if err != nil { return nil }` | Go |
| `print-stacktrace-only` | catch blocks that only print | Java/Kotlin |
| `debugger-left` | `debugger;`, `binding.pry`, `byebug` | JS/TS, Ruby |
| `rust-stub` | `todo!()` / `unimplemented!()` | Rust |

Advisory rules (never fail a build; reported, annotated, SARIF `warning`):

| Family | Checks |
|---|---|
| Dependency hygiene | `phantom-import` (unused), `undeclared-import` (not stdlib/local/manifest - the slopsquatting guard), `star-import`, `mutable-default` |
| Suppression abuse | `lint-escape`, `blanket-suppression`, `ts-suppression` |
| Code smells | `god-function` (>80 non-comment lines, unified lizard parse), `high-cyclomatic` (complexity >15, every parsed language), `high-cognitive` (cognitive >15, tree-sitter parse), `deep-nesting` (>5 levels, every family), `bloat-long-file` (>400 lines), `bloat-param-count` (>6 parameters), `bloat-comment-ratio` (>50% comments), `bloat-banner-comments` (bunched banner padding), `placeholder-naming`, `debug-print`, `unwrap-density`, `go-error-assigned-blank` |
| Credentials | `placeholder-credential` (fake keys gitleaks ignores) |
| UI slop | `ui-ai-gradient`, `ui-gradient-text`, `ui-marketing-copy`, `ui-placeholder-copy`, `ui-emoji-decor`, `ui-eyebrow-label`, `ui-italic-serif-hero`, `ui-fake-stats`, `ui-generic-testimonial`, `ui-not-just-x`, `ui-triple-slogan`, `ui-glass-glow`, `ui-decorative-numbering`, `ui-placeholder-image`, `ui-identical-cards`, `ui-em-dash-overuse` |
| Prose | `ai-prose`, `humanizer`, `em-dash-density`, `rule-of-three`, `rhetorical-questions`, `exclamations`, `md-emoji-headings`, `md-residue`, `step-comments`, `placeholder-note` |
| Provenance | `ai-provenance` (generation banners, AI co-author trailers), `missing-tests` (repo inventory) |
| Architecture (opt-in) | `arch-forbidden-import`, `arch-layer-violation`, `arch-missing-pattern` - blocking-grade, declared in `.ai-integrity-arch.json`; no config, no findings; **fail-closed since v1.18** - an invalid config exits 2, it is never silently skipped |

The provenance family is deliberately advisory: style is not evidence of
authorship, and the goal is traceability and quality, never blame. Every
finding names its rule id and carries a remediation hint; `.ai-integrity-ignore`
excludes paths or single rules; `--write-baseline` / `--baseline` accept
existing findings so only new slop shows up.

Detector language coverage for structural checks: **Python** (AST-backed),
**JavaScript/TypeScript/JSX/TSX/Vue/Svelte/Astro**, **Go**, **Java/Kotlin/**
**Scala/Groovy**, **C#**, **C/C++**, **Ruby**, **PHP**, **Rust**, **Swift**,
**Dart**, **Shell**, plus CSS/SCSS/HTML for UI slop. Comment extraction is
language-aware (line, block and docstring styles).

## How results reach you

1. **PR dashboard comment** - a table with every check and its state, updated
   in place as commits land
2. **Inline annotations** - warnings and errors pinned to file and line
3. **Security tab** - Semgrep, Trivy, OSV, CodeQL, Scorecard and AI-integrity
   results are uploaded as SARIF into code scanning, with per-tool categories
4. **Step summary** - slop score, grades and fix-first brief on every run page
5. **`ci-success`** - one gate for branch protection (also exposes a
   `verdict` output for callers that want to branch on the result)

## Profiles: quick / standard / strict / custom

One `profile` input changes enforcement levels - presets, not duplicated
workflows:

```yaml
jobs:
  master:
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@v2
    with:
      profile: quick    # fast PR feedback - deterministic gates stay on
      template_ref: v2  # fetch helpers from the same release as the workflow
    permissions:
      contents: read
      security-events: write
      pull-requests: write
```

**Helper pinning**: the workflow ref (`@v2`) and `template_ref` are two
separate versions. The workflow file can come from a release tag while
`template_ref: main` would fetch helper scripts from a moving branch - so
helpers and checks could silently diverge from the version you pinned.
`meta` prints a warning for mutable refs; pin `template_ref` to the same
tag as the workflow for reproducible CI.

| Setting | quick | standard (default) | strict | custom |
|---|---|---|---|---|
| Semgrep packs | `p/default` | input (3 packs) | input (3 packs) | input |
| OSV vulnerable deps | advisory | **fail** | **fail** | **fail** |
| Trivy HIGH/CRITICAL | advisory | advisory | **fail** | advisory |
| Duplication gate | advisory | **fail** | **fail** | **fail** |
| Complexity gate | advisory | **fail** | **fail** | **fail** |
| deps-review severity | critical | high | high | high |
| AI-integrity gate | advisory | **fail** (blocking + slop ceiling) | **fail + structural budgets** (`--enforce-bloat`, caps 4/20) | raw `fail_on_ai_slop` input |

## Run only what you want (`select`)

Every check job can be selected individually - real modularity without
duplicating workflows. Pass a comma-separated list of job names (no
spaces); an empty value runs everything:

```yaml
with:
  select: secrets,python    # only these two jobs run; the rest skip cleanly
```

Unknown names fail the run in `meta` with the valid list - a typo can
never silently skip a check. Valid names: `hygiene`, `secrets`,
`ai-integrity`, `docs`, `node-lint`, `node-test`, `python`, `rust`,
`go-lint`, `go-test`, `java`, `cpp`, `csharp`, `ruby`, `php`, `docker`,
`security`, `deps-review`, `scorecard`, `coverage-gate`,
`new-code-gate`, `quality-ratchet`. The aggregate gate still passes
when jobs are skipped by design, so branch protection keeps a single
required check.

## Quality ratchet: never worse than your baseline

Thresholds catch today's offenders; the ratchet protects the
trajectory. Record the current per-check finding counts once:

```bash
python3 scripts/ai_slop_detector.py . --write-quality-baseline .quality-baseline.json
```

Commit the file. From then on every run compares the scan against the
floor:

- **Any check above its recorded count fails the run** (exit 1) - fix
  the new findings, or make a reviewed commit that re-records the floor
  (the escape hatch is visible in git history, not a silent waiver).
- **Paying debt is celebrated** - the step-summary delta table shows
  improvements next to regressions, so cleanup PRs are visible.
- **A baseline that cannot be interpreted fails closed** (exit 2):
  invalid JSON, a schema version bump, or check ids this engine's
  catalog does not know (the catalog changed - re-record the floor).
  The same applies when `ratchet: true` demands a file that is missing.

The job runs automatically once the file exists (`ratchet: auto`, the
default), is forced with `ratchet: true`, or is disabled with
`ratchet: false`. Every check id the scan reports participates -
advisory and blocking alike - because a committed baseline is an
explicit per-check contract the repo opted into; only
`low-new-coverage` stays out (it depends on other jobs' coverage
artifacts, not on the tree alone). The ratchet counts findings before
`--baseline` fingerprint suppression, so accepted debt cannot hide a
regression, and it composes with the new-code gate: ceilings on the
diff, a floor for the whole repo.

Full semantics per check (what is inspected, what fails, false positives,
how to disable, added time) live in [docs/checks.md](docs/checks.md).

## Flaky test hunt: green means green

A flaky test makes every green run a coin flip: the suite passed on the
retry nobody recorded. Coverage says which lines ran and mutation says
whether assertions can fail; only repetition says whether the verdicts
are stable. Since v2.10 the hunt is a one-line adoption, like CodeQL and
mutation testing before it: call `flaky.yml` from your own scheduled (or
dispatched) wrapper,

```yaml
jobs:
  hunt:
    uses: WasewaseX/master-ci/.github/workflows/flaky.yml@v2.12.0
    with:
      template_repo: WasewaseX/master-ci
      template_ref: v2.12.0
    permissions:
      # The called quarantine job declares contents: write and
      # pull-requests: write; a reusable call that grants less than its
      # callee declares NEVER STARTS (the ci-canary's first read-only
      # dispatch died as a startup_failure with zero jobs). The scopes
      # let the run start; the committed .flaky-baseline.json decides
      # whether the loop ever fires - no baseline, no proposal, no PR,
      # and the scopes idle.
      contents: write
      pull-requests: write
```

and it runs your own test suite 2-5 times (`iterations` input, Monday
mornings by default) and reports every test whose verdict changed
between iterations:

- **One job, your runners** - pytest junitxml, vitest/jest junit, `go
  test -json`, `cargo test` output, surefire/Gradle XML, .NET TRX, RSpec
  (with `rspec_junit_formatter`) and PHPUnit are consumed as-is; C/C++
  and any other runner feed the same analyzer any JUnit XML (ctest:
  `ctest --output-junit run-1.xml`). No project changes how it tests.
- **Quarantine by declaration** - a committed `.flaky-baseline.json`
  records known flakes; the hunt reports them without failing and fails
  (exit 1) the moment a *new* flaky test appears. Without a baseline the
  hunt is report-only - adopt enforcement by committing the baseline it
  produces. Healed flakes are listed as removable debt.
- **Fail-closed (exit 2)** - a missing iteration report, an unreadable
  result file, conflicting verdicts inside one file, a run that records
  zero tests, or an unreadable baseline all fail the hunt: a measurement
  that cannot be taken loudly is never a silent pass.

```bash
# record the quarantine after the first hunt
python3 scripts/flaky_detect.py --results-dir flaky-results --write-baseline
```

Full semantics live in [docs/checks.md](docs/checks.md).

## Configuration (all optional)

| Input | Default | Meaning |
|---|---|---|
| `profile` | `standard` | `quick` / `standard` / `strict` / `custom` preset |
| `node_version` | `22` | Node.js for the Node stack |
| `python_version` | `3.13` | Python for the Python stack |
| `rust_toolchain` | `stable` | Rust toolchain |
| `go_version` | `stable` | Go toolchain |
| `java_version` | `21` | JDK (Temurin) for the JVM stack |
| `dotnet_version` | `10.0` | .NET SDK (ignored when `global.json` pins one) |
| `ruby_version` | `3.4` | Ruby for the Ruby stack |
| `php_version` | `8.4` | PHP for the PHP stack |
| `runs_on` | `ubuntu-latest` | Runner label for every job (e.g. `macos-latest`) |
| `run_node` / `run_python` / `run_rust` / `run_go` | `auto` | Force a stack `true` / `false`, or auto-detect |
| `run_java` / `run_cpp` / `run_csharp` / `run_ruby` / `run_php` / `run_docker` | `auto` | Force a stack `true` / `false`, or auto-detect |
| `select` | `''` | Comma-separated job names to run (empty = all). Unknown names fail in `meta` |
| `semgrep_config` | `p/default p/security-audit p/secrets` | Semgrep packs (standard/strict/custom) |
| `fail_on_ai_slop` | `false` | Raw input for `profile: custom`; `standard`/`strict` arm the AI-integrity gate themselves |
| `ai_slop_max_score` | `8` | Blocking score cap |
| `complexity_max` | `15` | Cyclomatic complexity gate |
| `duplication_threshold_pct` | `5` | jscpd failure threshold |
| `coverage_min` | `0` | Measured coverage floor (percent, `0` = off). Above 0 the Python/Go/Node/Rust/JVM stacks run tests under coverage (Go coverprofiles, cargo-llvm-cov cobertura, JaCoCo via Maven CLI injection or Gradle's own plugin) and `ai-integrity` gates on the reports; no report fails closed (exit 2) |
| `diff_base` | `''` | Git ref defining the new-code window - the same `coverage_min` floor then applies to the added lines (merge-base diff). When set, it also enables the new-code dimension ceilings unless `new_code_gates: false`. Empty = off |
| `new_code_gates` | `true` | Enforce the new-code dimension ceilings on the `diff_base` diff - legacy files keep their history, every added line meets today's bar |
| `new_code_max_slop` | `0` | Blocking-score ceiling for findings landing on added lines (zero tolerance by default) |
| `new_code_max_cognitive` | `15` | Cognitive-complexity ceiling for functions the change touches (`0` = off) |
| `new_code_max_cyclomatic` | `15` | Cyclomatic-complexity ceiling for functions the change touches (`0` = off) |
| `ratchet` | `auto` | `auto` enforces the committed baseline when the file exists; `true` demands it (missing = exit 2); `false` skips the job |
| `ratchet_file` | `.quality-baseline.json` | Baseline file the quality ratchet enforces |
| `scorecard_publish` | `true` | Publish Scorecard results (needs `id-token: write`) |
| `template_repo` | auto | Helper repo override for forks (validated: `owner/repo`) |
| `template_ref` | caller's own commit | Helper ref. Empty = the caller's own commit, only when the helpers live there (self/fork); a foreign helper repo must pin a tag, mutable branches print a warning |
| `concurrency_key` | `''` | Distinguishes concurrent calls when one workflow calls master-ci several times |

## Permissions: least privilege, explained

Every job in the reusable workflow declares its own `permissions:`; most run
with `contents: read` only. The caller grants the union once:

| Scope | Why | Which job uses it |
|---|---|---|
| `contents: read` | checkout | everything |
| `security-events: write` | SARIF upload to code scanning | security, scorecard, docker |
| `pull-requests: write` | dashboard comment, dependency review comment | dashboard, deps-review |
| `id-token: write` | OSSF Scorecard result publishing (OIDC) | scorecard only |
| `packages: write` | GHCR image push (release-docker.yml only) | release-docker publish |

No `secrets: inherit` is needed anywhere: the workflows only use the
automatic `GITHUB_TOKEN`. To drop `id-token` entirely, set
`scorecard_publish: false` and remove the line from your caller.

Fork pull requests are safe by construction: GitHub forces the token to
read-only on `pull_request` events from forks, the workflow declares no
secrets, and nothing in it pushes to your branches.

Untrusted caller inputs (`template_repo`, `template_ref`, `profile`) are
validated in the `meta` job before any job may fetch helpers; every job
that consumes helpers is gated on that validation succeeding. Supply-chain
posture is enforced mechanically by `tests/test_workflow_contracts.py`:
SHA pins, no `secrets: inherit`, no expression interpolation inside `run:`
blocks, per-job permissions and timeouts.

## CodeQL languages vs. full CI stacks

`codeql.yml` defaults to `languages: auto`: a detector (versioned with
the template, `scripts/codeql_detect.py`) reads the manifests the repo
already commits - go.mod, Cargo.toml, pom.xml/build.gradle, package.json,
pyproject.toml, Gemfile, Package.swift, CMakeLists, .csproj - and maps
them to the CodeQL dialects `javascript-typescript`, `python`, `go`,
`java-kotlin`, `c-cpp`, `csharp`, `ruby`, `rust` and `swift`. A repo
CodeQL cannot analyze fails the detect step (exit 2) with the fix
spelled out - pass `languages:` explicitly or drop the call; it never
analyzes an empty set silently. Explicit `languages:` still works for
repos that want to pin the dialects. That is
**CodeQL deep security analysis** for those languages only. The full stack
coverage - auto-detection, lint, format, types, tests, dependency policy -
now covers Rust, Node/TypeScript, Python, Go, JVM (Java/Kotlin), C/C++, .NET (C#/F#), Ruby, PHP and Docker.

CodeQL and Master CI's own stacks are complementary: enable the CodeQL job
for the deep data-flow queries, keep the stacks for the fast, deterministic
per-commit gates. Kotlin compiles through the JVM stack (Maven/Gradle);
Scala projects riding Maven or Gradle are covered by the same job.

Dependency installs are lockfile-strict: when a lockfile exists
(`pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`), the install runs
frozen/immutable and a drift failure is a real failure - there is no
silent fallback to mutable resolution. A Python project install
(`pip install -e .`) that fails fails the job; tests never run against
an environment the packaging step could not build.

If your repo has the default CodeQL setup enabled, switch it off
(Settings > Code security > Code scanning) to avoid a conflict with this
workflow.

## Container releases: release-docker.yml

Building and pushing images is a separate reusable workflow, so a release
never waits on CI and CI never holds registry credentials. It supports
three registries - `ghcr` (automatic `GITHUB_TOKEN`), `dockerhub`
(`dockerhub_username` / `dockerhub_token` secrets) and `ecr` (AWS OIDC,
see below) - and gives every published image:

- multi-arch buildx build (`platforms` input, QEMU set up automatically)
- a commit-SHA tag, `latest`, and any `extra_tags` you pass
- OCI provenance + SBOM attestations on every push
- a Trivy image scan uploaded to the Security tab
- a cosign **keyless** signature (Sigstore OIDC - no signing keys exist to
  store, rotate or leak) that is verified before the workflow reports
  success

```yaml
  publish:
    needs: ci          # release only from a green pipeline
    uses: WasewaseX/master-ci/.github/workflows/release-docker.yml@v2
    with:
      registry: ghcr
      image_name: owner/app
      platforms: linux/amd64,linux/arm64
    permissions:
      contents: read
      packages: write
      security-events: write
      id-token: write
```

`push: false` runs the same build load-only - a smoke release that proves
the image builds and scans without publishing.

## AWS deployments: deploy-aws.yml

Deployments authenticate with **AWS OIDC federation only**: the caller
passes an IAM `role_arn` that trusts GitHub's OIDC provider, and the
workflow assumes it for short-lived credentials. There is no input, no
secret and no code path for a static access key. The deployment contract
is validated (target allowlist, required inputs per target, path safety)
*before* any role is assumed.

| Target | Required inputs | Behavior |
|---|---|---|
| `s3` | `s3_bucket`, `s3_source_dir` | `aws s3 sync` (optional `s3_delete`), optional CloudFront invalidation |
| `lambda` | `lambda_function_name`, `lambda_zip_path` | zip (if a directory) + `update-function-code`, waits for `function-updated` |
| `ecs` | `ecs_cluster`, `ecs_service`, `ecs_task_definition_family`, `ecs_container_name`, `ecs_image` | renders the new image into the current task definition, updates the service, optionally waits for stability |
| `cloudformation` | `cf_stack_name`, `cf_template_file` | `aws cloudformation deploy` with `cf_capabilities` + `cf_parameters` (`Key=Value`, comma-separated) |

```yaml
  deploy:
    needs: ci
    uses: WasewaseX/master-ci/.github/workflows/deploy-aws.yml@v2
    with:
      role_arn: arn:aws:iam::123456789012:role/ci-deploy
      aws_region: eu-central-1
      target: ecs
      ecs_cluster: prod
      ecs_service: api
      ecs_task_definition_family: api
      ecs_container_name: api
      ecs_image: 123456789012.dkr.ecr.eu-central-1.amazonaws.com/api:v1.2.3
    permissions:
      contents: read
      id-token: write
```

One-time AWS setup (per AWS account): create the IAM role with a trust
policy for `arn:aws:iam::*:oidc-provider/token.actions.githubusercontent.com`,
scope its permissions to the deployment actions you need, and restrict
`StringEquals` on the sub/aud claims to your repo and audience
(`sts.amazonaws.com`). A typical ECR-based loop is
`release-docker.yml` with `registry: ecr` followed by `deploy-aws.yml`
with `target: ecs`.

## Automation and self loops

The template maintains itself. Loops included out of the box:

```text
push/PR ──> CI (dogfood) ──> red? ──> Self-Heal ──> autofix PR
   ^                                        |          |
   |                                        v          v
Deep Audit ──> findings ──> Autofix responds <── merge ──> green
   ^            |
   |            v
Nightly ──> audit tracking issue (secrets, vulns, prose: humans)
```

| Loop | File | Cadence | Effect |
|---|---|---|---|
| Dogfood | `ci.yml` | every push / PR | The template CI-checks itself with its own workflows |
| Fixture contracts | `ci.yml` | every push / PR | Detector and tooling run against `fixtures/` edge cases (empty, slop, ignore-file, malformed YAML) |
| **Selftest** | `selftest.yml` | every push / PR | The routine integration test, permanently green by construction: quick profile must pass, `select: secrets` must pass, and `verify` asserts both verdicts. A manual dispatch behaves exactly like a push - red always means broken |
| **Expected-failure watchdog** | `selftest-negative.yml` | weekly + relevant pushes + manual | Dispatches `selftest-guardrails.yml` and asserts the red is exactly the designed red - all five guardrails fired on their inner jobs and verify succeeded. Green child or mis-shaped outcome fails the watchdog |
| Designed-failure suite | `selftest-guardrails.yml` | dispatched by the watchdog only | The five guardrail calls (broken Semgrep pack, traversal `template_ref`, typo'd stack toggle, unknown select token, foreign helper with empty `template_ref`) run isolated here - caller jobs have no `continue-on-error`, so a dedicated dispatch-only workflow is the only way a designed red cannot pollute a routine run; its run name says red is expected |
| Self-update | `dependabot.yml` | weekly | Bumps every pinned action, grouped PR |
| **Autofix** | `autofix.yml` | weekly + right after every Deep Audit + manual | Runs the shared fix engine (`scripts/fix_engine.py`) - safe tier (rustfmt, gofmt, `ruff format`, Biome, shfmt, markdownlint, whitespace/EOF normalizer) plus lint tier (`ruff check --fix`, `go mod tidy`, `sqlfluff fix`, `semgrep --autofix`) - and opens one PR |
| **Self-Heal** | `heal.yml` | after CI failures on the default branch | Applies the **same fix engine** reactively, opens/updates the `ci/autofix` PR; circuit breakers: default-branch failures only, one heal at a time, stops while a fix PR is open |
| Deep sweep | `nightly.yml` | nightly + manual | Full-severity Trivy, CodeQL quality suites, verbose AI report |
| **Flake hunt** | `flaky.yml` | weekly (Mondays) + manual | Runs the test suite 2-5 times in one job and reports every test whose verdict changed between iterations; a committed `.flaky-baseline.json` quarantines known flakes and fails the hunt on a *new* one - test assurance's third leg, beside coverage and mutation |
| **Mutation testing** | `mutation.yml` | weekly (Mondays) + manual | Opt-in by declaration - `[tool.mutmut]` (Python), `stryker.conf.*` (Node), `.cargo-mutants.toml` (Rust), `.gremlins.yaml` (Go, thresholds are the gate) - surviving mutants fail the run: the weekly proof that the tests can fail. Since v2.9 it is also a reusable workflow: call it from your own scheduled wrapper, one pinned line |
| **Deep Audit** | `audit.yml` | weekly (Saturdays) + manual | Whole-repository sweep - full git history (gitleaks), every file (integrity engine), every dependency manifest (osv-scanner), Go modules (govulncheck), SQL (sqlfluff) - one tracking issue labeled `audit`; never gates CI. Its completion is the trigger for the Autofix response above |
| Housekeeping | `stale.yml` | weekly | Marks and closes inactive issues and PRs (never security-labeled ones) |
| Auto-label | `label.yml` | every PR | Labels PRs by touched paths |

**How "find problems" becomes "fix problems":** one fix policy lives in
`scripts/fix_engine.py` and every loop delegates to it, so the fixer set
always matches what CI checks (no fix/check oscillation). Fixes are tiered:
`safe` cannot change behavior (formatting, whitespace), `lint` applies only
tool-verified transforms (`ruff --fix`, `semgrep --autofix` for rules that
ship machine fixes). Everything that has no deterministic fix - secrets
(rotate), vulnerability upgrades (decision), AI-integrity prose (meaning) -
is never auto-fixed and stays in the audit tracking issue.

Autofix and Self-Heal open **reviewable PRs**; nothing merges or rewrites
code without a human pressing the button, CI must be green before the PR
merges, and closing the PR is the rollback. The Deep Audit never gates CI
at all - it is a clock-driven, read-only sweep with one tracking issue.

## MCP server: drive Master CI from any AI assistant

The template ships a built-in MCP server (`scripts/mcp_server.py`,
stdlib-only, MCP over stdio) so any MCP-capable AI assistant - Claude
Desktop, Cursor, Cline, Windsurf, z.ai and friends - can use the same
engines CI uses: scan for AI slop, ask which checks a change set would
trigger, dry-run the fix policy, apply it, and read the check catalog.
One engine, many surfaces: an MCP scan reports exactly what CI reports.
Every tool result embeds a **fix-ready `prompt` field** - the next
instruction the assistant should execute - and the MCP prompts primitive
serves ready-made walkthroughs (`fix-my-repo`, `explain-findings`,
`pre-flight`).

Installing the repository doubles as installing the skill: the repo root
carries a `SKILL.md`, so assistants with a skills directory can load it
with one command:

```bash
git clone --depth 1 --branch v2.12.0 https://github.com/WasewaseX/master-ci <skills-dir>/master-ci
```

MCP clients configure the same install like this:

```json
{
  "mcpServers": {
    "master-ci": {
      "command": "python3",
      "args": ["/path/to/master-ci/scripts/mcp_server.py"]
    }
  }
}
```

Set `MASTER_CI_MCP_ROOT` in the `env` block to lock every tool call to
one repository. Full tool reference: `docs/mcp.md`.

| Tool | Mutates? | What it does |
|---|---|---|
| `scan_slop` | no | AI-integrity scan: score, grade, verdict, blocking vs advisory findings with remedies |
| `scan_diff` | no | Scan only changed files (uncommitted or vs a base ref) + which CI stacks the change fires |
| `plan_fixes` | no | Dry-run of the fix policy: which tools would run, how many files the normalizer would touch |
| `apply_fixes` | **yes** | Run the fix policy in place (safe tier by default); returns the exact changed-file list; review with `git diff` |
| `list_checks` | no | Blocking vs advisory rules, phrase database, remedies, scoring formula, fix tiers |
| `filter_paths` | no | Which stacks a change set activates, from the live `dorny/paths-filter` table |
| `diagnose` | no | Install report: versions, loaded engines, missing formatters, self-test command |
| `check_updates` | network | Compare installed version with the newest tag; return the exact update command |

**Updates are never automatic.** The install tells you when it is stale
(`check_updates` or `mcp_ping --online`); you pull when you choose:
`git -C <install-dir> pull --ff-only`.

**Trust it before you use it** - the wire protocol is tested end to end
in CI, and you can re-prove it on any device at any time:

```bash
python3 scripts/mcp_ping.py            # spawns the server, drives a real MCP session
python3 scripts/mcp_ping.py --online   # plus the update check
```

## Testing the template

The template ships four layers of self-tests, all runnable locally:

```bash
python -m pytest -q          # 183 tests:
                             # - detector contracts (43): every rule family,
                             #   slop pairs, scoring, SARIF, baseline
                             # - path-filter regression (12): 140 synthetic
                             #   repo layouts with exact expected stacks,
                             #   picomatch-semantics translator, output wiring
                             # - fix engine (24): stack gating, safe/lint
                             #   tiers, generic normalizer edges, manifest
                             #   schema, engine/CI parity guard
                             # - MCP server (46): handlers, path lock, fix-
                             #   ready prompts, docs parity, and a real
                             #   subprocess stdio session plus the standalone
                             #   mcp_ping self test
                             # - workflow contracts (44): SHA pins, no
                             #   secrets:inherit, no template injection in
                             #   run blocks, timeouts, permissions, helper
                             #   configs, automation safety, selftest wiring,
                             #   self-heal circuit breakers, deep-audit
                             #   schedule/no-gate guarantees, Docker stack,
                             #   OIDC-only AWS deploys, cosign-signed releases
                             # - benchmark + meta validation (8): report
                             #   schema, select/stack allowlists
.github/workflows/selftest.yml  # in CI: the routine integration test,
                                # permanently green (pass / select subset,
                                # asserted by verify)
.github/workflows/selftest-guardrails.yml  # the five designed-failure
                                # calls, isolated in a dispatch-only
                                # workflow whose red runs are correct by
                                # construction
.github/workflows/selftest-negative.yml  # the expected-failure watchdog:
                                # dispatches the guardrails suite and only
                                # passes on the exactly-designed red
.github/workflows/integration.yml  # the consumer canary watchdog: asserts
                                # the independent consumer is alive, green,
                                # and reports its pin lag
```

The fourth layer is verification from the outside:
[WasewaseX/ci-canary](https://github.com/WasewaseX/ci-canary) is a real,
independent consumer - a tiny dependency-free Python+Node project whose CI
is the one-line `uses:` call from `examples/ci.yml`, pinned to an exact
release tag for both the workflow ref and `template_ref`. It runs on its
own schedule in its own repository, exactly like any stranger's project
would. The `integration.yml` watchdog then asserts from the template side,
daily, that the canary's latest run is green and at most 48 hours old, and
reports how far the canary's pin lags the newest release. A red or stale
canary fails the watchdog, so independent verification cannot silently rot.

The path-filter suite exists because wrong path detection is the failure
mode a CI template must never have: it pins the filter table extracted
directly from `master-ci.yml`, so any filter edit that changes which stacks
run for a layout fails the suite before it can fail a consumer.

## Zero-config defaults, fetched for you

When a check needs a config file and your repo does not have one, Master CI
copies the default from this repo at run time: `.yamllint.yaml`,
`.markdownlint-cli2.jsonc`, `.editorconfig`, `ruff.toml`, `deny.toml`,
`.semgrepignore`, `.github/zizmor.yml` (a policy that keeps SHA pinning
mandatory for actions and allows release-tag refs for the Master CI
workflows themselves), and `biome.jsonc` (scopes the Node linter to code
files and aligns its formatting with the default `.editorconfig` - Biome's
own defaults would otherwise format your YAML and Markdown). Commit any of
these to your repo to override. A contract test guarantees every referenced
default actually exists in this repo.

## Cost

Public repos: free, no minutes burned against private-repo quotas.
Private repos: works, but Actions minutes and Advanced Security SARIF
ingestion have their own billing; test before adopting there.

## Performance

The engines are benchmarked against a deterministic synthetic corpus
(fixed seed - identical on every machine, only timings vary):

```bash
python3 scripts/benchmark.py                        # full corpus, table
python3 scripts/benchmark.py --save baseline.json   # record this machine
python3 scripts/benchmark.py --check baseline.json  # fail on >3x regression
```

Reference numbers (`ubuntu-latest`, v1.8.6): detector ~1.2k files/s
(~1.0 MB/s), fix-engine plan ~90 ms, generic whitespace scan ~30 ms for
360 files, path filters ~60k paths/s, MCP server startup ~175 ms,
stdio roundtrip ~55 ms (the roundtrip re-parses the live filter table
per call - a known, measured optimization target). A weekly Benchmark
workflow publishes fresh numbers to its step summary, a 90-day
artifact, AND appends one JSON line per run to `benchmarks/history.jsonl`
on the dedicated `benchmarks` branch - a reproducible hosted history
that survives artifact expiry and is diffable with plain git
(`git log -p benchmarks/history.jsonl`). It is report-only because
shared runners are too noisy to fail a build on timings. The MCP
`bench` tool runs the same corpus from any AI client.

## Local development

```bash
pip install ruff mypy vulture lizard pytest
ruff check . && ruff format --check .
mypy --ignore-missing-imports .
python -m pytest -q
python3 scripts/ai_slop_detector.py . --max-score 8 --sarif /tmp/ai.sarif
python3 scripts/ai_slop_detector.py . --write-baseline .ai-baseline.json   # accept current findings
```

## Roadmap to v2.0 and beyond (deeper analysis, not more heuristics)

The direction for v1.18 through v2.1, in priority order - the goal is
understanding software, not stacking thirty more regex checks:

| # | Priority | Status |
|---|---|---|
| 1 | Fail-closed architecture configuration | **done (v1.18)** - invalid configs exit 2, never silently skip |
| 2 | Unified syntax-tree analysis across languages | **done (v1.19)** - lizard parse facts behind every structural budget, line scanner only as fallback |
| 3 | Cognitive complexity across languages | **done (v1.20)** - Sonar-style reader effort on tree-sitter trees, 15 budget, every parsed language |
| 4 | Coverage and mutation testing | **done (v1.21)** - `coverage_min` / `diff_base` gates consume coverage.json, Cobertura XML and Go coverprofiles, fail-closed; `mutation.yml` runs mutmut/Stryker weekly for opted-in repos |
| 5 | Full monorepo project discovery | **done (v1.22)** - manifest-aware workspace member enumeration (npm/pnpm `workspaces`, `go.work`, Cargo `[workspace]`), `--family all` map, declared members that cannot be honored fail closed (exit 2) |
| 6 | Native dependency and dead-code analysis | **done (v2.1)** - unused-dependency oracles for Go, Rust, PHP, C# and Java beside Python/Node, monorepo-scoped manifest discovery, `orphan-module` for unreachable files and Go packages; Ruby and C/C++ documented as unsound and left out |
| 7 | New-code quality gates | **done (v1.22)** - the `new-code-gate` job enforces blocking-score 0, cognitive 15 and cyclomatic 15 on the merge-base diff's added lines and touched functions; legacy files keep their history |
| 8 | Full ten-stack canary verification | **done (v2.0)** - the ci-canary consumer carries real code in all ten stacks (Python, Node, Go, Rust, Java, Kotlin, C#, C/C++, Ruby, PHP) and exercises every release against them |

Also since v1.18: the **quality-dimension split** - code health,
maintainability, security, test assurance, dependency health and
AI-integrity are reported and gated separately, because one blended
number is less credible than six honest ones. The template stays a CI
orchestration layer: mature semantic analyzers (CodeQL, Semgrep,
lizard) are integrated, not reimplemented as regexes.

And since v2.2: the **quality ratchet** - one committed baseline file
freezes per-check finding counts across every domain, any regression
blocks the run, and re-recording the floor is a reviewed commit. The
new-code gate keeps the diff honest; the ratchet keeps the trajectory
honest.

And since v2.3: the **flake hunt** - the test suite runs several times
on a schedule and every verdict flip between iterations is reported;
committed quarantine baselines grandfather known flakes and gate new
ones. Test assurance now covers all three questions: coverage (which
lines ran), mutation (would the assertions notice) and repetition (is
the verdict stable).

And since v2.4: the **quarantine loop** - the hunt's response, closing
its own verdict the way self-heal closes a red branch. When the hunt
fails on new flakes in enforcement mode, the candidate baseline is
merged append-only (every existing `first_seen` preserved, healed
flakes never removed), verified independently against the default
branch, and offered as one reviewable PR whose only possible change is
the baseline file. Closing the PR rejects the quarantine; merging it
silences only the listed tests. Measurement, verdict and response now
live in one loop.

And since v2.5: the **parser guarantee and the whole map** - the
SonarQube lesson applied to our own gates: a gate that cannot run must
fail, never skip. Every job that promises parse-backed metrics installs
the pinned analyzers and demands them (`--require-parsers`: a missing
lizard or tree-sitter is exit 2, never a quietly weaker scan), project
discovery drives the Go, Rust and JVM stacks the way it already drove
Node - nested modules, crates and builds are analyzed, not warned
about - and the PR dashboard reports which test-assurance layers are
armed and which are waiting for opt-in.

And since v2.6: the **wider net and the trend** - the audit's
build-next list, learned from the competitors rather than rebuilt from
scratch. Coverage now measures the whole map: the Rust slice emits
cobertura through the pinned `cargo-llvm-cov` and the JVM slice gets
the pinned JaCoCo agent injected from the Maven command line (Gradle
projects with the plugin get their report task invoked; the gate's
parser dispatches JaCoCo's own XML dialect by root tag). Mutation
testing spans four ecosystems, each opt-in by declaration:
cargo-mutants (Rust, exit 2 = survivors) and gremlins (Go, the
thresholds declared in `.gremlins.yaml` are the gate - verified
against the tool's own exit codes) join mutmut and Stryker.
Dependency hygiene spans every stack the template can see: depcheck
(Node) and vulture (Python) keep their advisory findings, and the
toolchains' own oracles join them - `go mod tidy -diff` (exits 1 when
go.mod would change) and cargo-machete (exits 1 on unused deps), both
advisory, both pinned. And the ci-success summary carries a
**quality trend**: a cache-rolled JSONL history records every run -
verdict, floor, failed jobs - and renders the last ten, so the
trajectory is visible the way SonarQube's history is, without running
a server. The template stays what it is - the coordinator of proven
analyzers (CodeQL for deep semantics, Semgrep for security, the
language toolchains for their own truths) - never a pile of homemade
heuristics.

And since v2.9: the **callable proof** - mutation testing joins the
reusable surface. `mutation.yml` no longer has to be copied whole the
way consumers once copied every scheduled workflow; it now takes
`workflow_call` with
the codeql.yml helper contract (explicit `template_repo`, `template_ref`
defaulting to the moving `v2` tag), so one pinned `uses:` line from the
caller's own scheduled wrapper is the whole adoption - and the caller
keeps owning its schedule. Opt-in stays a declaration in the calling
repository: the detection step reads the caller's checkout, so a call
without a committed mutation config reports "nothing ran" and passes.
The dashboard's Mutation testing row is now verified the way the CodeQL
row has been since v2.7 - both reporting tables grep the caller's own
workflow files for the call, and the row flips to *armed via own
workflow* only when the call is really committed. Every workflow file
is scanned, not just the first hit: a mutation call in a file sorted
after the aggregate's must still be seen.

And since v2.10: the **reusable hunt** - `flaky.yml` completes the
trilogy. The flake hunt and its quarantine loop are callable with the
same helper contract, the same pinned-one-line adoption, and the same
verified reporting: the Flake hunt row in both tables greps the
caller's workflows for the call instead of declaring a scheduled file,
so a row cannot claim repetition the repository never wired. With that,
every opt-in surface in the template - CodeQL, mutation testing, the
flake hunt - is one pinned `uses:` line; nothing is copied whole.

And since v2.11: the **unmuted nets** - the mutation hunt's four
ecosystems stop being a first-match chain. The v2.6 detect step was
`if/elif`: the canary's `[tool.mutmut]` muted Stryker, cargo-mutants
and gremlins forever, and no multi-stack repo could mutate more than
its first-declared ecosystem. The chain hid two detection bugs besides:
`ls` exits nonzero when any candidate filename is missing, so a repo
committing exactly one Stryker config - the common case - was never
detected, and a `.gremlins.yaml`-only declaration was invisible too.
Found by simulating the detect block against fixture trees while
preparing the canary's second-ecosystem exercise. Now every declaration
appends independently to a JSON array and every declared tool's steps
run against it, each candidate file tested with `-f`; the ten-stack
canary mutates its Python, Rust and Go stacks in one weekly call.

And since v2.12: the **cohesion measurement** - the P1 metric layer
from the audit opens with its largest single gap. `slop_lcom4.py`
computes LCOM4, the connected method groups of one class through
shared state and self-calls: Python through the AST, JS/TS through the
string-aware scanner, with constructors, dunder protocol methods,
statics and test classes excluded and the reason written down, and
stateless helper clusters dropped before counting. A class carrying
two unrelated responsibilities now reports `LCOM4 2 (budget 1)` with
the disconnected groups named as evidence - advisory, maintainability
dimension, a measurement a reviewer judges rather than a gate that
fails the build.

The forward plan - the measurement-engine phases accepted from the
external v2.11.0 competitor audit (2026-11-11), verified claim-by-claim
against the codebase with the evidence recorded - lives in
[roadmap.md](roadmap.md): P0 workflow correctness (complete), the P1
metric layer - LCOM4 shipped in v2.12, the densities, debt and coupling
measurements next - semantic bloat analysis, the P2 gates-and-history
work, and the public precision/recall benchmark.

## License

MIT
