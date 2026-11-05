# master-ci

Reusable GitHub Actions CI for **Rust**, **Node/TypeScript** and **Python**
projects. One file per project gives you 40+ checks, path-aware skipping,
aggressive caching, pretty reporting and a set of automation loops that keep
the whole thing updated.

![CI](https://github.com/WasewaseX/master-ci/actions/workflows/ci.yml/badge.svg)
![Nightly Deep Scan](https://github.com/WasewaseX/master-ci/actions/workflows/nightly.yml/badge.svg)

- Runs entirely on free `ubuntu-latest` runners for public repositories
- Detects which stacks changed and skips everything else
- Pushes findings to the **Security** tab (code scanning) and inline PR annotations
- Aggregates into a single `ci-success` check you mark as required
- Least-privilege per job, no `secrets: inherit`, every action pinned by commit SHA
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
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@v1
    permissions:
      contents: read
      security-events: write
      pull-requests: write
      id-token: write   # only for Scorecard publishing (see "Permissions")

  codeql:
    uses: WasewaseX/master-ci/.github/workflows/codeql.yml@v1
    with:
      languages: javascript-typescript,python
    permissions:
      contents: read
      security-events: write
```

Or fetch the ready-made caller with one shell line:

```bash
mkdir -p .github/workflows && curl -o .github/workflows/ci.yml https://raw.githubusercontent.com/WasewaseX/master-ci/main/examples/ci.yml
```

### Required check

Branch protection sees exactly one status: **`ci-success`**. Set it as a
required status check (Settings > Branches > Branch protection rules) and the
aggregate gate decides pass or fail for every PR.

## Versioning (pick one strategy)

| Reference | Behavior | Use when |
|---|---|---|
| `@v1` | Tracks the latest `v1.x` release tag | Default recommendation; Dependabot PRs keep it fresh |
| `@v1.x.y` | Exact release | Reproducibility without surprises |
| `@<commit-sha>` | Immutable commit | Strict supply-chain posture (zizmor audit-clean) |
| `@main` | Live edge | Testing template changes before a release |

Releases are tagged `v1.x.y`; `v1` is a moving tag updated on every release.
Copy [`dependabot.yml`](.github/dependabot.yml) into your repo and Dependabot
opens update PRs for the `uses:` lines automatically - the self-update loop
works for consumers too.

## What runs, and when

| Job | Trigger | Purpose |
|---|---|---|
| `changes` | always | Path filtering for all downstream jobs |
| `meta` | always | Resolves helper repo, resolves enforcement profile |
| `hygiene` | always | Workflow lint, YAML, shell, whitespace, duplication |
| `secrets` | always | Full-history secret scanning |
| `ai-integrity` | always | AI slop detection - **advisory by default** |
| `docs` | docs changed | Markdown lint |
| `node-lint` | Node changed | Lint, format, types, unused deps |
| `node-test` | Node changed | Test suite |
| `python` | Python changed | Lint, format, types, dead code, complexity, tests |
| `rust` | Rust changed | Format, clippy, advisories, licenses, tests |
| `security` | always | Semgrep, Trivy, OSV scanning |
| `deps-review` | PRs | Dependency and license review |
| `scorecard` | pushes to default branch | OSSF Scorecard |
| `dashboard` | PRs | Results table comment on the PR |
| `ci-success` | always | Aggregate gate |

## The check catalog (40 checks)

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

AI integrity (heuristic detector in this repo):

| # | Check | Signal | Blocks? |
|---|---|---|---|
| 36 | AI vocabulary in code comments | weighted phrase list | yes |
| 37 | Emoji padding in comments | comment scan | yes |
| 38 | TODO / FIXME density | density gate | yes |
| 39 | Comments that restate the code | ratio gate | yes |
| 40 | Boilerplate duplication | repeated-line scan | yes |

Prose checks (same detector, applied to docs) - **always advisory**, because
stylistic signals are not evidence of authorship:

| # | Check | Signal | Blocks? |
|---|---|---|---|
| 41 | AI prose vocabulary | weighted phrase list | no |
| 42 | Em-dash overuse | density per 1000 words | no |
| 43 | Rule-of-three lists | bullet-list shape | no |
| 44 | Rhetorical question load | density per 500 words | no |
| 45 | Exclamation padding | count gate | no |
| 46 | Humanizer interjections | phrase list | no |

Numbered past 40 because the prose family kept growing; the detector is one
tool with two rule sets. The blocking score cap defaults to 8 and is tunable.
Every finding names its rule id and carries a remediation hint; `.ai-integrity-ignore`
lets you exclude paths or disable single rules.

## How results reach you

1. **PR dashboard comment** - a table with every check and its state, updated
   in place as commits land
2. **Inline annotations** - warnings and errors pinned to file and line
3. **Security tab** - Semgrep, Trivy, OSV, CodeQL and Scorecard results are
   uploaded as SARIF into code scanning, with per-tool categories
4. **Step summary** - a Markdown table on every run page
5. **`ci-success`** - one gate for branch protection

## Profiles: quick / standard / strict / custom

One `profile` input changes enforcement levels - presets, not duplicated
workflows:

```yaml
jobs:
  master:
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@v1
    with:
      profile: quick    # fast PR feedback - deterministic gates stay on
    permissions:
      contents: read
      security-events: write
      pull-requests: write
```

| Setting | quick | standard (default) | strict | custom |
|---|---|---|---|---|
| Semgrep packs | `p/default` | input (3 packs) | input (3 packs) | input |
| OSV vulnerable deps | advisory | **fail** | **fail** | **fail** |
| Trivy HIGH/CRITICAL | advisory | advisory | **fail** | advisory |
| Duplication gate | advisory | **fail** | **fail** | **fail** |
| Complexity gate | advisory | **fail** | **fail** | **fail** |
| deps-review severity | critical | high | high | high |
| AI-integrity | advisory | advisory | advisory | advisory |

Full semantics per check (what is inspected, what fails, false positives,
how to disable, added time) live in [docs/checks.md](docs/checks.md).

## Configuration (all optional)

| Input | Default | Meaning |
|---|---|---|
| `profile` | `standard` | `quick` / `standard` / `strict` / `custom` preset |
| `node_version` | `22` | Node.js for the Node stack |
| `python_version` | `3.13` | Python for the Python stack |
| `rust_toolchain` | `stable` | Rust toolchain |
| `runs_on` | `ubuntu-latest` | Runner label for every job (e.g. `macos-latest`) |
| `run_node` / `run_python` / `run_rust` | `auto` | Force a stack `true` / `false`, or auto-detect |
| `semgrep_config` | `p/default p/security-audit p/secrets` | Semgrep packs (standard/strict/custom) |
| `fail_on_ai_slop` | `false` | Fail on AI-integrity **blocking** score (advisory by default) |
| `ai_slop_max_score` | `8` | Blocking score cap |
| `complexity_max` | `15` | Cyclomatic complexity gate |
| `duplication_threshold_pct` | `5` | jscpd failure threshold |
| `scorecard_publish` | `true` | Publish Scorecard results (needs `id-token: write`) |
| `template_repo` | auto | Helper repo override for forks |
| `template_ref` | `main` | Helper ref override |

## Permissions: least privilege, explained

Every job in the reusable workflow declares its own `permissions:`; most run
with `contents: read` only. The caller grants the union once:

| Scope | Why | Which job uses it |
|---|---|---|
| `contents: read` | checkout | everything |
| `security-events: write` | SARIF upload to code scanning | security, scorecard |
| `pull-requests: write` | dashboard comment, dependency review comment | dashboard, deps-review |
| `id-token: write` | OSSF Scorecard result publishing (OIDC) | scorecard only |

No `secrets: inherit` is needed anywhere: the workflows only use the
automatic `GITHUB_TOKEN`. To drop `id-token` entirely, set
`scorecard_publish: false` and remove the line from your caller.

## CodeQL languages vs. full CI stacks

The `languages:` input of `codeql.yml` accepts `javascript-typescript`,
`python`, `go`, `java-kotlin`, `c-cpp`, `ruby`, `rust` and `swift`. That is
**CodeQL deep security analysis** for those languages only. The full stack
coverage - auto-detection, lint, format, types, tests, dependency policy -
is currently built for **Rust, Node/TypeScript and Python**. Go, Java/Kotlin,
C/C++, Ruby and Swift get security scanning, not stack checks.

If your repo has the default CodeQL setup enabled, switch it off
(Settings > Code security > Code scanning) to avoid a conflict with this
workflow.

## Automation and self loops

The template maintains itself. Loops included out of the box:

```text
push/PR ──> CI (dogfood) ──> red? fix ──> push
   ^                                        |
   |                                        v
Dependabot PR ──> weekly action bumps <── green
   ^                                        |
   |                                        v
stale sweep ──> weekly housekeeping <── labels
   ^                                        |
   |                                        v
autofix PR ──> safe formatters ──> merge ──┘
```

| Loop | File | Cadence | Effect |
|---|---|---|---|
| Dogfood | `ci.yml` | every push / PR | The template CI-checks itself with its own workflows |
| Fixture contracts | `ci.yml` | every push / PR | Detector and tooling run against `fixtures/` edge cases (empty, slop, ignore-file, malformed YAML) |
| Self-update | `dependabot.yml` | weekly | Bumps every pinned action, grouped PR |
| Self-heal | `autofix.yml` | weekly + manual | Runs ruff, Biome, rustfmt, markdownlint `--fix`, opens a PR |
| Deep sweep | `nightly.yml` | nightly + manual | Full-severity Trivy, CodeQL quality suites, verbose AI report |
| Housekeeping | `stale.yml` | weekly | Marks and closes inactive issues and PRs |
| Auto-label | `label.yml` | every PR | Labels PRs by touched paths |

Autofix opens **reviewable PRs**; nothing merges or rewrites code without a
human pressing the button.

## Zero-config defaults, fetched for you

When a check needs a config file and your repo does not have one, Master CI
copies the default from this repo at run time: `.yamllint.yaml`,
`.markdownlint-cli2.jsonc`, `.editorconfig`, `ruff.toml`, `deny.toml`,
`.semgrepignore`. Commit any of these to your repo to override.

## Cost

Public repos: free, no minutes burned against private-repo quotas.
Private repos: works, but Actions minutes and Advanced Security SARIF
ingestion have their own billing; test before adopting there.

## Local development

```bash
pip install ruff mypy vulture lizard pytest
ruff check . && ruff format --check .
mypy --ignore-missing-imports .
python -m pytest -q
python3 scripts/ai_slop_detector.py . --max-score 8
```

## License

MIT
