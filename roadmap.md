# Roadmap: from CI orchestrator to measurement engine

The forward plan. Accepted from the external v2.11.0 competitor audit
(2026-11-11, against commit `92b23d1`), then verified claim-by-claim
against this codebase before anything was written down here: every
"confirmed" below was checked in the source, with the evidence recorded
in the verification record at the end. The historical arc - v1.18
through v2.11, what shipped and why - lives in the README's roadmap
section. This file is only what comes next.

The audit's central conclusion, adopted as the thesis: Master CI is a
strong CI orchestration platform - reusable workflows, one required
-quality aggregate, supply-chain hygiene, a ten-stack canary - and its
code-quality engine trails the specialist products. The direction is
not more checks. It is measurement: maintainability, unnecessary code,
architectural complexity and technical debt, reported with evidence,
cost estimates and after-fix proof.

The one-sentence principle (from the audit, adopted whole): a good
code-quality platform should prove why code is unnecessarily
complicated, identify the exact source of the problem, estimate its
maintenance cost, and show evidence after the fix.

Two boundaries this roadmap keeps:

- Coordinate, do not replace. CodeQL and Semgrep stay integrated;
  the template never grows a homemade semantic engine. SonarQube,
  Qlty, Qodana and MegaLinter are references to learn from, not
  products to rebuild.
- No weighted composite before evidence. The Bloat Index ships as
  seven independent measurements first; a single combined score is
  built only after each category is validated against real
  repositories and the benchmark below.

## Where we stand (the audit's position table, accepted)

| Reference    | What we take from it                                  | What we do not do          |
| ------------ | ----------------------------------------------------- | -------------------------- |
| SonarQube    | Reliability/maintainability split, remediation effort, debt ratio, new-code vs overall separation, hotspot review | Not a SonarQube server     |
| Qlty         | LCOM4, duplication and complexity density, debt ratio  | Not a Qlty clone           |
| Qodana       | Per-language inspection profiles, severities, scopes   | Not an IDE-inspection port |
| MegaLinter   | Analyzer registry design, versioned language adapters  | Not a breadth race (67 languages is not the goal) |
| CodeQL       | Deeper integration: path queries, custom queries       | Never reimplemented        |
| Semgrep      | Project-specific rules, taint boundaries               | Never reimplemented        |

## P0: correctness and trustworthy analysis

Before new metrics, the workflows must do what they claim. Everything
in this phase was verified as a real gap in the current source.

| Task                 | Required result                                                                 | Verified state today |
| -------------------- | ------------------------------------------------------------------------------- | -------------------- |
| Stryker Node setup   | `mutation.yml` installs a pinned Node and activates the package manager before pnpm/yarn/npm run | Shipped in v2.11.1: setup-node pins Node 22, pnpm/corepack activation before the lockfile contract |
| Mutation discovery   | Every discovered project mutates; the detect block stops reading root-level declarations only | Shipped in v2.11.1: per-project scan across all families (root always included), fail-at-end loops, evidence captured on the failure path, every tool step under `always()` |
| Flaky-hunt deps      | The Python hunt installs the project's dependencies and pytest explicitly       | `python -m pytest` runs against whatever the runner happens to have |
| Flaky-hunt discovery | Project discovery across all supported stacks; per-project status in the summary | Root-only; a skipped suite is not distinguishable from a completed hunt |
| Parser-coverage policy | Per-language required-coverage policy with documented exceptions (generated, third-party) | Unsupported languages are reported, never enforceable |
| Required aggregate   | CodeQL and Master CI stay under the single `required-quality` check              | Done since v2.8; keep |
| Stale documentation  | Coverage input description names every integrated stack                          | Description still says "Python/Go/Node" while Rust (cargo-llvm-cov) and JVM (JaCoCo) shipped in v2.6 |

Also recorded: mutation testing has no incremental PR mode (scheduled
and dispatched only, by design today), and the scheduled (cron) path on
a multi-tool consumer is still awaiting its first hosted firing - the
three-ecosystem proof exists as `workflow_dispatch` runs (ci-canary
42983988435 under v2.11 and 43011661235 under v2.11.1's per-project
rewrite: mutmut + cargo-mutants + gremlins in one call, three dirs
scanned, Stryker correctly skipped as undeclared, per-project evidence
under `cargo-mutants-out/root/`).

Acceptance test (from the audit, adopted): a synthetic monorepo with
independent projects in several languages must show an explicit result
for every project in every workflow that claims the stack. No omitted
project hides behind a green aggregate.

## P1: real code-quality measurements

The metric layer. Priorities inside the phase: LCOM4 first (the
largest single gap - a class with five unrelated methods is worse than
one with twenty related ones, and no current rule sees it), then the
densities, then debt.

| Metric               | Definition                                             | Target gap it closes                     |
| -------------------- | ------------------------------------------------------ | ---------------------------------------- |
| Reliability rating   | Dedicated dimension: defective control flow, unsafe error handling, null/optional mistakes, resource leaks, state errors, unhandled failure paths | No reliability grade separate from maintainability |
| Remediation effort   | Rule-based repair-time estimate per finding, calibrated against reviewed fixes | Findings carry no cost |
| Debt ratio           | Estimated remediation cost / estimated development cost, overall and new-code | No debt indicator; explicitly a documented-estimate indicator, not an objective financial value |
| LCOM4                | Connected method groups through shared state           | No class-cohesion measurement            |
| Coupling             | Fan-in, fan-out, afferent/efferent, instability, cross-layer edges | Import cycles exist; no coupling model   |
| Complexity density   | Total complexity / executable lines x 1000, per project and language | Thresholds judge functions, never projects |
| Duplication density  | Duplicated executable lines / eligible lines            | jscpd catches copies, not density trends |
| Hotspot risk         | Churn x complexity x defect history x dependents        | No churn-aware prioritization            |
| Abstraction overhead | Forwarding-only methods, no-behavior classes, one-use interfaces, redundant adapter and DI chains | Thin-wrapper rule sees one function, never a 20-layer graph |

Constraints adopted with the metrics:

- Density denominators are language-aware executable lines, with an
  explicit generated-code policy.
- Reliability stays a separate dimension, never blended into
  maintainability or AI-integrity (same reasoning as the v1.18
  dimension split: one blended number is less credible than honest
  separate ones).
- Remediation estimates carry their rule and version, so calibration
  is reviewable.

Acceptance test: fixtures with high complexity, low cohesion,
duplicated implementations and stable-but-heavily-coupled modules each
produce their distinct measurement.

## P1: semantic bloat analysis

The engine under the metrics. The current oracles are deliberately
conservative and text-based where they are text-based; this phase
replaces text with structure, one ecosystem at a time.

| Task                 | Required result                                                                 | Verified state today |
| -------------------- | ------------------------------------------------------------------------------- | -------------------- |
| Symbol graph         | Exact definitions and references from native tooling                            | Dead-code oracle is name-occurrence based ("deliberately dumb", by design) - a comment or string can mask a dead declaration |
| Call graph           | Project-wide call relationships with confidence levels where dispatch is dynamic | No call graph        |
| Dependency graph     | Native package and build relationships (Cargo, go.mod, Composer, Maven/Gradle, Roslyn) | Cycles resolve natively for Python, JS/TS, Go and Ruby only; JVM/PHP/C#/C++ out by design until native graphs exist |
| AST clone detection  | Near-identical implementations behind different names and formatting            | Token-level jscpd only |
| Abstraction overhead | The whole relationship graph judged, not each wrapper alone                     | See P1 metric table  |
| Architecture adapters | Native import/layer rules per language (Python AST, tsc, go packages, cargo metadata, Roslyn, JVM build metadata, Composer) | Architecture engine is regex-based import extraction for Python and JS-family |
| Nesting from the tree | Syntax-tree nesting for every supported grammar                                | AST-backed for Python; other families still use the structural line scanner's block matcher |

Acceptance test: dynamic registration, reflection, generated code,
framework entry points and legitimate re-export modules must be
distinguished from real dead code and real overhead; uncertain results
stay advisory.

## P2: gates, baselines, history

| Task                  | Required result                                                                 | Verified state today |
| --------------------- | ------------------------------------------------------------------------------- | -------------------- |
| Finding fingerprints  | The quality ratchet reports new / resolved / unchanged / reintroduced from stable identities (rule id, project, file, symbol, normalized fingerprint, severity, rule version) | The ratchet freezes per-check counts; removing one old finding while adding a new one leaves the count unchanged and invisible. A fingerprint suppression baseline already exists for advisory findings - this upgrades the ratchet, it does not start from zero |
| Per-project baselines | Monorepo projects cannot hide a regression behind another project's improvement  | Baseline is per check id, not per project |
| New-code gates        | Duplication density, debt ratio, LCOM4 and coupling join cognitive/cyclomatic on the merge-base diff | New-code gate covers blocking findings, cognitive and cyclomatic only |
| Durable history       | Metric snapshots in a versioned store (dedicated branch), not just CI outcomes   | `quality_history.py` records ts/run_id/ref/sha/verdict/coverage_min/failed_jobs in an `actions/cache` JSONL; caches unused for seven days are evicted - it is a trend of CI outcomes, not of code quality |
| Analyzer profiles     | Qodana-style per-language rules, severities, scopes, thresholds, baseline-aware gates | Thresholds are global constants |
| Refactoring proposals | Reviewable PRs with evidence - the quarantine-PR pattern generalized to quality debt | Quarantine is the only PR-emitting loop today |

Acceptance test: remove one old finding and introduce another under
the same rule. The baseline must report one resolved and one new
finding even though the total count never changed.

## P2: language breadth (ordered)

Use each ecosystem's native engines; do not duplicate their analysis.

1. C# with Roslyn.
2. Java and Kotlin with build/compiler metadata.
3. PHP with PHPStan and Composer metadata.
4. Ruby with native parser and dependency metadata.
5. C/C++ with clang-tidy and build-graph analysis.
6. Python monorepo discovery (workflow gap: discovery drives node,
   rust, go and jvm only; Python, .NET, Ruby and PHP jobs are
   root-manifest-only today).

## The benchmark (the proof competitors cannot fake)

Clean self-scans do not prove a detector catches real bloat in
arbitrary codebases. A public fixture repository, every fixture with a
declared expected outcome:

| Fixture                              | Expected result                 |
| ------------------------------------ | ------------------------------- |
| Duplicate implementation             | Duplicate detected              |
| Two unrelated class responsibilities | Low cohesion detected           |
| Unused function                      | Dead declaration detected       |
| Dynamically registered function      | No false dead-code accusation   |
| Long but cohesive file               | Context-aware result            |
| Short but complex function           | Complexity detected             |
| Excessive abstraction chain          | Abstraction overhead detected   |
| Unnecessary dependency               | Dependency bloat detected       |
| Legitimate framework wrapper         | No false positive               |
| Circular dependencies                | Architecture violation detected |
| Complex code with high churn         | Hotspot identified              |
| Redundant tests                      | Test redundancy identified      |

Five measurements per rule: precision, recall, false-positive rate,
analysis coverage, runtime and memory. Then run the same fixtures
through SonarQube, Qlty and Qodana and compare equivalent metrics, not
unrelated scores. A rule with 95% precision and 70% recall is more
useful than one with 50% precision and hundreds of findings.

Precedent inside this repo: the v2.11 detect-lie fix shipped only
after the detect block was simulated against eight fixture trees. The
benchmark generalizes that discipline to every detector.

Test bloat joins the fixture list as its own measurement area:
duplicate test cases, repeated fixtures, overlapping assertions, tests
that exercise no unique branch, mutation survivors grouped by common
missing behavior. Suite size is not the signal; redundancy and
effectiveness are.

## Priority order (the audit's ranking, adopted)

| # | Item                                          | Priority |
| - | --------------------------------------------- | -------- |
| 1 | Unified bloat engine and LCOM4                | Critical |
| 2 | Semantic dead-code and dependency analysis    | Critical |
| 3 | Project discovery across every workflow       | Critical |
| 4 | Technical debt and new-code quality metrics   | High     |
| 5 | Architecture and coupling graphs              | High     |
| 6 | Durable history and finding fingerprints      | High     |
| 7 | Mutation and flaky-test ecosystem expansion   | Medium   |
| 8 | Precision and recall benchmark                | Medium   |

Each phase ships through the established loop: battery green, canary
pins moved, hosted run verified, one release per coherent slice. P0
items can ride individual patch releases; P1 metric clusters land one
minor release at a time with a canary exercise each; the benchmark
repo is its own deliverable with its own CI surface.

## Verification record (audit claim vs code, checked 2026-11-11)

| Audit item                       | Claim                                   | Verdict               | Evidence |
| -------------------------------- | --------------------------------------- | --------------------- | -------- |
| Bloat thresholds (item 9 context) | 400-file / 80-function / 6-param / 5-nesting / 15-cyclo / 15-cognitive / 50% comments / 4 banners | Confirmed, two details added | `slop_structural.py` constants; comment ratio only judged in files >= 60 non-blank lines; banners need median gap < 20; cyclomatic budget in `slop_lizard.py` |
| Referenced scripts (items 9, 15, 19-23) | `slop_bloat/structural/lizard/cognitive/analysis_coverage/dead/arch/cycles/deps.py`, `discover_projects.py`, `quality_history.py` exist | Confirmed | All present in `scripts/` |
| Item 13 - nesting line scanner   | Nesting relies on `_family_blocks()` and the scanner | Confirmed for non-Python; Python nesting is AST-backed | `slop_bloat.py` `_nesting_findings` shared by parse and scanner paths |
| Item 23 - discovery gaps         | Discovery integrated for Node/Go/Rust/JVM only | Confirmed | `master-ci.yml` calls `discover_projects.py` only with those four families |
| Item 24 - Stryker Node setup     | No explicit Node setup before Stryker   | Confirmed             | No `setup-node` step in `mutation.yml` |
| Item 24 - root-level mutation declarations | Mutation ignores the discovery system | Confirmed             | Detect block reads workspace root |
| Item 24 - hosted schedule unproven | Scheduled multi-tool run not yet exercised on the canary | Confirmed with a correction | The three-ecosystem proof exists as a dispatch run (42983988435, success); only the cron firing is pending |
| Item 25 - flaky setup gaps       | Python dep install missing, Node config-file detection, Ruby formatter requirement | Confirmed             | `flaky.yml` pytest step, vitest/jest config probes with skip warning, `rspec_junit_formatter` check |
| Item 26 - coverage docs stale    | Input description names only Python/Go/Node | Confirmed verbatim    | `master-ci.yml` `coverage_min` description; Rust and JVM integrations shipped v2.6 |
| Item 26 - coverage opt-in        | Default floor zero                      | Confirmed             | `coverage_min` default 0 |
| Item 28 - count-based ratchet    | Ratchet counts findings, not identities | Confirmed with nuance | `slop_ratchet.py` per-check counts; a fingerprint suppression baseline already exists beside it (`slop_cli.py` "two baselines") |
| Item 29 - shallow history        | History lacks quality metrics; cache-borne | Confirmed             | `FIELDS` tuple and `actions/cache` carry in `quality_history.py` |
| Item 30 - narrow new-code gate   | Gate covers findings + cognitive + cyclomatic | Confirmed           | `new_code_gate.py` inputs |
| Item 19 - dead-code oracle       | Text-occurrence based, conservative     | Confirmed             | `slop_dead.py` docstring states the trade explicitly |
| Item 21 - cycle coverage         | Py/JS-TS/Go/Ruby native; JVM/PHP/C#/C++ excluded | Confirmed, exclusion is documented as deliberate design, not an oversight | `slop_cycles.py` module docstring |
| Item 15 - parser coverage        | v2.8 five-state buckets                 | Confirmed             | `slop_analysis_coverage.py` and its bucket tests |

Not accepted as written: nothing. Two items needed the corrections
recorded above (scheduled-run evidence, fingerprint baseline) and the
threshold table gained two undocumented details; everything else
checked out exactly as the audit stated it.
