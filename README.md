# master-ci

Reusable GitHub Actions CI for **Rust**, **Node/TypeScript** and **Python** projects.
One file per project gives you 40 checks, path-aware skipping, aggressive caching,
pretty reporting and a set of automation loops that keep the whole thing updated.

![CI](https://github.com/WasewaseX/master-ci/actions/workflows/ci.yml/badge.svg)
![Nightly Deep Scan](https://github.com/WasewaseX/master-ci/actions/workflows/nightly.yml/badge.svg)

- Runs entirely on free `ubuntu-latest` runners for public repositories
- Detects which stacks changed and skips everything else
- Pushes findings to the **Security** tab (code scanning) and inline PR annotations
- Aggregates into a single `ci-success` check you mark as required

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
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@main
    secrets: inherit

  codeql:
    uses: WasewaseX/master-ci/.github/workflows/codeql.yml@main
    with:
      languages: javascript-typescript,python
    permissions:
      contents: read
      security-events: write
```

The single line that matters is:

```yaml
uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@main
```

Or fetch the ready-made caller with one shell line:

```bash
mkdir -p .github/workflows && curl -o .github/workflows/ci.yml https://raw.githubusercontent.com/WasewaseX/master-ci/main/examples/ci.yml
```

`languages:` accepts `javascript-typescript`, `python`, `go`, `java-kotlin`,
`c-cpp`, `ruby`, `rust` and `swift`. Pick only what your project contains.

### Required check

Branch protection sees exactly one status: **`ci-success`**. Set it as a
required status check (Settings > Branches > Branch protection rules) and the
aggregate gate decides pass or fail for every PR.

## What runs, and when

| Job | Trigger | Purpose |
|---|---|---|
| `changes` | always | Path filtering for all downstream jobs |
| `meta` | always | Resolves helper repo and config sources |
| `hygiene` | always | Workflow lint, YAML, shell, whitespace, duplication |
| `secrets` | always | Full-history secret scanning |
| `ai-integrity` | always | AI slop, AI prose and humanizer heuristics |
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

| # | Check | Signal |
|---|---|---|
| 36 | AI vocabulary in code comments | weighted phrase list |
| 37 | Emoji padding in comments | comment scan |
| 38 | TODO / FIXME density | density gate |
| 39 | Comments that restate the code | ratio gate |
| 40 | Boilerplate duplication | repeated-line scan |

Prose checks (same detector, applied to docs):

| # | Check | Signal |
|---|---|---|
| 41 | AI prose vocabulary | weighted phrase list |
| 42 | Em-dash overuse | density per 1000 words |
| 43 | Rule-of-three lists | bullet-list shape |
| 44 | Rhetorical question load | density per 500 words |
| 45 | Exclamation padding | count gate |
| 46 | Humanizer interjections | phrase list |

Numbered past 40 because the prose family kept growing; the detector is one
tool with two rule sets. The score cap defaults to 8 and is tunable.

## How results reach you

1. **PR dashboard comment** - a table with every check and its state, updated
   in place as commits land
2. **Inline annotations** - warnings and errors pinned to file and line
3. **Security tab** - Semgrep, Trivy, OSV, CodeQL and Scorecard results are
   uploaded as SARIF into code scanning, with per-tool categories
4. **Step summary** - a Markdown table on every run page
5. **`ci-success`** - one gate for branch protection

## Configuration (all optional)

| Input | Default | Meaning |
|---|---|---|
| `node_version` | `22` | Node.js for the Node stack |
| `python_version` | `3.13` | Python for the Python stack |
| `rust_toolchain` | `stable` | Rust toolchain |
| `run_node` / `run_python` / `run_rust` | `auto` | Force a stack `true` / `false`, or auto-detect |
| `semgrep_config` | `p/default p/security-audit p/secrets` | Semgrep packs |
| `fail_on_ai_slop` | `true` | Fail on AI-integrity score over the cap |
| `ai_slop_max_score` | `8` | Score cap |
| `complexity_max` | `15` | Cyclomatic complexity gate |
| `duplication_threshold_pct` | `5` | jscpd failure threshold |
| `template_repo` | auto | Helper repo override for forks |
| `template_ref` | `main` | Helper ref override |

Force a stack off or on:

```yaml
jobs:
  master:
    uses: WasewaseX/master-ci/.github/workflows/master-ci.yml@main
    with:
      run_rust: false
      ai_slop_max_score: 12
    secrets: inherit
```

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
| Self-update | `dependabot.yml` | weekly | Bumps every pinned action, grouped PR |
| Self-heal | `autofix.yml` | weekly + manual | Runs ruff, Biome, rustfmt, markdownlint `--fix`, opens a PR |
| Deep sweep | `nightly.yml` | nightly + manual | Full-severity Trivy, CodeQL quality suites, verbose AI report |
| Housekeeping | `stale.yml` | weekly | Marks and closes inactive issues and PRs |
| Auto-label | `label.yml` | every PR | Labels PRs by touched paths |

Consumers inherit most of this automatically: the reusable workflows are
version-pinned to commit SHAs, and Dependabot in your repo keeps the one
`uses:` line current. Copy `dependabot.yml` into a consumer repo to get the
same action-update loop there.

## Zero-config defaults, fetched for you

When a check needs a config file and your repo does not have one, Master CI
copies the default from this repo at run time: `.yamllint.yaml`,
`.markdownlint-cli2.jsonc`, `.editorconfig`, `ruff.toml`, `deny.toml`,
`.semgrepignore`. Commit any of these to your repo to override.

## Stack notes

- **Node**: package manager auto-detected from lockfiles (pnpm, yarn, npm),
  with matching cache. If a `lint` script exists it runs instead of Biome.
- **Python**: `ruff`, `mypy`, `vulture`, `lizard`, `pytest` install per run;
  pip cache enabled. Project installed when `[project]` exists.
- **Rust**: one job shares `rust-cache` across format, clippy, deny and test.
- **CodeQL**: free for public repos. If your repo has the default CodeQL
  setup enabled, switch it off (Settings > Code security > Code scanning)
  to avoid a conflict with this workflow.

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
