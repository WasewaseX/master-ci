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
  skip a check). `coverage_min` must be a non-negative number;
  `diff_base` gets the same ref-character validation as `template_ref`.
  Mutable helper refs (`main`/`master`) print a pinning
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
- **zizmor suppressions:** a `# zizmor: ignore[audit]` comment only
  registers where zizmor can parse it as a YAML comment - standalone on
  its own line (covering the next line) or trailing a `uses:`/`run:`
  key. Inside a `run: |` block scalar it is shell text, the suppression
  never registers, and the audit fails the job (the v2.4.0 escape,
  caught by the v2.4.1 contract test).
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
- **Parser guarantee (v2.5):** the CI scan runs `--require-parsers` -
  lizard and tree-sitter are installed from `configs/tool-versions.yml`
  pins, and a missing analyzer is exit 2, never a silently weakened scan
  (parse-backed rules must never degrade to line heuristics unnoticed).
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
  checks; `strict` additionally passes `--enforce-bloat`, escalating
  the structural-budget family (`bloat-long-file`, `bloat-param-count`,
  `bloat-comment-ratio`, `bloat-banner-comments`, `god-function`,
  `deep-nesting`, `chained-dict-get`, `high-cyclomatic`) from advisory
  to blocking - file size, parameter, comment and complexity budgets
  fail the PR outright. `strict`
  also tightens the caps to `ai_slop_max=4` / ceiling `20`.
- **False positives:** domain vocabulary overlapping the phrase list;
  abstract methods that raise NotImplementedError; intentional emoji.
  Tests asserting via a raises-style context (`with pytest.raises(...)`,
  `self.assertRaises(...)`) count as assertions, not empty tests.
  Opt out per path or per check with a committed `.ai-integrity-ignore`
  (glob lines, `check:<id>` lines, `check:<id>:<glob>` for a scoped
  disable), or record existing findings with `--write-baseline` and run
  with `--baseline` so only new slop appears.
- **Bloat budget (advisory by default; `strict` escalates to blocking):**
  the anti-bloatware guard for machine-written
  padding - code that does 100 lines of work in 300, aligned with
  scanaislop.com's quality bar. `bloat-long-file` (over 400 non-blank
  lines, test files exempt), `god-function` (a function over 80
  non-comment lines, measured through the unified lizard parse in
  every parsed family - Python keeps the same budget through its AST),
  `deep-nesting` (over 5 levels in every family), `bloat-param-count`
  (over 6 parameters, read from the real signature), `high-cyclomatic`
  (cyclomatic complexity over 15 - the same ceiling the CI gate
  enforces - in every parsed language), `bloat-comment-ratio` (over
  half the file is
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
  no-op. Go, Rust and JVM discovered their own projects since v2.5; the
  .NET, Ruby and PHP jobs still analyze root manifests only, and emit a
  `::warning::` listing nested manifests they skip, so no stack can
  silently do nothing.
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
- **Round-4 scanaislop adoption (advisory):** the repo-level deadness
  tranche from scanaislop.com's catalog (`code-quality/unused-declaration`
  and the knip family `knip/unused-export` / `knip/unused-dependency`,
  plus the main site's "Unused helpers" headline signal). Deadness is a
  property of the repository, not of a file, so both checks run once per
  scan, not once per file. `unused-declaration` reports a module-level
  Python def/class or a JS/TS export whose bare name appears exactly once
  in the whole repository - its own definition site. Any other mention
  anywhere (another source file, a test, a doc, a workflow, even a
  string) is accepted as a reference, so the false-positive rate stays
  near zero at the cost of missing dynamically-dispatched dead code.
  Test files are candidates for neither harvest, because test runners
  discover tests by name; they still count as usage oracles for
  everything else. Dunders and `main` are exempt. `unused-dependency`
  reports a runtime dependency that no source file ever imports:
  `[project] dependencies` in pyproject.toml (dev groups are exempt)
  and `dependencies`/`peerDependencies` in package.json
  (`devDependencies` are exempt) - v2.1 extended the same contract to
  Go, Rust, PHP, C# and Java, see that bullet below. Dist-to-import
  aliasing is handled (PyYAML->yaml,
  python-dateutil->dateutil, ...) and entry-point-style servers
  (gunicorn, uvloop, ...) are allowlisted. Both checks cap at 8 findings
  per scan and are disabled by design when the scan root is a single
  file.
- **Round-5 scanaislop adoption (advisory):** the languages page's Java
  column. `debug-print` now covers `System.out`/`System.err` leftovers
  (alongside Go's `fmt.Print*` and JS's `console.log`),
  `java-broad-throws` reports `throws Exception` / `throws Throwable`
  signatures (the specific type belongs in the signature; catch blocks
  that only print were already covered by `print-stacktrace-only`), and
  `phantom-import` extends to Java imports (the simple name must appear
  outside its import lines; wildcard imports are exempt). Test sources
  are exempt from all three.
- **Round-6 scanaislop adoption (advisory):** the last tells from the
  four slop sources - dynamic evaluation plus the design signs the
  earlier rounds had not yet covered. `eval-usage` reports `eval()` and
  `new Function()` in JS/TS and AST-verified `eval()`/`exec()` call
  nodes in Python (test sources are exempt on the JS side).
  `ui-placeholder-image` flags the placeholder-image services
  (picsum.photos, placehold.co, via.placeholder.com, pravatar.cc,
  dummyimage.com, ...) that stand in for photos nobody chose (tenex
  sign 5, noqode sign 7). `ui-identical-cards` reports a class string
  of 30+ characters repeated four or more times in one frontend file -
  one template multiplied, not N designed blocks (tenex sign 7, noqode
  sign 3). `ui-em-dash-overuse` flags eight or more em dashes in one
  frontend file ("the em dash, everywhere" - tenex sign 1). The UI
  regex tables moved to `slop_ui_patterns.py`, keeping the shared
  pattern module under the same 400-line budget the detector enforces
  on others, and the unknown-directive scan now strips string literals
  first so regex tables cannot self-report.
- **v1.19 - unified syntax-tree analysis:** the structural budgets are
  parse-backed, not regex-guessed. `slop_lizard.py` runs lizard (the
  same mature analyzer the CI complexity gate uses) over every parsed
  family - Python, JS/TS/JSX/TSX, Go, JVM, C#, C/C++, Ruby, PHP, Rust,
  Swift, Dart - and reduces each file to shared function facts: name,
  start line, nloc (non-comment, non-blank lines), cyclomatic
  complexity, parameter count. `god-function` now measures honest nloc,
  so comment padding can no longer pass a length budget and blank
  lines cannot convict dense code; `bloat-param-count` reads real
  parameter lists (generic type arguments never inflate them); and the
  new `high-cyclomatic` reports any function over the same complexity
  15 ceiling the CI gate enforces, in every parsed language - Python
  included, where it joins the AST-backed length/nesting/parameter
  checks. When no parse exists - the optional analyzer is missing, the
  file is SFC markup (.vue/.svelte/.astro), or the source is too
  malformed for the lexer - the line scanner takes over and the
  message says so ("function block spans"): a missing analyzer
  degrades the measurement, it never drops the check. Nesting stays on
  the line scanner either way; it is the one function fact the parser
  does not carry. A related accuracy fix: a *scoped* `# type:
  ignore[code]` names its rule and no longer counts toward
  `blanket-suppression` - only bare suppressions do.
- **v1.20 - cognitive complexity across languages:** the second
  tree-backed measure, on top of the unified parse. `slop_cognitive.py`
  reads real tree-sitter syntax trees (one mature parser, grammars for
  Python, JS/TS/JSX/TSX, Go, JVM, C#, C/C++, Ruby, PHP, Rust, Swift,
  Dart and Kotlin/Scala) and scores every function the way a reader
  experiences it, following Sonar's model: control structures cost 1
  plus their nesting depth, branch labels (else, case arms) cost 1,
  each new run of the same logical operator costs 1, direct recursion
  costs 1, and a nested function resets the nesting counter - a reader
  starts over inside a closure. The new `high-cognitive` check reports
  functions over the budget of 15 (Sonar's own "moderately complex"
  threshold); advisory by default, escalated by `--enforce-bloat`
  alongside the other structural budgets, categorized under
  **Maintainability**. Where no tree is available - the optional
  parser is missing, the file is SFC markup, or the source is too
  malformed to parse - the check reports nothing rather than guessing
  from line shapes. Wiring this gate in held the template's own
  codebase to its own standard: fourteen engine functions (and one
  contract test) were flattened below the budget before release, and
  the dogfood self-scan is 0 findings, all seven dimensions A(0), at
  max strictness.
- **Round-7 scanaislop adoption (blocking-grade, opt-in):** the
  "Custom Architecture Rules" from the scanaislop docs. A
  `.ai-integrity-arch.json` at the scan root declares the project's
  law; the detector enforces it deterministically. `arch-forbidden-import`
  (`forbidden_imports`: module + reason - a module no file may import),
  `arch-layer-violation` (`layers`: named path globs with
  `must_not_import` - core code importing the UI layer fails the gate;
  import names resolve to layers through file stems and package
  directory names), `arch-missing-pattern` (`required_patterns`: every
  file matching a glob must contain a regex - e.g. `^class` in core
  modules, `from __future__ import annotations` everywhere). No config
  file means no findings, so existing consumers are unaffected - and a
  declared rule is a contract, not a judgment call, so violations are
  blocking-grade by construction. master-ci holds itself to this: the
  template ships its own config (slop modules may not import MCP
  modules; every engine module keeps PEP 563).
- **v1.18 fail-closed architecture config:** a config that exists but
  cannot be honored now fails the run instead of being silently
  skipped - the CLI exits **2** (configuration error) with the reason on
  stderr. Rejected: invalid JSON, a non-object root, unknown keys, empty
  or malformed rule lists, rule objects missing required string fields,
  a `module` that is not a module/package name, a `required_patterns`
  regex that does not compile, a `must_not_import` naming a layer nobody
  declared (that rule could never match, so it would look enforced while
  enforcing nothing), and a config that declares no rules at all. A
  missing config file stays opt-in silence.
- **v1.18 quality dimensions - the scores are separated:** one number
  for "how much machine slop" and another for "how healthy is the code"
  is more credible than either blended. `category_breakdown` reports
  seven independent dimensions, each scored with the same deficit model
  over its own findings: **Architecture** (arch-*), **Code health**
  (exception handling, debug residue, dead code, structural bloat),
  **Maintainability** (design debt, naming, suppression habits),
  **Security** (`eval-usage`, placeholder credentials), **Test
  assurance** (empty/tautological tests, missing test inventory),
  **Dependency health** (undeclared imports, unused dependencies), and
  **AI integrity** (authorship and machine-prose signals - the slop
  gate's home). `--max-slop-score` now gates the **AI-integrity
  dimension only**; a bloat-heavy-but-slop-free repo grades A on the
  ceiling, and `--max-score` still gates every blocking weight in every
  dimension. The `--report-file` markdown gains a "Quality dimensions"
  table, and the CLI summary line prints `slop=<score>/100` plus all
  seven dimension grades.
- **v1.18 scoped noqa exempts phantom imports:** an import line ending
  in `# noqa` (ruff F401 convention) marks the unused import
  intentional - re-export surfaces like package `__init__` facades - so
  `phantom-import` skips it. Unmarked unused imports still report.
- **Scoring refinements (scanaislop-aligned):** advisory pressure is
  tier-weighted - maintainability debts (bloat, god-function, deep
  nesting, dispatch ladders) press at 0.75x, mechanical residue
  (phantom imports, dead residue, debug prints) at 0.5x, style and UI
  findings at 0.5x, traceability notes at 0.25x - and every check's
  contribution saturates at 40 points per file (`MAX_PER_RULE`), so one
  noisy rule can never dominate a deficit. Blocking weight is never
  tiered: strict signals stay strict.
- **v1.21 coverage gates - test assurance from inventory to proof:**
  before this release the test-assurance dimension could only report
  test *inventory* (`empty-test`, `tautological-test`, `missing-tests`);
  now it can report measured *proof*. Setting `coverage_min` above 0
  makes the Python, Go and Node stacks run their suites under coverage
  (Python via `coverage run`/`coverage json`, Go via
  `-coverprofile=cover.out`, Node via the project's own
  `test:coverage` script emitting Cobertura XML) and upload the report
  as an artifact; the dedicated `coverage-gate` job - which waits for
  the coverage-producing stacks and runs only when at least one of
  them actually executed (a no-code-change commit skips the stacks,
  and "nothing changed" is not a coverage failure) - downloads the
  reports and consumes them through `scripts/coverage_gate.py`
  (standalone CLI), which
  parses all three dialects (Python `coverage.json`, Cobertura XML,
  Go `cover.out`) and renders a "Test assurance: measured coverage"
  table into the markdown report. The floor gates **every** report -
  in a polyglot repo each stack measures its own slice, and every
  slice must clear the bar. Setting `diff_base` (e.g. `origin/main`)
  additionally applies the same floor to the **new-code window**:
  the gate intersects the merge-base diff's added lines with each
  report's line data, and per-file breaches surface as advisory
  `low-new-coverage` findings (test-assurance dimension, SARIF,
  annotations) plus a worst-files table. Fail-closed contract (same
  semantics as the v1.18 architecture config): a requested floor with
  no report, an unparseable report, or a new-code gate without a diff
  base exits **2** with the reason on stderr - a gate that cannot be
  evaluated never silently passes, and exit 2 fails the CI step even
  when `fail_on_ai_slop` is false (only gate *scores* are
  advisory-controlled). Known limits: Node coverage
  must be Cobertura (clover or istanbul JSON dialects fail as corrupt
  rather than being guessed); Go module-prefixed paths match diff
  paths by suffix; a changed file absent from the report counts as
  0% new-code coverage. Companion on the schedule side:
  `.github/workflows/mutation.yml` runs mutmut (Python, when
  `[tool.mutmut]` is declared) or Stryker (Node, when any valid Stryker
  config filename is committed - `stryker.conf.*`, `stryker.config.*`,
  `.stryker.conf.*`, `.stryker.config.*`, json/js/mjs/cjs) weekly -
  coverage says which lines ran, mutation says whether the assertions
  would notice. The mutation job installs the project's own
  dependencies first (mutmut drives the project's pytest; Stryker's
  test-runner plugin lives in the project's node_modules) - mutants are
  never measured against a bare environment.
  As of v2.6 the scheduled hunt spans four ecosystems, each opt-in by
  declaration: mutmut (Python, `[tool.mutmut]`), Stryker (Node,
  `stryker.conf.*`), cargo-mutants (Rust, `.cargo-mutants.toml` - exit
  2 = survivors found) and gremlins (Go, `.gremlins.yaml` - exit 10/11
  when the declared threshold efficacy or mutant-coverage floors are
  breached; without declared thresholds gremlins never trips, so the
  config file is the gate). Every install is pinned in
  `configs/tool-versions.yml`.
  **Adoption (v2.9):** `mutation.yml` is a reusable workflow as well as
  a scheduled one. The consumer owns the schedule trigger - a called
  workflow cannot carry one - by wrapping one job that calls
  `WasewaseX/master-ci/.github/workflows/mutation.yml@<pinned tag>`
  with `template_repo` / `template_ref` (defaults: `WasewaseX/master-ci`
  @ `v2`, the `codeql.yml` contract: a cross-repo call cannot
  auto-detect the helper repo because `GITHUB_WORKFLOW_REF` names the
  caller there). Opt-in stays a declaration in the calling repository:
  the detection step reads the caller's checkout, and a call without a
  committed mutation config reports that nothing ran. The Test
  assurance tables verify the call the way they verify the CodeQL one -
  the Mutation testing row flips to *armed via own workflow* only when
  the call is really committed.
- **v2.0 - the module dependency graph (`import-cycle`, advisory):**
  the repo-level structural verification that closes the roadmap's
  "deeper analysis" arc. `slop_cycles.py` builds a module dependency
  graph natively - Python imports through the AST (relative and
  absolute, submodule-or-attribute resolution), JS/TS static
  `import`/`require`/`export-from` specifiers probed against the known
  file set (extension, TypeScript compiled-extension swap, directory
  index), Go import paths under the root go.mod's module mapped to
  package directories, Ruby `require_relative` - and runs an iterative
  Tarjan SCC over it. Every strongly-connected component with more
  than one node is one `import-cycle` finding: the shortest loop
  through the component, located at the first import line, capped at 8
  per scan, maintainability dimension. The graph is conservative by
  design: dynamic `import()` is excluded (a lazy edge is a legitimate
  way to *break* a cycle - counting it would punish the fix), plain
  Ruby `require` is excluded ($LOAD_PATH can mean a gem), and JVM, PHP,
  C# and C/C++ are out of scope because their cycles are properties of
  classpaths, autoloaders and build graphs, not of the scanned source.
  Namespace glue is excluded before the search - a package's
  `__init__.py` and a JS barrel that is nothing but re-exports encode
  the one-directional parent-imports-child idiom, and counting them
  would convict the most common correct package shape. Self-imports
  are ignored; test files participate like any other module. Where the
  v1.18 architecture config enforces a *declared* import law, a cycle
  is unconditional design debt - it forces every member into one
  change set and makes isolated testing impossible. `build_graph` is
  exported for tests and tooling.
- **v2.1 - native dependency analysis across every sound stack
  (`unused-dependency` completion, `orphan-module` new, advisory):**
  the roadmap's item 6, closed. The Python and Node oracles
  moved from slop_dead.py into a dedicated dependency module and grew
  five siblings, one per stack where a manifest-to-code oracle is
  actually sound: Go (go.mod direct `require` lines - `// indirect` is
  transitive weight the toolchain owns; any quoted path under the
  module prefix is use), Rust (Cargo.toml plain `[dependencies]` -
  dev/build/workspace tables and per-target tables exempt; dash and
  underscore spellings both count; a `package =` rename is use under
  either name), PHP (composer.json `require` - require-dev, platform
  constraints and polyfills exempt; use is a mention of the vendor
  namespace, which PSR-4 guarantees for a consumed package), C#
  (csproj `PackageReference Include=` - `Update=` is version
  management, build integration (analyzers, runners, test SDKs,
  generators, coverlet, SourceLink) never appears in `using` and is
  exempt; the longest token of the package id must appear in some .cs
  file), and Java (pom.xml direct-child `<dependencies>`, compile
  scope only - `<dependencyManagement>` is a version catalog,
  build-plugin dependencies are toolchain, and runtime/provided scopes
  are SPI and container weight; any artifactId token found in a JVM
  import or FQCN is use). Two stacks are deliberately left out, with
  the reason written down: Ruby (Bundler.require auto-loads every
  default-group gem at boot, so a missing require line is not evidence
  of anything) and C/C++ (CMake target names do not determine include
  paths; the false-positive rate would breach the conservative trade).
  All manifests are discovered anywhere under the scan root and each
  is judged against the sources under its own directory, so a monorepo
  member is checked against its own code, not the aggregate. The new
  `orphan-module` check is the file-scale version of the same
  conservative oracle: a module no import, config, doc or script ever
  mentions. Python, JS/TS and Ruby are judged by file stem (their
  import specifiers name files); Go is judged by package directory,
  because files inside one Go package reference each other namelessly.
  Stems under four characters, test files, entry-point stems (`main`,
  `index`, `app`, `cli`, ...) and namespace glue (`__init__`, `mod`)
  are exempt; the oracle corpus reaches past source into manifests,
  workflows and config files, so a module wired up by a containerfile
  or a compose command never gets flagged. One accuracy fix the
  self-scan surfaced: `duplicate-import` now compares full dotted
  module paths, not first components - `collections` and
  `collections.abc` are different modules, and importing both is not a
  duplicate. Both new checks are advisory, capped at 8 per scan, and
  disabled when the scan root is a single file.
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
- **Fails when:** ruff/mypy/pytest errors, complexity above
  `complexity_max` when enforced, or a missing lizard binary after the
  pinned install (v2.5 parser guarantee, exit 2).
- **False positives:** vulture on dynamically-dispatched code; mypy on
  untyped third parties (mitigated by the flag above).
- **The helper checkout is invisible (v2.9.1):** every workflow fetches
  master-ci's helpers into a transient `_ci/` directory inside the
  consumer's tree before the stack jobs run. That code is the
  template's, versioned and linted by the template's own battery - a
  consumer's scan must never judge it. Vulture and lizard excluded
  `_ci` from the start; the default ruff config (copied only when the
  consumer ships none) now extends-excludes it, and the mypy scan
  carries the same exclusion its own file probe always declared
  (`grep -v '^_ci/'`) - which also removes the latent duplicate-module
  collision for consumer trees that name their files like the
  template's helpers. Repos committing their own ruff or mypy config
  are their own authority: add `_ci` to your excludes if you fetch
  helpers and name files like the template does.
- **Disable:** `run_python: 'false'`.
- **Time:** 2-5 minutes.

## `rust` - Rust stack

- **Inspects:** every discovered Cargo root - the repository root,
  `[workspace]` members and nested independent crates (v2.5 project
  discovery): rustfmt, clippy (`-D warnings`), cargo-deny per project
  (advisories, licenses, sources - pinned source install),
  cargo-machete unused-dependency analysis (advisory, v2.6 - the
  Rust counterpart to depcheck), and tests; with `coverage_min`
  armed the same test run emits a cobertura report per crate through
  the pinned `cargo-llvm-cov` (v2.6 - the Rust slice joins the
  measured floors the Go slice has had since the gate existed).
- **Does not inspect:** benches beyond clippy's `--all-targets`,
  no_std targets, MSRV matrix (single toolchain input).
- **Fails when:** fmt/clippy/deny/test failures in any discovered
  project - one red project fails the stack, every project still runs.
- **False positives:** clippy lints on generated code (add `#[allow]`
  attributes); cargo-deny on unpublishable dev deps; cargo-machete on
  macros whose name only appears in a derive (document it in
  `package.metadata` per the tool's own convention).
- **Disable:** `run_rust: 'false'`.
- **Time:** 5-20 minutes cold, 1-5 warm (shared `rust-cache`); the
  pinned cargo-deny source build adds a few minutes; coverage adds
  one instrumented rebuild (v2.6, only when `coverage_min` is set).

## `go-lint` / `go-test` - Go stack

- **Inspects:** every discovered module - `go.work` members and nested
  `go.mod` modules (v2.5 project discovery): gofmt formatting, `go vet`,
  golangci-lint (pinned, built via `go install`, per module, honoring
  each module's own `.golangci.yml`), `go build ./...`, and the test
  suite under the race detector (`go test -race -shuffle=on ./...`);
  coverage emits one coverprofile per module when `coverage_min` is
  set, and `go mod tidy -diff` (advisory, v2.6 - the toolchain's own
  unused/missing-requirement oracle, needs Go >= 1.23) flags any
  go.mod/go.sum that tidy would change.
- **Does not inspect:** vendored trees (`vendor/` is excluded from path
  filtering like every stack).
- **Fails when:** formatting drift, vet findings, golangci-lint
  findings, build or test failures - accumulated across every module,
  so one red module fails the stack and every module still runs.
- **False positives:** golangci-lint defaults on legacy code - add a
  `.golangci.yml` to tune; repos with no tests still pass.
- **Disable:** `run_go: 'false'`; force with `run_go: 'true'`, pin the
  toolchain with `go_version` (default `stable`).
- **Time:** 1-5 minutes warm (module cache), 5-10 cold.

## `java` - JVM stack (Java, Kotlin, Scala via Maven or Gradle)

- **Inspects:** every discovered build root (`pom.xml`/`mvnw` or
  `build.gradle*`/`settings.gradle*` - v2.5 project discovery): Gradle
  wrapper checksums are validated repo-wide before any code runs; each
  Maven project runs the full `mvn verify` lifecycle (compile, unit +
  integration tests, packaging), each Gradle build runs `gradle build`
  (compile + test); with `coverage_min` armed (v2.6) Maven projects
  get the pinned JaCoCo agent injected from the command line
  (`prepare-agent` + `report` goals - no pom edit needed) and Gradle
  projects that apply the jacoco plugin get `test jacocoTestReport`
  invoked, with an enable hint for the ones that do not - Gradle
  cannot receive the agent without the plugin, and the template does
  not rewrite build files.
- **Does not inspect:** `build/` and `target/` outputs (excluded from
  path filtering).
- **Fails when:** wrapper checksum mismatch, compile, test, or packaging
  failures - whatever the build tool's own lifecycle fails on,
  accumulated across every discovered project.
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
- `ci-success` is the only check you should mark required - under the
  full name branch protection actually sees, `<caller job id> /
  ci-success` (a caller job named `master` reports `master /
  ci-success`; the ci-canary aggregate caller reports `required /
  gate`). It fails when
  any needed job failed or was cancelled, and succeeds when jobs were
  merely skipped. It never runs tools itself.
- Both summaries carry a **Test assurance** table (v2.5): the armed or
  off state of the coverage floor, new-code ceilings, quality ratchet,
  mutation testing and the flake hunt, each with its one-line enable
  hint - an opt-in layer is reported, never silently absent. The
  **Semantic analysis (CodeQL)** row (v2.7) and the **Mutation
  testing** row (v2.9) are the verified ones: both jobs check out the
  calling repository and grep its own workflow files for the
  `codeql.yml`, `required-quality.yml` or `mutation.yml` call, so a row
  shows *armed via own workflow* only when the call is really
  committed. The grep scans every workflow file, not just the first
  hit - a call in a file sorted after the aggregate's must still be
  seen.

## `codeql.yml` - CodeQL Advanced, semantic security analysis (standalone)

Not a job in `master-ci.yml` but a sibling reusable workflow, free on
public repositories, called with one line and no `with:` block:
`uses: WasewaseX/master-ci/.github/workflows/codeql.yml@v2` plus
`security-events: write`.

- `languages: auto` (the default, v2.7) detects dialects from the
  manifests the repo commits: `scripts/codeql_detect.py` maps go.mod,
  Cargo.toml, pom.xml/build.gradle(.kts), package.json, pyproject.toml,
  Gemfile, Package.swift, CMakeLists/meson/Makefile and .csproj/.sln
  onto the nine CodeQL languages, walking a fixed skip list
  (node_modules, dist, build, target, vendor, coverage) so vendored
  code never votes. The detector is fetched from `template_repo` /
  `template_ref` (defaults: `WasewaseX/master-ci` @ `v2`, overridable
  for forks), so its manifest map is versioned with the tests that pin
  it; the contract tests hold the detector's set and the workflow's
  allowed set equal so the two cannot drift.
- A repository CodeQL cannot analyze is exit 2 with the fix spelled
  out - pass `languages:` explicitly or drop the call - never an empty
  analysis reported as success.
- Explicit `languages:`, `queries:` (default `security-and-quality`)
  and `config_file:` still work unchanged; one analyze job per language
  runs `autobuild` for compiled dialects and uploads SARIF to the
  Security tab with a per-language category.

## `coverage-gate` and `new-code-gate` - measured floors and new-code ceilings

Both jobs gate on the merge-base diff (`diff_base`), fetch the ref with the
shared `fetch_diff_base.sh` helper, and fail closed: a requested gate that
cannot be evaluated is exit 2, never a silent pass.

On a `pull_request` event the runner checks out the merge ref at depth 1,
so no common history exists and the merge-base (three-dot) diff cannot be
computed even though both refs resolve - the hosted canary PR exercise
hit exactly that (exit 128 on `origin/main...HEAD`). The shared diff
(v2.7.2) then falls back to the two-dot tree diff, which needs no
ancestry: on a merge ref the tree already contains the base branch, so
the tree diff is exactly the change under review. If both forms fail the
gate still exits 2 - the fallback widens what can be measured, never what
passes.

- `coverage-gate` (v1.21) enforces `coverage_min` against every uploaded
  report (coverage.json, Cobertura XML, Go coverprofile, JaCoCo XML -
  v2.6 dispatches on the XML root tag, so cargo-llvm-cov's cobertura
  and Maven/Gradle jacoco reports ride the same floor) and, when
  `diff_base` is set, the same floor on the lines the change adds. Only
  coverage subjects enter the new-code denominator: test files and
  non-code files (configs, docs, manifests) are not coverage subjects
  for any mainstream tool, so they stay out of the math; a code file
  with no report entry still counts as uncovered, so untested new code
  cannot hide.
- `new-code-gate` (v1.22) enforces the dimension ceilings on new code -
  legacy files keep their history, every added line meets today's bar:
  - `new_code_max_slop` (default 0): the blocking score of detector
    findings landing on added lines. Findings on untouched lines are
    heritage, not gate input.
  - `new_code_max_cognitive` (default 15) and `new_code_max_cyclomatic`
    (default 15): apply to functions whose span intersects the added
    lines. The span is `start..max(start + nloc, next start)` - an
    over-approximation on purpose: a gate may over-flag a legacy
    neighbor, never miss a new offender.
  - `new_code_gates: false` disables the whole job. Exit 1 = a ceiling
    breached by the change; exit 2 = no diff base, git failure (with the
    v2.7.2 shallow-checkout tree-diff fallback in between), or a
    missing parser under a requested ceiling (v2.5 parser guarantee:
    lizard is demanded for the cyclomatic ceiling and tree-sitter for
    the cognitive ceiling - the job installs the pins from
    `configs/tool-versions.yml` before the gate runs, so a requested
    metric that cannot run fails the gate instead of silently
    reporting zero touched functions).

## `quality-ratchet` - the committed floor no check may regress from (v2.2)

The new-code gate keeps the diff honest; the ratchet keeps the whole
repository honest over time. One committed file,
`.quality-baseline.json` (override with `ratchet_file`), records how
many findings each check id reports at baseline time. Every run
compares the current scan against that floor:

- **Any check above its recorded count fails the run** (exit 1). The
  step summary shows the delta table - regressions first, improvements
  below them - so a red ratchet names the exact check and how far it
  moved.
- **Paying debt is visible**: counts that drop render as improvements.
  Deleting the last finding of a check removes its row entirely.
- **Re-recording the floor is a reviewed commit**: run
  `python3 scripts/ai_slop_detector.py . --write-quality-baseline
  .quality-baseline.json` and commit the change. Git history then shows
  when and why the floor moved - the escape hatch is not a silent
  waiver.
- **Parser guarantee (v2.5):** the enforcing scan runs
  `--require-parsers` - a floor recorded with parsers must be enforced
  with parsers; a missing analyzer is exit 2, not a quietly smaller
  count that lets a regression pass.
- **Fail-closed (exit 2) when the floor cannot be interpreted**:
  - `ratchet: true` and the file is missing (a demanded contract that
    cannot be honored),
  - the file is unreadable or not valid JSON,
  - the schema `version` is not the one this engine speaks,
  - `checks` is not an object of check id -> non-negative integer,
  - a check id in the baseline is unknown to this engine's catalog
    (the catalog changed in either direction - re-record the floor).

With the default `ratchet: auto`, the job runs only when the baseline
file is committed, so the ratchet is opt-in by committing the file and
invisible otherwise. `ratchet: false` disables the job even when the
file exists.

**What counts**: every check id the scan reports, advisory and blocking
alike - a committed baseline is an explicit per-check contract the repo
opted into, the same reasoning that lets `--enforce-bloat` escalate the
structural family. The single exception is `low-new-coverage`: it
depends on coverage artifacts other jobs produce, not on the tree
alone, so freezing it would compare different environments. The
ratchet counts findings **before** `--baseline` fingerprint
suppression: accepted debt stays in the report's history but cannot
hide a regression from the floor. Per-check exclusion is available via
`.ai-integrity-ignore` `check:<id>` lines, which remove the check from
every run of this engine, ratchet included.

**False positives**: none from staleness - the floor moves only when a
reviewed commit moves it. PRs that only reformat or move code keep
their counts stable. Monorepos: counts are repository-wide by design;
scope the scan or ignore rules per project if per-project floors are
wanted.

**Added cost**: the scan the `ai-integrity` job already performs, plus
one JSON read and a diff - seconds, no additional tooling.

## `flake-hunt` - the test suite, repeated until the verdicts are trustworthy (v2.3)

- **Runs in:** `flaky.yml` (copy into the consumer repository), weekly
  on Mondays plus manual dispatch with an `iterations` input (2-5,
  default 3). It is a standalone scheduled workflow, like
  `mutation.yml` - it never gates PRs and is never part of
  `ci-success`. Unlike `mutation.yml` (reusable since v2.9), the hunt
  is not callable: a consumer adopts it by copying the workflow and
  pinning the helper default, because the quarantine loop needs the
  write-holding job to live where the caller's token can open PRs.
- **What it does:** runs the repository's own test suite N times in one
  job, reads every `run-<i>` result file the iterations produced, and
  classifies each test: **flaky** (both a pass and a fail across
  iterations), **always-failing** (failed every iteration - the main CI
  should already be red; listed for completeness, never quarantined) or
  stable. A test absent from one iteration is reported as absent, not
  guessed.
- **Formats consumed as-is:** JUnit XML (pytest `--junitxml`, PHPUnit
  `--log-junit`, surefire and Gradle XML, vitest `--reporter=junit`,
  jest-junit, RSpec's `rspec_junit_formatter`, ctest
  `--output-junit`), .NET TRX (`dotnet test --logger trx`), Go test
  JSON (`go test -json` - verdict events only, stream and
  package-level events are noise) and Rust libtest stdout (`cargo
  test`). Multi-module JVM trees are covered via `find` over every
  `*/target/surefire-reports` and `*/build/test-results/test`
  directory. No project changes how it tests.
- **Does not do:** retry-until-green (a retry hides flakes, repetition
  exposes them), quarantine failures in PR CI (the hunt is a scheduled
  measurement), authorship claims, or any modification of tests.
- **Quarantine baseline:** a committed `.flaky-baseline.json` records
  the flaky set (`{"version": 1, "tests": {"<key>": {"first_seen":
  "<date>"}}}`). Its presence arms `--fail-on-flaky`: a **new** flaky
  test fails the hunt (exit 1), quarantined flakes are reported without
  failing, and healed flakes (no longer flaky) are listed as removable
  debt. Without a baseline the hunt is report-only - adopting
  enforcement is one commit of the file the run produces.
  `--write-baseline` records the current flaky set as a reviewable
  commit (two-space indent, sorted keys, deterministic bytes - the
  hygiene gate enforces the same editorconfig on it). Since v2.4 the
  baseline update itself is automated - see the quarantine loop below.
- **Fails closed (exit 2) when the hunt cannot be executed at all:**
  missing results directory, no `run-<i>` files at all, non-contiguous
  run numbering (every iteration must produce a file, even if the suite
  crashed - write an empty-but-valid report or fix the runner),
  `--iterations` disagreeing with the files, an unreadable result file
  (malformed XML/JSON or a format the engine does not speak), a test
  recorded with conflicting verdicts inside one file, a run that records
  zero tests, or an unreadable baseline. Report files the runners failed
  to produce are surfaced as `::warning::` annotations before the
  analyzer's fail-closed verdict.
- **Reports:** a step-summary markdown table (new flakes first, always-
  failing and healed sections below), a `flaky-report.json` artifact
  (per-test outcomes in iteration order, quarantined/new/healed lists)
  and exit codes 0 (clean or quarantined), 1 (new flakes under
  `--fail-on-flaky`), 2 (fail-closed).
- **Relationship to the gates:** coverage and mutation run at PR time
  or on schedules; the hunt closes the third question of test
  assurance. It composes with the quality ratchet (committed floors
  everywhere) but is independent of it: the ratchet counts detector
  findings, the hunt counts runner verdicts.
- **False positives:** order-dependent suites may look flaky when
  parallelization reshuffles tests between iterations - that is real
  flakiness by definition and the table names the test. Time-dependent
  tests that straddle midnight produce one noisy weekly report;
  quarantine or fix them. A suite that skips a test only under load
  shows `absent` columns, not flakiness.

### The quarantine loop (v2.4)

A hunt that fails on new flakes in enforcement mode does not stop at
the exit code. The hunt job builds a candidate baseline with
`scripts/flaky_quarantine.py propose` - an append-only merge that
preserves every existing `first_seen` verbatim, stamps the new keys
with today's date, and never removes a healed flake (removal is a
reviewed commit, exactly like re-recording a ratchet floor). The
candidate is written to `RUNNER_TEMP`, outside the worktree, so it
cannot leak into the PR branch through the hunt job's checkout.

A second `quarantine` job - the only one holding write scopes, and it
runs none of the repository's test code - downloads the evidence and
re-verifies the candidate against the default branch
(`flaky_quarantine.py verify`): exactly the committed baseline unioned
with the report's new flakes, no removal, no fabrication, no rewritten
history; any disagreement exits 2 before a PR can open. On success it
opens or updates the `ci/flake-quarantine` PR, bounded by
`add-paths: .flaky-baseline.json` - even a compromised worktree can
only ever offer the baseline file. Unlike self-heal, an open
quarantine PR is updated in place: the candidate is recomputed from
the default branch each hunt, so the update carries no accumulating
state.

The loop is gated three ways, each independently justified: the
report's machine verdict (`status: new-flakes` - the hunt's own data,
not an exit code parsed from prose), a committed baseline (report-only
repos are refused - adopting a first floor is a human decision), and
the default branch (a dispatch on a feature branch hunts, never
quarantines). Closing the PR rejects the quarantine and the next hunt
keeps failing until the flakes are fixed; merging it silences only the
listed tests - a new flake still fails the next hunt.

## Project discovery - the monorepo map

`discover_projects.py --family <family>` inventories independent projects
per language family; jobs loop over the list so no project is silently
skipped (`--fail-empty` turns an empty inventory into a loud failure).
Since v1.22 the inventory is manifest-aware:

- workspace markers enumerate their actual members: npm/pnpm `workspaces`
  (list or `packages` object, `pnpm-workspace.yaml`), `go.work` `use`
  entries, Cargo `[workspace]` members with glob expansion and `exclude`,
- each project carries its manifest and best-effort name (package.json
  `name`, Cargo `[package] name`, go.mod `module`),
- a declared member that cannot be backed by a real manifest - a named
  workspace entry pointing at a directory without its manifest, a
  `go.work` use without `go.mod`, a malformed `pnpm-workspace.yaml` -
  fails closed with exit 2: a workspace that declares members never
  silently degrades to root-only analysis,
- glob patterns that match nothing become warnings (package managers
  tolerate them); a workspace that resolves to zero projects stays
  available to `--fail-empty`,
- `--family all` prints the full map across every family - the honest
  answer to "what does this monorepo actually contain?",
- Gradle is deliberately not member-enumerated: a Gradle build is
  analyzed from the root invocation, so the root project is the honest
  unit.

Since v2.5 the map is consumed everywhere it matters: the Go jobs
discover `go.work` members and nested modules, the Rust job discovers
`[workspace]` members and nested crates (cargo-deny runs per project),
and the JVM job discovers nested poms and Gradle builds - the old
root-manifests-only warnings are gone because nothing is skipped
anymore. Node keeps the `--fail-empty` contract; Go, Rust and JVM skip
with an explicit reason when the inventory is empty (a config-only
path-filter trigger analyzes nothing, and says so).

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

## `doctor` - the setup diagnostic (v2.0)

The template verifies code; `scripts/doctor.py` verifies the *setup* -
the workflow file, the pins and the permissions a consumer wrote by
hand. It runs offline against any checkout (default
`.github/workflows`, override with `--workflows-dir`) and turns the
README's prose contract into deterministic checks:

- **Ref style (per reusable-workflow job):** `@main` is an error - a
  mutable ref changes CI behavior without review. `@vX.Y.Z` is the
  recommended reproducible baseline; `@v2` tracks the latest release
  (fine, but doctor notes the difference); any other branch or SHA
  ref draws a warning.
- **The helper-pin contract (master-ci.yml only):** `template_repo`
  without `template_ref` is an error - a foreign helper repo needs an
  explicit pin, exactly what meta's fail-closed lookup enforces at
  run time; no pins at all draws the "foreign projects must pin both"
  warning; both set draws the pinned confirmation.
- **Permissions (per job, job-level over workflow-level):** missing
  `contents: read` or `security-events: write` is an error (checkout
  and SARIF upload fail outright); missing `pull-requests: write` is
  a warning (annotations and the PR report degrade); missing
  `id-token: write` is info (OIDC capabilities switch off).
- **Gate inputs:** `coverage_min` must be an integer 0-100 (anything
  else is an error) and a set value draws the "floor armed" info;
  `diff_base` draws the "new-code gate is armed" info.
- **The aggregate check:** if no workflow file mentions `ci-success`
  (raw text - comments count), doctor reminds you to mark it as the
  required status check.

Severities map to exit codes the same way the rest of the template
fails: 0 healthy (warnings allowed), 1 at least one error, 2 doctor
could not diagnose at all - missing PyYAML, no workflows directory, or
a workflow file that cannot be parsed. Fail closed: an unreadable
setup is an error, never a shrug. `--json` renders the same notes as
machine output (`ok`, `errors`, `warnings`, `infos`) for scripts and
editor integrations. `--template` retargets the slug check for forks
that publish their own template copy.

Run it before pushing a workflow change, in any job, or as a repo
hygiene step: `python scripts/doctor.py` (add `--json` for CI use).
