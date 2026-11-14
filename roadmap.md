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
| Flaky-hunt deps      | The Python hunt installs the project's dependencies and pytest explicitly       | Shipped in v2.11.2: pinned pytest first, the project's install contract second, the project's own pytest wins |
| Flaky-hunt discovery | Project discovery across all supported stacks; per-project status in the summary | Shipped in v2.11.2: discovery drives all eight families, per-suite results dirs, and `flaky_aggregate.py` names every suite - hunted with counts, skipped with the reason, failed-hunt loudly |
| Parser-coverage policy | Per-language required-coverage policy with documented exceptions (generated, third-party) | Unsupported languages are reported, never enforceable |
| Required aggregate   | CodeQL and Master CI stay under the single `required-quality` check              | Done since v2.8; keep |
| Stale documentation  | Coverage input description names every integrated stack                          | Shipped in v2.11.3: both workflow descriptions name all five stacks and their reporters; the v2.6-era checks.md paragraph caught up (five stacks, four parser dialects) |

The P0 phase ships complete: Stryker's pinned Node and mutation's
per-project scan (v2.11.1), the flaky hunt's per-project discovery
and per-suite status table (v2.11.2), the required aggregate kept
since v2.8, and the stale descriptions here. The remaining P0 note -
the scheduled (cron) mutation firing on a multi-tool consumer - is
evidence to collect, not code to write. The canary's cron mechanism
is proven live: the CI workflow's schedule has fired eight times, all
success (latest run 43594116995, 2026-11-14) - scheduled triggers fire
and run green on the consumer. The
mutation workflow's own cron window is the last unbanked firing
(first window Monday 2026-11-16).
P1 has begun: LCOM4, the phase's first metric and the audit's largest
single gap, shipped in v2.12.0, and the densities - complexity density
and duplication density - in v2.13.0; remediation debt with the debt
ratio and module coupling shipped together in v2.14.0; and v2.15.0
ships the phase's last three rows - the reliability rating, hotspot
risk and abstraction overhead - completing the P1 metric layer, plus
the P2 new-code gates row (duplication density, debt ratio, LCOM4 and
coupling join the merge-base diff).

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
densities, then debt with coupling. The phase ships complete at
v2.15.0: every row below is measured, tested and calibrated.

| Metric               | Definition                                             | Target gap it closes                     |
| -------------------- | ------------------------------------------------------ | ---------------------------------------- |
| Reliability rating   | Dedicated dimension: defective control flow, unsafe error handling, null/optional mistakes, resource leaks, state errors, unhandled failure paths | Shipped in v2.15.0: `reliability-rating` in `slop_reliability.py` - a dedicated failure-path dimension (error handling, leaks, shared state leave code health) plus two AST-backed defect checks filling the named gaps: `unclosed-resource` (an `open()` result held outside any with-block and never closed; blocking) and `shared-mutable-state` (a class attribute born a container literal every instance shares; advisory, `ClassVar` the declared exit). The rating prices the dimension's findings with the debt engine's registry minutes over the same SQALE denominator and grades SonarQube's bands (A to 3% of dev cost, B 5, C 10, D 20, E past); self grades A at 0% - one advisory finding, off the A band only |
| Remediation effort   | Rule-based repair-time estimate per finding, calibrated against reviewed fixes | Shipped in v2.14.0: pricing inside `slop_debt.py` - every finding priced by the class the engine's registry already gave it (design work 120 min: cycles, cohesion, the densities; structural budgets 30; blocking defects 20; advisory judgment calls 10) - no second taxonomy to drift from the severity model |
| Debt ratio           | Estimated remediation cost / estimated development cost, overall and new-code | Shipped in v2.14.0: `debt-ratio` in `slop_debt.py` - remediation minutes over the SQALE reconstruction cost (3 min per lexer-counted executable line of the densities' corpus), budget 5.0% against self at 0.14% (50 priced minutes over ~35.6k dev minutes); advisory, maintainability, one finding per scan, anchored at the most expensive finding, the engine's own finding never prices itself |
| LCOM4                | Connected method groups through shared state           | Shipped in v2.12.0: `low-cohesion` in `slop_lcom4.py` - Python AST-backed, JS/TS scanner-backed, the classic exclusions (constructors, dunders, statics, test files) documented with their false-positive reasons; advisory, maintainability dimension, groups rendered as evidence |
| Coupling             | Fan-in, fan-out, afferent/efferent, instability, cross-layer edges | Shipped in v2.14.0: `high-coupling` and `unstable-hub` in `slop_coupling.py` - fan-out, fan-in and Martin instability over the graph from `slop_cycles.build_graph`, production nodes only (tests and generated names leave, test-origin edges dropped before the tallies); fan-out budget 30 against self 22, unstable hub at fan-in 25+ with instability above 0.66 against self 25 at I 0.04, a pure provider; advisory, maintainability |
| Complexity density   | Total complexity / executable lines x 1000, per project and language | Shipped in v2.13.0: `complexity-density` in `slop_density.py` - total cyclomatic complexity per 1000 lexer-counted executable lines, budget 300 calibrated against this codebase's own 247/1000; one shared corpus (lexer-backed sources, no tests, no generated trees, 250-line minimum so snippets get no verdict); advisory, maintainability, per-language breakdown and worst contributors as evidence |
| Duplication density  | Duplicated executable lines / eligible lines            | Shipped in v2.13.0: `duplication-density` beside it - duplicated executable lines (stripped, non-comment, 40+ chars, seen twice or more) as a percent of the same corpus, budget 5.0% against self at 2.3%; the trend number a ratchet holds, anchored at the most repeated line |
| Hotspot risk         | Churn x complexity x defect history x dependents        | Shipped in v2.15.0: `hotspot-risk` in `slop_hotspot.py` - `risk = (churn + 3 x fixes) x ccn x (1 + fan_in / 10)` over the one measured corpus and graph: git history read in the scan root (any git failure means no history and silence, never a guessed count; the show-prefix strips so keys match the densities' rel paths), the densities' per-file complexity, the coupling graph's fan-in. Budget 1200 against self 352 at adoption (3.4x headroom - churn drifts per commit); advisory, anchored at the hottest file with the ranked list as evidence |
| Abstraction overhead | Forwarding-only methods, no-behavior classes, one-use interfaces, redundant adapter and DI chains | Shipped in v2.15.0 (first slice): `forwarding-chain` and `no-behavior-class` in `slop_abstraction.py` - the thin-wrapper rule upgraded to the pattern around it: a same-target delegation ladder of 4+ pass-through rungs within one module, and a class whose every non-dunder method forwards to one injected object; Protocol/ABC, dataclass, exception and enum shapes excluded with their false-positive reasons. Cross-module chains stay for the semantic phase's graph; both advisory, maintainability |

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
| Finding fingerprints  | The quality ratchet reports new / resolved / unchanged / reintroduced from stable identities (rule id, project, file, symbol, normalized fingerprint, severity, rule version) | Shipped in v2.16.0: the ratchet's schema v2 carries, per project, the full identity set (`project|check|path|fingerprint` - lines excluded because they drift) beside the counts, and reports the identity ledger - new / resolved / unchanged / reintroduced - beside the delta table; the roadmap's acceptance test is a named contract (`test_roadmap_acceptance_swap_reports_churn_at_flat_counts`): one old finding removed, one new added, flat total, verdict OK with 1 resolved + 1 new named. Reintroduced debt is reported, never gating; the v1 file format is rejected (exit 2, re-record) |
| Per-project baselines | Monorepo projects cannot hide a regression behind another project's improvement  | Shipped in v2.16.0: the floor is per project - `project_of` derives the monorepo dimension from the first path segment holding a family manifest (dotnet globs included), unmanifested files stay the root project, and the delta table and the identity ledger are both keyed by project; a project pushed above its own count fails even when the repo total drops |
| New-code gates        | Duplication density, debt ratio, LCOM4 and coupling join cognitive/cyclomatic on the merge-base diff | Shipped in v2.15.0: `--max-new-duplication` (duplicated long lines over non-blank added lines), `--max-new-debt` (findings on added lines priced by the debt engine's minutes over the SQALE cost of the added lines) and `--max-design-findings` (low-cohesion, high-coupling, unstable-hub verdicts anchored on added lines) in `new_code_gate.py`, wired through the `new_code_*` inputs of both reusable workflows with defaults 5% / 5% / 0; the scan runs once and feeds all three finding-based ceilings |
| Durable history       | Metric snapshots in a versioned store (dedicated branch), not just CI outcomes   | Shipped in v2.16.0: `slop_snapshots.py` - a versioned JSONL store (schema 1, eleven declared metric fields with directions, absent metrics omitted never zero-filled) with record / trend / diff and a SARIF bridge (`slop_snapshot_extract.py`) feeding it from the scan; the `metric-snapshots` job appends one row per run to the dedicated `quality-store` branch (orphan first run, worktree append after) and renders the trend into the step summary - the cache-borne outcome trend keeps its job, the measured numbers now outlive cache eviction |
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
6. Python monorepo discovery - shipped in v2.16.0 beyond the named
   gap: the python, csharp, ruby and php jobs all discover per project
   like node, rust, go and jvm (the JVM detect shape: zero projects is
   a config-only trigger, skipped with the reason); ruby keeps its
   root Rakefile/gemspec fallback. C/C++ with build-graph analysis
   stays the breadth item (clang-tidy and CMake discovery).

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

The seed shipped in v2.16.0: `fixtures/bench/` carries all twelve rows
as declared-outcome fixtures (`manifest.json` per slug: expected
detected / clean / pending with the reason) and `fixture_benchmark.py`
judges every shipped engine against the declarations - ten verdicts
proven (duplicate implementation, low cohesion, unused function,
dynamic registration stays clean, short-but-complex, abstraction chain,
unnecessary dependency, framework wrapper stays clean, cycles, churn
hotspot on a real git history built in temp), two declared pending with
their reason (context-aware file verdicts and test redundancy are later
phases). Exit 0 with pendings, 1 on any failed declaration, 2 on a
malformed manifest; the fixtures never carry git internals.

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
