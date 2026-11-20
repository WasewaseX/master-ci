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
| Finding fingerprints  | The quality ratchet reports new / resolved / unchanged / reintroduced from stable identities (rule id, project, file, symbol, normalized fingerprint, severity, rule version) | Shipped in v2.16.0: the ratchet's schema v2 carries, per project, the full identity set (`project\|check\|path\|fingerprint` - lines excluded because they drift) beside the counts, and reports the identity ledger - new / resolved / unchanged / reintroduced - beside the delta table; the roadmap's acceptance test is a named contract (`test_roadmap_acceptance_swap_reports_churn_at_flat_counts`): one old finding removed, one new added, flat total, verdict OK with 1 resolved + 1 new named. Reintroduced debt is reported, never gating; the v1 file format is rejected (exit 2, re-record) |
| Per-project baselines | Monorepo projects cannot hide a regression behind another project's improvement  | Shipped in v2.16.0: the floor is per project - `project_of` derives the monorepo dimension from the first path segment holding a family manifest (dotnet globs included), unmanifested files stay the root project, and the delta table and the identity ledger are both keyed by project; a project pushed above its own count fails even when the repo total drops |
| New-code gates        | Duplication density, debt ratio, LCOM4 and coupling join cognitive/cyclomatic on the merge-base diff | Shipped in v2.15.0: `--max-new-duplication` (duplicated long lines over non-blank added lines), `--max-new-debt` (findings on added lines priced by the debt engine's minutes over the SQALE cost of the added lines) and `--max-design-findings` (low-cohesion, high-coupling, unstable-hub verdicts anchored on added lines) in `new_code_gate.py`, wired through the `new_code_*` inputs of both reusable workflows with defaults 5% / 5% / 0; the scan runs once and feeds all three finding-based ceilings |
| Durable history       | Metric snapshots in a versioned store (dedicated branch), not just CI outcomes   | Shipped in v2.16.0: `slop_snapshots.py` - a versioned JSONL store (schema 1, eleven declared metric fields with directions, absent metrics omitted never zero-filled) with record / trend / diff and a SARIF bridge (`slop_snapshot_extract.py`) feeding it from the scan; the `metric-snapshots` job appends one row per run to the dedicated `quality-store` branch (orphan first run, worktree append after) and renders the trend into the step summary - the cache-borne outcome trend keeps its job, the measured numbers now outlive cache eviction |
| Analyzer profiles     | Qodana-style per-language rules, severities, scopes, thresholds, baseline-aware gates | Shipped in v2.17.0: `slop_profiles.py` - analyzer profiles as data (`ProfileSpec`: per-check downgrade-only severities, scope exclusions, budget overrides, loaded from JSON or YAML with fail-closed validation against the one check-id registry); `apply_profile` returns every silenced finding as a visible `Suppression` (a profile relaxes noise, it never invents severity), `DEFAULT_PROFILE` is a proven no-op, the shipped budget identity (density 300/5.0, coupling 30, hotspot 1200) is pinned by contract test, and the density pair flows end-to-end through `density_findings(budgets=...)`; `--profile` applies before baselines, ratchets and gates |
| Refactoring proposals | Reviewable PRs with evidence - the quarantine-PR pattern generalized to quality debt | Shipped in v2.17.0: `slop_refactor.py` generalizes the quarantine-PR pattern to quality debt - one debt-priced, reviewable markdown draft per finding at `proposals/<check>/<slug>.md` plus a sorted `index.md` and schema-stamped `bundle.json`, every fact sourced verbatim from the Finding, the slop_remedies registry and slop_debt's pricing (an unpriced check id fails closed before any write), byte-identical regeneration behind a `.slop-proposals` marker - local files only, no push, no PR, no permissions beyond its own output directory, so the emission step never repeats the v2.16.0 `contents:write` failure; `--proposals DIR` |

Acceptance test: remove one old finding and introduce another under
the same rule. The baseline must report one resolved and one new
finding even though the total count never changed.

## P2: language breadth (ordered)

Use each ecosystem's native engines; do not duplicate their analysis.

1. C# with Roslyn.
2. Java and Kotlin with build/compiler metadata.
3. PHP with PHPStan and Composer metadata.
4. Ruby with native parser and dependency metadata.
5. C/C++ with clang-tidy and build-graph analysis - shipped in v2.17.0:
   the `cpp` family's declared-name contract completed (every directory
   holding a `CMakeLists.txt` is a project, nested projects
   independent, declared `project()` name with a dir-name fallback)
   and the new `quality-cpp` job running clang-tidy per project over
   the exported `compile_commands.json` (translation units from the
   build graph, headers via `-header-filter`), `contents: read`
   exactly, zero projects a config-only skip, and every project's
   outcome named in the step summary - findings counted, skips loud
   with the reason.
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

The seed shipped in v2.16.0 and completed in v2.17.0: `fixtures/bench/`
carries all twelve rows as declared-outcome fixtures (`manifest.json`
per slug: expected detected / clean / pending with the reason) and
`fixture_benchmark.py` judges every shipped engine against the
declarations - twelve verdicts proven, zero pending: duplicate
implementation, low cohesion, unused function, dynamic registration
stays clean, short-but-complex, abstraction chain, unnecessary
dependency, framework wrapper stays clean, cycles, churn hotspot on a
real git history built in temp, the context-aware file verdict
(`long-but-cohesive` - the file-length check consults the LCOM4
cohesion oracle and the ordinary size-only flag stays silent) and test
redundancy (`redundant-test` - exact normalized duplicate Python test
bodies, test paths only). Exit 0 when every declaration holds, 1 on
any failed declaration, 2 on a malformed manifest; the pending
vocabulary stays for future deferred rows; the fixtures never carry
git internals.

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


## v2.18 - the trustworthiness round (audit-driven, 2026-11-17)

An external audit inspected the implementation and named eight
findings; six shipped, each reproduced against source before the fix:

| # | Finding | Verdict | Fix | Commit |
| - | ------- | ------- | --- | ------ |
| A | JS phantom-import checks the package name, not bindings | Confirmed: 8/10 repro cases wrong | binding-aware import analysis, 7 regression tests | e72f3d0 |
| B | profile budgets accepted for coupling/hotspot, never wired | Confirmed: engines had no budget input | both engines take budgets; byte-identical no-op | 15ba396 |
| C | reliability rating is SQALE math on SonarQube's reliability label | Confirmed | severity-based grade, own documented weights; minutes become informational share | 0844980 |
| D | duplication density is repeated-line counting, presented as clone detection | Confirmed | dup-block-density: contiguous 6+-line runs, own budget, calibrated 2.0% | ad13944 |
| E | repo-wide metrics silently lose parse-failed/unsupported files | Confirmed | measure_corpus five-state coverage + analysis-coverage verdict | ad13944 |
| G | snapshots scrape SARIF messages; healthy values lost | Confirmed | metrics-v1 structured document, extractor prefers it; SARIF blocking count was latent-zero, fixed | bc83864, cdb081b |
| H | fixture benchmark proves outcomes, not accuracy | Confirmed by the docstrings themselves | accuracy harness + labeled corpus + clean controls + promotion rule | 761c310 |
| - | watchdog pin grep misses the v2.8+ call shape | Confirmed: hosted #10/#11 failed, consumer green | parser accepts any master-ci pin + template_ref | b57e868 |

Remaining rows (evidence-driven, in the audit's P2/P3 order):
language-native symbol resolution (TypeScript compiler API, Roslyn,
clang tooling) replaces the name-search heuristics as each language's
accuracy evidence justifies it; corpus widening per
benchmarks/accuracy/README.md; CI modularity and caching after the
measurements are trusted.

## Round-8 - the scanaislop bulk adoption (2026-11-17)

The user directive: reverse-engineer the aislop rule catalog
(github.com/scanaislop/aislop, 66-rule ai-slop engine) and bulk-add
everything the engine does not yet cover. The six prior adoption
rounds had taken the catalog's first 30 rules; this round took the
rest that fits a zero-dependency oracle - 27 new check ids in four
modules:

| Batch | Module | Checks |
| ----- | ------ | ------ |
| C# column (13) | slop_csharp.py | async-void, broad-catch, console-leftover, empty-catch-rethrow, if-ladder, index-loop, linq-count, not-implemented, null-forgiving, redundant-doc-comment, string-concat-in-loop, suppressed-warning, sync-over-async |
| C/C++ column (8) | slop_cpp.py | c-style-cast, define-constant, endl-in-stream, iostream-leftover, manual-delete, not-implemented, null-literal, using-namespace-std-in-header |
| TS precision (2) | slop_ts_checks.py | redundant-type-coercion, hidden-fallback |
| Cross-file (1) | slop_type_decls.py | duplicate-type-declaration |
| Family columns (3) | slop_family.py | go-library-panic, rust-unwrap, scala-broad-catch |

Not adopted, with the reason recorded: `unused-css` (needs a
project-wide CSS reference graph, a different oracle class than the
line engine owns), `duplicate-type-declaration` structural equality
(textual normalization only), and the three TS cast/directive rules
already covered by the combined `ts-suppression` check since round 2.

Discipline kept from the v2.18 thesis - the bulk-add did not bend a
single measurement law:

- every check advisory by registry law; the reliability rating prices
  exactly the seven failure-path ids it categorizes;
- 29 new contract tests (934 -> 963) pin each positive, nearest
  negative and the advisory flag;
- the dogfood self-scan is byte-identical to the v2.18 baseline
  (blocking 0, 12 advisory findings, all dimensions A) - the engine
  swallowed its own medicine twice: four new functions were flattened
  under the cognitive budget 15, and the test module loads per the
  conftest convention after the boilerplate rule flagged its preamble;
- fixture benchmark 12/12, accuracy clean controls untouched (zero
  round-8 findings), ruff clean, bloat budgets held (all new modules
  under 400 non-blank lines).

Deliberate language limits, stated per rule in the module docstrings:
the oracles are line/regex-grade, not Roslyn/clang/tree-sitter-grade;
each finding names the pattern, not the intent. Language-native
parsers replace these when the accuracy evidence justifies them
(audit P2 order, unchanged).

## Round-9 - the catalog closes (2026-11-17)

A re-audit of the scanaislop catalog against the shipped registry found
the 58-pattern matrix already 56/56 semantically covered: rounds 2-8
had adopted every rule under another id (`ts-suppression` carries the
cast/directive rules since round 2, `undeclared-import` is the
hallucinated-import oracle, `rust-stub` the Rust todo macros,
`trivial-comments` grades narration, the `todo-density` +
`placeholder-impl` + `stub-function` trio grades stub markers). Two
genuine gaps remained, both Python, both adopted in `slop_py_family.py`
(the family column split out of `slop_py_checks` under the 400-line
budget):

| Gap | Oracle | Exemptions |
| --- | ------ | ---------- |
| `broad-except` | typed handler naming Exception/BaseException, reliability-advisory like its C# sibling | lone re-raise (translation point), coded `# noqa: BLE001` (declared intent), pass-only handlers (swallowed-exception owns them) |
| Python `debug-print` | print() calls in production modules, AST-backed, advisory file-level count | test paths, script dirs, entrypoint basenames, main-guard files |

Discipline kept: 16 contract tests (963 -> 979); fixture benchmark
extended to 50 rows, all PASS; accuracy `broad-except` and
`debug-print` at precision 1.00 recall 1.00 over one judged row each;
clean controls untouched; ruff clean; and the dogfood self-scan stays
byte-identical to the v2.18 baseline (blocking 0, 12 advisory
findings) after two of the engine's own best-effort handlers gained
their declared intent - the only source edits the new rule demanded
of the codebase that ships it.

One environmental note recorded for honesty: the 30-test red run this
round began from was the sandbox missing `lizard` and
`tree-sitter-language-pack` (both declared in pyproject.toml), not a
regression - the full suite is green with the declared dependencies
installed, and the sandbox is now provisioned to match CI.


## Round-10 - the accuracy corpus deepening (2026-11-18)

No new checks. The round the audit thesis asked for after the catalog
closed: the promotion rule (`precision >= 0.8 across >= 5 judged rows`)
was honest but unfalsifiable at one judged row per check - every
verdict read `advisory-only (insufficient accuracy evidence)` because
the corpus was a seed, and no round had grown it. Twenty labeled
fixtures grew the corpus 46 -> 66 rows and pushed four checks to the
threshold:

| Check | Rows before | Rows after | Verdict after |
| ----- | ----------- | ---------- | ------------- |
| `csharp-async-void` | 1 | 5 | promotion-ready evidence |
| `csharp-broad-catch` | 1 | 5 | promotion-ready evidence |
| `rust-unwrap` | 1 | 5 | promotion-ready evidence |
| `debug-print` | 1 | 5 | promotion-ready evidence |

Each column gained realistic positives (the fire-and-forget queue
worker, the log-and-default catch, the map-lookup unwrap, the service
print) and the nearest negative its exemption logic needs protected
(the On-prefix handler, the exception-reading catch, the documented
`expect()`, the stderr writer). Labels were derived from the oracle
sources before the engine ran, per the corpus protocol; all twenty
rows passed on first judgment, so no label was rewritten to fit.

The tranche tripped the engine's own laws twice - both fixed in kind:
the round-10 wiring pushed `fixture_benchmark.py` to 415 non-blank
lines against its 400-line bloat budget (the single-engine probes
moved to `fixture_bench_fixtures.py`, runner back to 396), and the
tenth copy of the contract-test scan helper crossed
`boilerplate-duplication`'s 10-line threshold (the helper moved into
`detector_module.py`, the one-place module the conftest docstring
established; round-7/9/10 test files import it). The dogfood self-scan
is back at the v2.18 baseline (blocking 0, 12 advisory findings, all
dimensions A), the fixture benchmark reads 70/70 PASS, the suite is
998 green, ruff clean.

Left honest and open: promotion itself is a registry-law act, not an
evidence accident - the four checks stay advisory until the
maintainer promotes them with the corpus now able to object. The
other 36 judged columns still sit below five rows; the next corpus
rounds grow them the same way.

A CI note recorded for honesty: the first push carrying both commits
(af025c5) failed the hosted python job's mypy step - and the failure
was round-9's, not round-10's. c26a7bd had never been pushed before,
and the two intervening runs were path-skipped docs-only commits, so
the round-9 fixtures never met the hosted mypy: two service.py files
and two worker.py files in fixtures/bench collided as duplicate
modules, and a masked second error (an undefined Task name) surfaced
once the first cleared. Fixed in 7a37557 by renaming the four fixtures
to unique module names and giving the guarded fixture a real Task
class; mypy now passes the CI invocation over 163 files.

## Round-12 - the scanaislop claims triage (2026-11-18)

The competitor scanned this repo and published 18 claims: four
`complexity/file-too-large`, eleven `ai-slop/unused-import`, three
`ai-slop/python-repetitive-dispatch`, each triaged here against the
real source before anything was fixed - the audit's rule that a claim
is a hypothesis until reproduced held.

The file-too-large group was a metric disagreement, not a defect: the
four scripts (fixture_benchmark, slop_cycles, slop_dead,
slop_structural) measure 385-392 non-blank lines against our documented
400 non-blank budget, while scanaislop counts total lines (441-460).
The contract in .ai-integrity-ignore ("no scope-offs: scripts/ is a
package of modules, each inside the 400-line file budget") is satisfied
under the metric the verdict messages state. No change.

The unused-import group was false on every one of its eleven rows.
`ai_slop_detector.py` is the vendored facade foreign repos import by
path; every flagged line carries `# noqa: F401` under a documented
re-export contract, and the flagged symbols resolve to real consumers
tests reach through importlib (`mod.check_class_cohesion`,
`mod.ArchConfigError`) - dynamic access a static import graph cannot
see. But the triage surfaced one genuinely dead binding the claims'
frame hid: `slop_scan.py` imported `ArchConfigError` it never used,
masked by its own blanket noqa. Removed. And the narrative contract
"every public name is re-exported here" turned out to be false in
twenty-four places - `tests/test_facade_surface.py` now asserts the
facade re-exports every top-level `check_*` in every `slop_*` module
(61 parametrized assertions), so the surface is machine-checked, not
narrated.

The repetitive-dispatch group was the real gap, half already closed:
round-2 adopted the check (LADDER_MIN 4) and the self-scan already
fired on slop_corpus.py:211 - but the detector only walked elif
chains, and flaky_parsers.py's `parse_file` dispatched through four
SEPARATE guard-ifs with returns, exactly the shape scanaislop caught
and we could not. The dispatch family moved to its own module
(`slop_dispatch.py`) - slop_residue.py was at 452 non-blank lines
against its 400 budget - and the guard-run detector landed: a run is
consecutive sibling ifs, empty else clauses, one subject, every branch
leaving the block (return/raise/continue/break), because guards that
do not exit are conditional features and flagging them would be the
false-positive factory this rule must never become. The two flagged
ladders were refactored to dispatch tables (`_PARSERS` in
flaky_parsers.py, `_EXCLUSION_COUNTERS` in slop_corpus.py) so the
engine's own source passes the rule it now enforces.

The round also caught the engine in a false positive of its own: the
self-scan's `undeclared-import` on accuracy_benchmark.py:8 read the
docstring sentence "from a small seed:" as an import of a module named
`a` - a line regex over raw text. The inventory is AST now
(`_py_import_tops`); prose cannot become an import, and a parse failure
still reports through analysis-coverage rather than passing silently.

"Hold every pull request to this" is wired, not promised: the
post-fix findings (11, all pre-existing debt) are recorded as
`.quality-baseline.json` (schema v2: 5 check floors, per-finding
identities), the ratchet defaults to auto, and a probe run proved both
directions - a planted four-branch ladder exits 1, the clean tree exits
0. New dispatch, unused-import, over-budget-file or undeclared-import
findings now fail CI instead of joining the advisory pile. Along the
way the sandbox reproduced the hosted gates exactly (pinned lizard,
tree-sitter, ruff 0.17.4, mypy) and turned the format gate green: nine
files had drifted from `ruff format` before this round - seven of them
untouched since earlier rounds - all nine reformatted, `ruff check .`
and `ruff format --check .` and mypy (89 files) all pass.

Final state: 1109 tests green (61 new facade-contract, 5 new
dispatch/undeclared regressions), self-scan blocking 4 / advisory 12 /
verdict CLEAN with maintainability A and zero findings attributable to
the round, ratchet floor recorded and proven to bite.

## Round-13 - the C/C++ column, the blind spot, and two defects the round itself caught (2026-11-18)

The evidence-driven rows left after v2.18 named corpus widening as
the next act; the round opened by pushing the hosted state and
learning why it had sat unverified for two hours - the first push
carrying rounds 11-12 red'd the hosted hygiene job's editorconfig
step: 11 "wrong amount of left-padding spaces" errors across
hook_gate.py, hook_install.py and slop_catalog.py. Round-11's
docstrings and recipe templates align prose at 18, 25 and 2 spaces -
none a multiple of the [*.py] indent_size 4 the repo's .editorconfig
declares, and no sandbox gate sees it: ruff format never touches
string contents, mypy never reads prose. The same blind spot had
red'd main once before (96c6cb5); now twice. Fixed in kind: the
continuations moved to the nearest multiple of four with the labels
re-padded to keep the alignment, editorconfig-checker v4.0.2 exits 0
over the tree, and the parity rule moved into the suite
(tests/test_editorconfig_compliance.py: Python left-padding multiples,
trailing whitespace, final newline) so the third occurrence fails in
pytest before it fails on a runner.

Running the documented reproduce command for the accuracy benchmark -
the audit's own discipline, execute the docs - surfaced a defect the
suite could not see: `accuracy_benchmark.py --json` crashed with
AttributeError because main() passed the CLI's string argument where
run_accuracy_benchmark expects a Path. The test calls the function
directly with a Path, so the CLI contract was never exercised; fixed
by converting at the boundary, and the documented command now runs.

Writing the round's fixtures then found a second defect by the
protocol's own route: slop_cpp.py declares .cxx and .hxx inside
CPP_ONLY_EXTS and HEADER_EXTS, but neither CODE_EXTS nor FAMILY_OF_EXT
carried them - a .cxx translation unit or .hxx header never reached
the cpp scanner at all (unknown file type, family ""), so the rules
that named those extensions could never fire on them. Both maps
gained the two extensions, and the general contract is now pinned
(tests/test_extension_parity.py): every extension any family rule
names in its scope must be read by the scanner and dispatched to a
family - a scope the scanner cannot reach is a rule that lies about
its own coverage.

The corpus tranche widened the C/C++ column: 40 new fixtures - for
each of the eight cpp checks, four diverse real-shaped positives
(driver cast, sqlite pointer cast, ratio promotion, size_t wire
length; config timeout, port, DSP factor, platform-limit macro;
ofstream logger, report builder, report writer, transcript; service
trace, inspector dump, parser cerr, two-site store prints; raw-new
cache, early-return parser, plugin release, two-resource teardown;
interface stub, platform gap, assert stub, optional capability; legacy
call, member init, handle reset, header default argument; widget,
util, logger and geometry headers) and one nearest-negative clean row
per check pinning the exemption logic - named casts, constexpr plus a
function-like macro, the newline literal, the main()-owning CLI, the
unique_ptr owner, the real error throw, the nullptr boundary call,
the translation-unit directive. Labels were declared from the rule
contracts before the engine ran; the engine agreed with all forty on
the first run (110 fixtures: 110 pass, 0 fail, 0 pending) and the
accuracy report shows the whole eight-check column at the
five-judged-row promotion threshold with precision 1.00 and recall
1.00 - twelve of forty checks now carry promotion-ready evidence,
zero corpus violations, and the promotion-verdict pin in
test_accuracy_benchmark.py grew to the exact twelve.

The round also paid the engine's oldest baselined debt: the
boilerplate-duplication finding (the two-line sys.path preamble
repeated ten times across the grandfathered test modules - the
twelfth copy had tripped the rule, ten more were frozen in the
baseline). The ten migrated off the preamble; conftest is now the
only bootstrap for every test module, the finding is gone, and the
baseline rewrite records it in resolved_history - a reintroduction
will be named as such, and the floor for the check is gone so one
copy bites immediately. The self-scan moved from blocking 4 /
advisory 12 / code-health F(86) to blocking 0 / advisory 12 /
code-health A(0) - the round's own new comment block was caught by
prose-block before the commit and tightened, so the round ends with
fewer findings than it started and none of its own.

The audit's P3 row (CI caching, ordered after the measurements
became trusted) took its first slice: the survey found exactly one
caching site in twenty workflows - the quality-history trend file -
while the four heaviest installs re-pulled tree-sitter-language-pack
and the rest of the pinned parser set on every run. Each install now
sits behind an actions/cache step keyed on the helpers'
tool-versions.yml hash (a new pin invalidates, the same pin restores,
the install stays the source of truth on a miss), the workflow
contract test's config-reference capture was made quote-aware (the
first quoted hashFiles reference false-failed a \S+ capture), and
actionlint passes over all twenty workflows.

Final state: 1113 tests green (2 editorconfig-parity, 2
extension-parity, benchmark pins grown), ruff clean, mypy 138 files
clean, editorconfig-checker exit 0, hosted CI green on the red-repair
commit 46605c4, fixture benchmark 110/110 PASS, accuracy report
zero violations with twelve checks promotion-ready. Left honest and open: the 28 remaining columns below
five rows (the 13-check C# column's other eleven, TS precision, the
family columns, the quality dimensions) grow the same way; promotion
itself stays a maintainer act the corpus can now object to in twelve
places.

## Round-14 - the claims paid in kind (2026-11-18)

Round-12 triaged scanaislop's claims against source and won the
argument: the file-too-large rows counted total lines where our
`bloat-long-file` budget counts non-blank lines, and the facade
re-exports carried `# noqa: F401` our own binding-aware phantom-import
check honors. The user re-ran the tool and pasted the same rows back
with a fair question: why do the warnings persist? An argument won is
not a repo clean. This round paid the claims in kind - the repo is
now clean under the competitor's own rules, in the competitor's own
production-path scope, verified by a replica ported from their
shipped TypeScript (`src/engines/ai-slop/unused-imports.ts`,
`src/engines/code-quality/complexity.ts`), not from their report.

The facade (scripts/ai_slop_detector.py) now declares every re-export
with the PEP 484 redundant-alias convention - `from X import Y as Y`.
That convention is the machine-readable statement "this module
publishes the name", and scanaislop's unused-import rule skips a
redundant alias explicitly (their extractor, line 108-109); ruff and
the engine's own phantom-import check stay quiet via the same scoped
noqa as before. One surface, three scanners, zero disagreement. The
facade contract test (test_facade_surface.py) passes unchanged - the
alias form binds the same names.

Four modules that had grown past the 400-total-line mark in the
claims' counting split along their seams, the same split the engine
enforces on consumers: slop_structural.py (451) moved the string-aware
comment blanker and the shared code-line view into slop_code_lines.py
and kept the budget law and the block scanners; slop_cycles.py (460)
moved the per-language edge extractors into slop_cycle_edges.py and
kept the graph, Tarjan's SCC and the check; slop_dead.py (441) moved
the orphan-module half into slop_orphan.py, which also absorbed the
dead MAX_ORPHANS constant and finally wires it into its own cap;
fixture_benchmark.py (470) moved the Probe table and the per-tranche
registrations into fixture_bench_probes.py and kept the judge, the
renderer and the CLI. Every new module sits at or under 280 total
lines; no public check name moved facade-ward, so no consumer import
changed except the two that name the moved check directly
(slop_scan.py, and the facade's own check_orphan_modules source).

Evidence: 1113 tests pass (the one honest touch: the redundant-tests
fixture test reached a probe through the runner module's namespace and
now names the probes module); ruff check and ruff format --check clean
over 153 files; mypy clean over 94; self-scan verdict CLEAN with a
floor byte-identical to the committed .quality-baseline.json; the
quality ratchet exits 0 in enforce mode; the fixture benchmark runs
110/110 PASS; editorconfig-checker v4.0.2 exits 0. The replica scan -
their unused-import extractor and their file-too-large budget and
trigger, over their production-path scope - reports zero claims, where
the same replica run before this round reproduces the user's paste
exactly (4 too-large, 16 unused-import, all in scripts/).

## Round-15 - the C# column grows up (2026-11-18)

The corpus protocol's named next act: the eleven C# checks that had
carried a single judged row since round-8 each gained four diverse
real-shaped positives - the pricing service's Debug.WriteLine, the
bare catch that rethrows, the five-branch status ladder, the index
for-loop over Count, the .Count() yes/no guard, the NotImplementedException
stub, the null-forgiving bang on a config section, the quadratic +=
of a literal in a loop, the .Result read and the GetAwaiter().GetResult()
gate, the bare #pragma warning disable, the summary that restates
"Gets the name." - plus one nearest-negative clean row per rule pinning
the exemption logic the way round-13 pinned the C/C++ column's. Labels
were written before the engine ran; 165/165 fixtures judged PASS on the
first run, zero violations, zero label disputes. The accuracy report now reads
precision 1.00 recall 1.00 judged 5 on every one of the eleven
columns: twenty-four checks carry promotion-ready evidence, up from
thirteen. The honesty pins moved with the corpus (the promotion-set
pin grew from twelve to twenty-four and its comment names round-15;
the fixture-count pin reads 165; the tiny-fixture pin caught two
30-line files at exactly its boundary and the fixtures were trimmed
instead of the pin). The promotion act itself stays a reviewed decision
per the protocol - the evidence is banked, the flip is not made here.

## Round-16 - the catalog exhaustively adopted, the platform reverse-engineered (2026-11-19)

Two directives, both delivered whole. First, the scanaislop catalog
diffed rule-by-rule against the engine's 138 checks: twelve rules had
never been adopted, and all twelve shipped in this round - the security
column (slop_security.py: innerhtml, dangerously-set-innerhtml,
sql-injection, shell-injection, unsafe-deserialization, unsafe-c-call,
hardcoded-secret), the TS compiler-bypass assertions and the repeated
call-chain identity (slop_ts_checks.py), and the two oracles that need
the whole repository as evidence (slop_ecosystem.py: unused-css with
its errs-alive evidence rule, vulnerable-dependency as an opt-in
pip-audit conversion that leaves skipped analysis to the gate layer).
Blocking grade rides the security column; the judgment calls stay
advisory. The contract tests caught three real oracle defects before
any push - blanked-string literals reading as non-literal commands,
set-membership standing in for substring evidence, and chains matched
without their receiver. The self-scan stays clean under all twelve.

Second, Semaphore CI/CD researched across ~32 live doc pages and
reverse-engineered into four contract-tested tools plus the research
map (docs/semaphore-parity-research.md): the parameterized promotion
model with its conditions-DSL subset and regex-validated parameters
(.semaphore/promotions.yml declares this repo's own two promotions),
the compile-time secret contract (sem_secrets_lint.py - which caught
the repo's own dockerhub secrets undeclared on its first run), the
JUnit compile/combine/pipeline-report triad with the failed-first
slowest-first report and the flaky-overlaps-failed semantics, and the
Insights collector reproducing Semaphore's four metrics including the
exact cancelled-runs exclusion rule in reliability. The wiring is real:
the compile-time contracts run in ci.yml's fixtures job, the insights
and report run in the nightly. Skipped features (SSH debug, machine
sizing, billing) are named skips with reasons, not silence.

Regression held: 1202 tests green (up from 1113 - 48 parity-column
contracts and 31 semaphore contracts added), ruff clean, mypy clean
over 101 source files, self-scan verdict CLEAN, and the pre-existing
sandbox defect (the missing lizard/tree-sitter analyzer group making
every parse "fail") diagnosed and fixed before any of the round's
evidence was trusted.

The hosted gates held the round to its own standard twice. The first
push (70888df) red'd in three: the quality ratchet caught the round's
own code (eight functions past the 15-cognitive budget, four mains
duplicating one signature past the boilerplate threshold, six em-dashes
in the research doc, one over-long comment), gitleaks caught the
synthetic credentials the new contract tests carry, and the format
check caught unformatted files. The second push paid the debt the
repo's way - refactor, not re-record: metrics_for split into
_mttr_minutes/_reliability/_recent_count, combine grew _merge_one,
check_unused_css grew discovery/corpus/orphan helpers, the shell and
deserialization oracles became table-driven per family, the promotion
validators grew per-concern helpers, secrets_lint.scan grew
_audit_one_workflow and _fork_exposure. high-cognitive 9 to 1 (the
baseline's own), high-cyclomatic 6 to 0, deep-nesting gone, boilerplate
gone, prose-block back to its recorded 7 - the ratchet exits 0 with
the floor byte-identical, no baseline move. gitleaks passes with the
documented .gitleaks.toml mechanism: the synthetic credentials are
allowlisted by path, default rules extended, values in no workflow,
script or doc. Hosted CI green on febc362: CI, Master CI Selftest,
Metric Store and the Selftest Negative Harness success, the
Guardrails red is its designed red. Seven open dependabot PRs were
rebased onto the fixed base - their earlier reds were the ratchet
correctly refusing an outdated base.

## Round-19 - the inventory round: batch 2 closed, batch 3 opened (2026-11-20)

PR #23 (round-18, batch 2's four features plus the greptile rounds'
fixes) merged by rebase after the second greptile pass's three findings
were fixed - the learnings writer now obeys its loader's contracts,
the diagnoser keeps every learned fix and counts unknown lines per
line across jobs. Round-19 then landed four more roadmap items:

- **A19 - SBOM and license policy** (`sem_sbom.py`): six ecosystems,
  SPDX-2.3-shaped deterministic documents (digest-named, constant
  creation stamp, sorted rows), declared licenses or NOASSERTION,
  fail-closed policy with allow/deny/deny_unknown and a
  contradiction-is-config-error rule; unparseable manifests are data
  gaps. Ten contract tests pin the shapes, the determinism, the exit
  codes (0/1/2) and the gap behavior.
- **A20 - the holdout split** (`sem_holdout.py`): sha256(salt:id)
  splits, purity and disjointness test-pinned, the accuracy-real-v1
  manifest fail-closed (schema, provenance, 40-hex commit, label
  verbs), missing vendored fixtures recorded as gaps, present ones
  judged per label. The manifest ships with zero repos and the row
  stays partial - no real-world claim without a vendored corpus.
- **A9 - near-duplicate clones** (`slop_similarity.py`): pair-shingle
  anchors vote on alignments, blocks grow inside a mismatch ratio, the
  similarity is the evidence. Two fixture rows joined the benchmark
  table (167 fixtures now), the clean controls stayed clean, and the
  detector's own scan cost was measured A/B on the synthetic corpus:
  136.1 with the check, 135.5 without - under one percent.
- **C43 - rerun inventory** (`sem_reruns.py`): run_attempt > 1 jobs
  named with recovery verdicts, missing attempts as data gaps,
  deterministic output, run-level payloads refused nothing silently.

The round paid its own ratchet, the second time in three rounds the
repo's gates caught the round's own code: seven functions past the
cognitive/cyclomatic/nesting budgets, one chained-dict-get, one
environment URL baked into the SBOM namespace (rebuilt as a spec-valid
URN), the test preamble repeated past the boilerplate floor (the
round-13 conftest convention was ignored by the four new test modules
- they were migrated off it), and both CLI signature groups pushed to
ten identical lines. Every row was paid by refactor, not re-record -
the single baseline move is the new check's own three advisory rows,
the documented catalog-adoption path: `near-duplicate-clone` joins the
floor at its honest count with identities pinned, nothing else moved.

One verification discipline note, recorded because it almost cost the
round a phantom bug: the selftest workflow's push filter rendered as
`branches: ain]` in tool output for an entire working session. Before
claiming the defect, the raw bytes were checked - the line is
`branches: [main]`, and the "ain]" was the renderer swallowing the
`[m`. A hardening contract test now pins every `on.push.branches` /
`on.pull_request.branches` filter to be a YAML list of real branch
names, so a genuinely mangled filter of that shape can never merge -
but the test's honest description is prevention, not a fix: no such
defect existed on main.

Regression held: 1425 tests green (up from the merged baseline),
ruff check and format clean, mypy clean over 221 source files, the
accuracy benchmark exit 0 with the clean controls clean, the fixture
benchmark 167/167 pass, the ratchet exit 0, the self-scan verdict
CLEAN grade A exit 0, and hosted CI verified by step name after the
push.

## The 50-feature reverse-engineering program (2026-11-19)

The external spec lists fifty features across three reference
families: the SonarQube/Qlty/Qodana quality engine (A1-A20), the
CodeRabbit review platform (B21-B39) and the Semaphore pipeline model
(C40-C50), and demands each one either exists here or goes on the
build plan. The status column was audited feature-by-feature against
this tree on the date above: every "shipped" row names the module or
workflow that carries it, every "partial" row says what is missing,
and every "missing" row lands in a numbered batch. The audit method
was grep plus source reads over `scripts/`, `tests/`, `docs/` and
`.github/workflows/` - no status was copied from prose.

The build order follows value and integration cost, fastest first:
batch 1 was round-17's slice plus the two validators the audit found
missing; batch 2 closed with round-18 and round-19; batches 3-4 stage
the review-platform features, which remain the one true gap (13 of 19
B-rows are missing or partial after round-19, plus the four open
A-rows; the C column has none left).

### A. Quality engine (SonarQube, Qlty, Qodana): 16 shipped, 4 partial, 0 missing

| # | Feature | Master CI asset | Status |
| --- | --- | --- | --- |
| A1 | New-code definitions and leak periods | `new_code_gate.py`, `fetch_diff_base.sh`, `slop_ratchet.py` | shipped |
| A2 | Separate new-code quality gates | new-code gate vs total-project verdicts in the report | shipped |
| A3 | Multi-condition gate engine | `required-quality.yml` composite + `slop_profiles.py` budgets | shipped |
| A4 | Inheritable quality profiles | `slop_profile_merge.py` (round-18): base -> project -> local chains, child-wins severities and budgets per key, scope-excludes union (a child never re-includes), every document loaded fail-closed and the merged output re-validated against the registries - scope keys and every scope glob through the loader's own pattern checks, so a constructed spec cannot smuggle an uncompilable pattern into a scan; scan-flag integration stays with the next slice | shipped |
| A5 | Per-rule remediation estimates | `slop_remedies.py` registry + `slop_debt.py` effort model | shipped |
| A6 | Independent quality dimensions | 8 dimensions in `slop_outputs.py` (architecture, code-health, reliability, maintainability, security, test-assurance, dependency-health, ai-integrity) | shipped |
| A7 | Coverage ingestion, new-code coverage | `coverage_gate.py`, `coverage_parsers.py` | shipped |
| A8 | Exact duplicated-block detection | `slop_duplicates.py` + `dup-block-density` (v2.18 audit D) | shipped |
| A9 | Near-duplicate (similarity-scored) clones | `slop_similarity.py` (round-19): pair-shingle anchors vote on (file pair, delta) alignments, a passing vote grows outward while the mismatch ratio holds, and a block reports with matched/length similarity in the message - the score is the evidence; same-file overlapping regions never accuse each other, one alignment reports once, findings capped at three best-score rows, advisory by registry; the exact end of the spectrum stays with `slop_duplicates.py` and `dup-block-density` | shipped |
| A10 | Cross-language class cohesion | `slop_lcom4.py` (Python AST; other families approximate) | partial - batch 3 |
| A11 | Multi-dimensional complexity | `slop_cognitive.py`, `slop_lizard.py`, `slop_bloat.py`, `slop_density.py` | shipped |
| A12 | Semantic dead-code detection | `slop_dead.py` (textual) + `semantic-dead-symbol` (parse-tree, round-17) | shipped |
| A13 | Unnecessary abstraction analysis | `slop_abstraction.py` (forwarding-chain, no-behavior-class) | shipped |
| A14 | Architecture and boundary enforcement | `slop_arch.py`, `slop_cycles.py`, `slop_coupling.py` | shipped |
| A15 | Analyzer registry with descriptors | `slop_catalog.py` carries 151 rows; no per-analyzer version/timeout/lifecycle descriptors | partial - batch 3 |
| A16 | File selection and analysis scope | `slop_files.py`, `path_filters.py` | shipped |
| A17 | CodeQL integration | `codeql.yml`, `codeql_detect.py`, SARIF in the required gate | shipped |
| A18 | Semgrep taint rule packs | `slop_security.py` sink column; interprocedural taint stays with Semgrep itself | partial - batch 4 |
| A19 | Supply-chain analysis | `slop_deps.py`, `slop_ecosystem.py`, `vulnerable-dependency`, plus `sem_sbom.py` (round-19): an SPDX-2.3-shaped SBOM over the six supported manifests (PyPI requirements/pyproject incl. poetry, npm package.json, go.mod, Cargo.toml, csproj, Gemfile) with declared versions verbatim, purls (scoped npm folded into a namespace, NuGet casing preserved), manifest-declared licenses, digest-named deterministic documents (identical trees render byte-identical), and a fail-closed allow/deny/deny_unknown license policy judged per package - an unparseable manifest is a recorded data gap, never a silent skip; resolution stays with the lock files | shipped |
| A20 | Real-world accuracy benchmark | `accuracy_benchmark.py` + labeled fixtures; the holdout split shipped in round-19 (`sem_holdout.py`): a case's split is a pure sha256 function of salt and id (two runs agree, no case in both), judged through the same labeled rows with per-split counts in a holdout-report-v1 artifact; the real-repo corpus protocol is implemented fail-closed (`benchmarks/accuracy/corpus-real.json`, accuracy-real-v1: upstream URL, exact 40-hex commit, labels written before the engine runs) and the manifest ships empty - a named-but-unvendored fixture is a recorded data gap, never a guessed pass; zero real repos are vendored yet, so real-world accuracy stays unproven | partial - batch 4 |

### B. Review platform (CodeRabbit): 6 shipped, 7 partial, 6 missing

| # | Feature | Master CI asset | Status |
| --- | --- | --- | --- |
| B21 | Structured PR summaries | `sem_pr_summary.py` (round-18): schema-v1 summaries stamped with the head SHA they describe (a missing SHA is refused at build), registry-dimension counts, blocking rows with locations, data gaps recorded as gaps; `--check-stale` answers whether the summary still matches a head, exit 1 when stale; blocking-ness follows each finding's reported severity (the registry only backs older rows without one, so profile downgrades and `--enforce-bloat` escalations read as reported), and a valid-but-empty findings document is a clean scan, not a data gap | shipped |
| B22 | PR walkthrough with grouped files | MCP prompts only (`mcp_prompts.py`) | missing - batch 3 |
| B23 | Change stack / logical cohorts | `sem_change_stack.py` (round-17): path-rule cohorts, foundation-first order, unclassified shown; real dependency ordering arrives with B25 slice 2 | shipped |
| B24 | Mermaid change diagrams | nothing; must stay optional and gate-neutral | missing - batch 4 |
| B25 | Repository code graph | IR v1 + `slop_symbol_graph.py` (first slice); graph persistence shipped in round-18: versioned JSON bundles (graph schema + IR version, both refused on mismatch), use counts and parse failures stored, def sites rebuilt from the files - the loaded graph accuses exactly what the built graph accused | shipped |
| B26 | Linked-issue requirement validation | nothing | missing - batch 4 |
| B27 | Repo coding guidelines / path instructions | `AGENTS.md` exists; no instruction engine | partial - batch 3 |
| B28 | Persistent reviewer learnings | `sem_ci_learnings.py` (slice, round-18): versioned learnings-v1 store of failure-signature lessons (pattern, the remediation that fixed it, the review that taught it), fail-closed loading, deterministic deduplicated recording, `--learnings` integration in `sem_ci_diagnose.py` with `learned` confidence; the writer is held to the loader's own contracts (required-field rule before any write, rendered document re-parsed through the loader before disk, refusals leave the file byte-unchanged); the cross-review memory lifecycle stays open | partial |
| B29 | Custom pre-merge checks | `hook_gate.py` per-edit gate; no declarative custom-check config | partial - batch 3 |
| B30 | Incremental review orchestration | CI re-runs only; no commit-aware review state | missing - batch 4 |
| B31 | PR merge-risk assessment | `sem_merge_risk.py` (round-17): model v1, registry-driven severity, evidence rows, data gaps reported as incomplete | shipped |
| B32 | PR triage queue | nothing | missing - batch 4 |
| B33 | Reviewer and label recommendations | `label.yml` (actions/labeler v7); no reviewer derivation | partial - batch 3 |
| B34 | CI failure diagnosis | `sem_ci_diagnose.py` (round-18): signature table over this repo's own gates (ruff, ruff-format, mypy, pytest, yamllint, markdown-lint, file-too-large, ratchet, pin-lag) driven from Actions jobs JSON or raw logs; step-name matches are exact (including the workflow's own "Lint YAML" spelling), log matches textual, learnings matches learned, no match stays unclassified with evidence - clear failure lines without a signature (Actions error annotations, `fatal:`, `Error:`, non-zero exit-code wrappers) are preserved as unclassified evidence while progress lines stay silence, Actions log prefixes (runner timestamp, `gh run view --log` job/step tabs) are stripped before matching, and cancelled/skipped work is claimed as nothing; merge parity is test-pinned - jobs reports count unknown lines per line (not per job) exactly as log mode does, and lessons that teach different fixes stay separate causes through every merge; `sem_reruns.py` (round-19) reads the same payload for the rerun inventory | shipped |
| B35 | Finishing touches / verified fixes | `fix_engine.py`, `fix_tools.py` with tiers and dry-run | shipped |
| B36 | Cross-repository context via MCP | `mcp_server.py`, `mcp_tools.py`; no allowlisted cross-repo context | partial - batch 4 |
| B37 | Repo-wide security deep scans | `slop_security.py` + scheduled `audit.yml` | partial - batch 3 |
| B38 | Security finding lifecycle | SARIF out; no finding state machine | missing - batch 4 |
| B39 | Post-merge automation | quality-store pin promotion + metric store | partial - batch 3 |

### C. Pipeline model (Semaphore): 11 shipped, 0 partial, 0 missing

The parity research lives in `docs/semaphore-parity-research.md`; the
round-16 contracts are pinned by tests.

| # | Feature | Master CI asset | Status |
| --- | --- | --- | --- |
| C40 | Pipeline schema compile-time validation | `tests/test_workflow_contracts.py`, `sem_secrets_lint.py` | shipped |
| C41 | Pipeline DAG, global config, after-pipeline | workflow DAG + `if: always()` summary jobs | shipped |
| C42 | Matrix expansion safety | `sem_matrix_validate.py` (round-17): duplicate keys, empty/non-list dims, product vs the documented 64 ceiling, exclude-only key checks, dynamic expansion reported gate-neutral | shipped |
| C43 | Partial reruns / failed-job recovery | `sem_reruns.py` (round-19): the run-level jobs JSON becomes a rerun inventory - every job with `run_attempt` > 1 named with its attempt count, conclusion and recovery verdict (recovered / still-failing / rerun-pending), a missing `run_attempt` recorded as a data gap rather than guessed attempt one, deterministic rows, byte-identical output, run-level payloads only; advisory by design - the recovery question stays a human one | shipped |
| C44 | Fail-fast, auto-cancel, concurrency | concurrency groups per workflow | shipped |
| C45 | Dynamic init, monorepo detection, preflight | `path_filters.py` + changes jobs | shipped |
| C46 | Parameterized promotions | `sem_promotions.py` + `.semaphore/promotions.yml` (round-16) | shipped |
| C47 | Scoped secrets and access policy | `sem_secrets_lint.py` + `docs/secrets-manifest.yml` (round-16) | shipped |
| C48 | Artifact lifecycles (job/workflow/project) | upload/download actions + quality-store branch as the project namespace | shipped |
| C49 | Layered dependency caching | pip cache slice (d698f4d); Docker layer cache N/A, documented | shipped |
| C50 | Test reports, flaky intelligence, insights | `sem_test_reports.py`, `flaky_detect.py`, `flaky_aggregate.py`, `flaky_quarantine.py`, `sem_insights.py`, `nightly.yml` | shipped |

### The batch plan

- **Batch 1 (this round):** the IR vertical slice (A12/B25), C42 the
  matrix-expansion validator, B23 the change-stack cohorts, B31 the
  merge-risk scorer. Evidence in `docs/nextgen-architecture.md`.
- **Batch 2 - the summary and profiles round (closed):** round-18
  landed A4 profile inheritance, B21 PR summaries tied to head SHAs,
  B34 CI failure diagnosis and B25 graph persistence; round-19 landed
  A19 SBOM + license policy (`sem_sbom.py`) and the A20 holdout split
  (`sem_holdout.py`). A20 stays partial by honesty: the real-repo
  protocol is implemented fail-closed and the manifest ships empty -
  zero real repos are vendored, so real-world accuracy is not claimed;
  the remaining half moves to batch 4.
- **Batch 3 - the review-platform round (round-19 landed two of ten):**
  A9 near-duplicate similarity (`slop_similarity.py`) and C43 rerun
  inventory (`sem_reruns.py`) shipped; still open: B22 walkthroughs,
  B26 linked-issue validation, B27 instruction engine, B29 custom
  checks, B33 reviewer derivation, B37 deep-scan schedule, A10
  cross-language cohesion, A15 registry descriptors.
- **Batch 4 - the lifecycle round:** B28 learnings (the store and the
  `learned`-confidence diagnosis slice landed in round-18; the full
  cross-review lifecycle remains), A20 real-repo corpus (the vendoring
  protocol landed in round-19; the first real repo does not exist
  yet), B30 incremental review state, B32 triage, B24 optional
  diagrams, B36 cross-repo MCP, B38 security lifecycle, A18 taint pack
  boundaries.

Every batch lands with the same contract the template holds itself to:
tests first against the acceptance criteria, the ratchet on its own
diff, benchmark evidence for any detector change, and no status
promoted to shipped without the code and the numbers to point at.
