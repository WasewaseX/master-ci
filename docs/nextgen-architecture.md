# The next-generation analysis system: what the code says, what the IR unlocks

Written against the actual source (2026-11-19, commit range up to the
round-17 vertical slice). Every number below was measured on this
repository with `scripts/ir_benchmark.py` or read out of the modules it
names. Nothing here is a plan pretending to be a result: shipped work
is marked shipped, evaluation is marked evaluation.

## 1. What Master CI actually is today

Measured inventory, this commit:

- **106 Python modules in `scripts/`, 26,378 lines**, zero runtime
  dependencies beyond the optional `scanner` group (pyyaml, lizard,
  tree-sitter + grammar pack).
- **151 registered checks** in the catalog (`slop_catalog.py` reads the
  live registries: `REMEDIES`, blocking/advisory classes, categories,
  reliability and design memberships).
- **49 test modules, 1222 tests**, all green on the round-17 tree; the
  required gate is the `required-quality-check` composite (master-ci
  result plus CodeQL result).
- The scan dispatch is composition, not a framework: `slop_scan.py`
  imports each check function and extends one findings list - file-scale
  oracles run per file, repo-scale oracles (`slop_duplicates`,
  `slop_cycles`, `slop_symbol_graph`, the density and coupling
  measurements) take the whole corpus.
- The same engine serves four surfaces: the CLI
  (`slop_cli.py --list-checks`), the MCP server (`mcp_server.py`),
  the per-edit hook (`hook_gate.py`), and the CI workflow family
  (21 workflow files under `.github/workflows/`).

The architecture thesis from the v2.11 audit still holds and this round
extends it: Master CI is a CI orchestration platform whose measurement
engine is catching up to the specialist products. The next-generation
work is not a rewrite; it is one shared substrate under the existing
checks plus native engines where they pay for themselves.

## 2. The vertical slice that shipped (round-17)

The shared, versioned intermediate representation and its first
consumer are merged and gated:

- `scripts/slop_ir.py` - IR v1: `SymbolIR`, `ImportIR`, `FileIR`,
  `IR_VERSION = 1`, JSON round-trip with a version stamp that refuses
  mismatched bundles. `EXTRACTORS` is the machine-readable coverage
  claim: python (native-ast), typescript/tsx/javascript/go
  (tree-sitter). An absent language is an unsupported language.
- `scripts/slop_ir_python.py` - the Python extractor over the standard
  library's `ast` module: symbols with real nesting, imports with bound
  names, call names, and the `(name, line)` identifier pool. Comments
  and strings never enter the pool.
- `scripts/slop_ir_treesitter.py` - the TS/TSX/JS/Go extractor over the
  grammar pack, iterative by design (deep expression chains overflow a
  recursive visitor). Grammar-missing and error-node trees surface as
  `parse_ok=False` with the reason; a failed parse cannot masquerade as
  a clean file.
- `scripts/slop_symbol_graph.py` - `build_symbol_graph` lowers the
  corpus, subtracts definition sites from the identifier pool, and
  `check_semantic_dead_symbols` reports private symbols no parse-tree
  reference reaches. Registered as advisory `semantic-dead-symbol`
  (remedy, category, facade re-export, catalog row, dispatch after the
  textual `unused-declaration`).
- `benchmarks/accuracy/flawed_python/` - the labeled positive control:
  a dead private function whose only mentions are a comment and a
  string literal.

Benchmark evidence (recorded, reproducible via
`python3 scripts/ir_benchmark.py --json`):

| Phase | Result |
| --- | --- |
| Performance | 161 files lowered in 0.711 s (226.5 files/s), 2,441 symbols, 56,891 identifiers, 0 parse failures |
| Quality | TP 1, FP 0, FN 0 on the labeled corpus; precision 1.0, recall 1.0 |
| Controls | clean_python, clean_js, clean_go, clean_csharp, clean_cpp all silent |
| Delta vs textual oracle | semantic-only: `_audit_helper_only` (prose-kept-alive); textual-only: `serialize_order` (public, never referenced) - complementary, not redundant |
| Full-scan share | IR phase 0.7 s of the 14.2 s whole-repo scan (about 5%) |

The delta row is the point: the textual occurrence oracle in
`slop_dead.py` and the parse-tree oracle answer different questions.
Prose cannot keep a symbol alive anymore; public never-referenced names
stay with the textual check; short names (under 4 chars) stay with the
textual check too, matched to the orphan-module stem rule.

The self-scan contract paid immediately: the repo's own scan flagged
`Finding.__post_init__` - a dataclass-protocol method the interpreter
invokes - before any external user hit it. The exemption set grew
(`__post_init__`, `__init_subclass__`, `__set_name__`,
`__class_getitem__`, `__subclasshook__`, Go `init`/`main`), each with a
regression test. This is the acceptance criterion "framework entry
points are recognized" proven by the gate catching its own false
positive.

### Round-18: the graph bundle (persistence slice shipped)

Slice 2's first half landed in round-18: `save_graph`/`load_graph` in
`slop_symbol_graph.py` persist the whole graph as a JSON bundle that is
versioned twice - `graph_schema` for the bundle layout and `ir_version`
for the stored file records - and a reader refuses either mismatch, so
a stale cache can never silently mix with new extractors' output.
`use_counts` and `parse_failures` are stored explicitly (recomputing
them would need the sources, and the point of persistence is to answer
without them); `def_sites` are rebuilt from the stored files. The
acceptance test pins the honesty property: the loaded graph accuses
exactly the symbols the built graph accused, on the flawed fixture.
What is still open from slice 2: content-hash keyed incremental refresh
and module-path-qualified def sites - neither is implemented and both
stay on the staged list.

### Round-19: the measurement inventory (batch 2 closed, batch 3 opened)

Round-19 extended the measurement engine with four inventoried
oracles, each advisory and each carrying its evidence in the output:

- `sem_sbom.py` (A19) - the declared dependency surface as an
  SPDX-2.3-shaped document: deterministic (digest-named namespace,
  sorted rows, constant creation stamp), six ecosystems, manifest
  licenses, fail-closed license policy. The namespace is a URN, not a
  URL: the SPDX field only requires uniqueness, and a URN cannot bake
  an environment-specific host into source.
- `sem_holdout.py` (A20) - the accuracy corpus now splits: a case's
  split is sha256(salt:id), pure and disjoint, so a widening PR can
  prove its labels generalize across both halves. The real-repo
  manifest (`corpus-real.json`, accuracy-real-v1) validates
  fail-closed and ships empty - vendoring zero repos claims zero
  real-world accuracy.
- `slop_similarity.py` (A9) - the near-duplicate column: pair-shingle
  anchors vote on alignments, blocks grow within a mismatch ratio, and
  the finding's message carries the similarity score. The A/B on the
  synthetic corpus measured the cost at under one percent (136.1 vs
  135.5 files/s), and the fixture benchmark's two new rows judge
  167/167.
- `sem_reruns.py` (C43) - the Actions jobs JSON read as a rerun
  inventory: attempts, conclusions, recovery verdicts, missing
  `run_attempt` as data gaps. The same payload `sem_ci_diagnose.py`
  consumes, so one fetch feeds both tools.

Still open from the staged list: content-hash keyed incremental
refresh and module-path-qualified def sites (slice 2's remainder),
slice 3 TS scope-awareness, and the A20 vendoring itself.

## 3. The language capability matrix

Built from `EXTRACTORS` in `slop_ir.py` plus the live check families.
The rule this matrix enforces: Master CI never claims semantic coverage
it does not possess, and unsupported cells read "not claimed", never
"supported".

| Capability | Python | TS / TSX / JS | Go | C / C++ | C# | Java | Ruby | SQL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Syntax parse (tree-sitter / native) | native `ast` | grammar pack | grammar pack | lizard | lizard | lizard | text | not claimed |
| Symbol IR (definitions, imports, calls) | shipped (round-17) | shipped (round-17) | shipped (round-17) | not claimed | not claimed | not claimed | not claimed | not claimed |
| Cross-file reference resolution | def-site subtraction, repo-wide pool (advisory grade) | same, syntax-grade | same, syntax-grade | not claimed | not claimed | not claimed | not claimed | not claimed |
| Complexity budgets | cognitive + lizard | cognitive | cognitive | lizard + cpp family | lizard + csharp family | lizard | text heuristics | not claimed |
| Deadness oracles | textual + parse-tree | textual + parse-tree | textual + parse-tree | textual | textual | textual | textual | not claimed |
| Security sinks | security column | innerhtml family | ignored-error family | unsafe-c-call | csharp family | broad-throws | not claimed | not claimed |
| Dependency health | manifest oracles | knip-style oracles | go mod oracles | not claimed | lockfile oracles | not claimed | not claimed | not claimed |

Honest gaps the matrix pins down: scope-aware resolution (types,
module systems, overload sets) is claimed nowhere yet - the tree-sitter
slice is syntax-grade by design. Java, Ruby and SQL have no IR path.
These are the staged work below, not silent absence.

## 4. Existing-analyzer inspection: duplication, gaps, integration cost

Read from the modules before proposing anything:

- **Duplicated graph logic (consolidation candidate, no behavior
  change):** three modules build their own cross-file view -
  `slop_imports.py` (phantom/undeclared imports), `slop_cycles.py` +
  `slop_cycle_edges.py` (cycles), `slop_coupling.py` (coupling and
  hubs). The symbol graph does not replace them yet; slice 2 should
  feed all three from one graph partition instead of three file walks.
- **Complexity family:** `slop_cognitive.py` (tree-sitter), and
  `slop_lizard.py` (lizard) overlap on cyclomatic numbers; the budgets
  stay separate because their semantics differ (cognitive is nesting-
  weighted, lizard is decision-count). Documented as intentional.
- **Deadness family:** now two oracles with a documented jurisdiction
  split (private parse-tree vs public textual). `slop_orphan.py` and
  `slop_dead.py` share the occurrence helper by design.
- **Missing capabilities the matrix shows:** scope-aware resolution,
  Java/C#/C++ IR, dataflow of any kind, incremental graph refresh.
  None are faked by existing checks; all are staged work.
- **Performance:** the whole-repo scan is 14.2 s; the IR phase is
  0.7 s of it. The textual passes and doc scanning dominate. No
  evidence yet that analysis time hurts the hook path (`hook_gate.py`
  re-runs the engine per edit - that is the path where latency would
  matter first).

## 5. The Rust decision record

The question: should Rust become a dedicated semantic analysis engine
alongside Python (Python keeps orchestration, GitHub integration,
configuration, policies, reporting, MCP)?

**Decision: not yet, with explicit trigger criteria.** This is an
evidence-based deferral, not a rejection.

Measured evidence for the deferral:

- The IR builder (the candidate hot loop) costs 0.7 s of the 14.2 s
  scan - about 5%. Rewriting it in Rust cannot buy a user-visible
  improvement today.
- The heavy parsers already run in C: Python's `ast` is C, lizard is
  C, tree-sitter's runtime and grammars are C. A Rust engine would
  re-wrap the same grammars through its own FFI.
- A Rust component adds a real integration cost: a toolchain in CI, a
  build matrix across runner images, artifact packaging for the
  consumer-copy contract (`hook_gate.py` ships the engine as plain
  source files), and an FFI or IPC boundary per finding.

The trigger criteria - when measurement crosses one of these, the
`semcore` crate becomes the next slice:

1. The graph/IR phase exceeds 25% of the scan's p95 wall time on the
   canary corpus (measure per round with `ir_benchmark.py`).
2. Incremental per-edit latency for the hook gate exceeds its budget
   (the hook re-scans on every edit; an incremental parse-and-patch
   engine is the classic Rust payoff).
3. A slice genuinely needs cross-file dataflow or type-aware
   resolution at scale (the language-native engines below are the
   alternative path there - they may make Rust unnecessary for that
   specific slice).

Native semantic tooling, evaluated per language for the staged slices:
Python stays on `ast` plus type-aware tooling (mypy's tables are
available in-process); Go gets `go/ast` with `go/types` when a Go slice
lands (the stdlib parsers are exact, no grammar work); TypeScript gets
the Compiler API through a pinned Node sidecar when scope-aware TS
resolution is needed; Java (javac/JDT), C# (Roslyn), C/C++ (Clang
tooling) are compiler-grade but each costs a toolchain in CI - they
enter only with a slice that pays for it. SQL stays unclaimed until a
dialect-aware, schema-aware parser becomes a real requirement. Cargo
and rustc themselves would be the first consumers of the Rust engine if
it lands (dogfood the analyzer on its own host language).

Tree-sitter's role stays syntax: parsing, incremental text patches,
grammar-shared symbol extraction. Incremental re-parse of one changed
file is cheap and useful; it is not semantic understanding, and nothing
in this design mistakes one for the other.

## 6. Limitations of the shipped slice

Stated plainly, enforced by tests where possible:

- The use oracle is repo-wide and line-granular: two references on the
  same line as a definition count as the definition (conservative -
  biases toward silence, never toward false findings).
- Same-name shadows across files share one use pool: a `_helper` in
  `pkg_a` is kept alive by a different `_helper`'s call in `pkg_b`.
  Slice 2 adds module-path qualification to def sites.
- TS/JS arrow-function consts (`const f = () => ...`) are not symbols
  yet; only declaration nodes are. Documented, tested as absent.
- Reflection, plugin registration and template entry points remain
  outside parse-tree sight - the finding is advisory with the remedy
  that says "make that registration explicit".
- Parse failures and unsupported languages are visible states
  (`parse_failures`, `EXTRACTORS`), never silently shrunken corpora.

## 7. The staged next steps

1. **Slice 2 - one graph, three consumers:** persist IR bundles under a
   keyed cache, qualify def sites by module path, and feed
   phantom-import, cycle and coupling verdicts from the same partition.
   Incremental refresh keyed by content hash; a clean full rebuild must
   equal the refreshed graph (the acceptance test writes itself).
2. **Slice 3 - TS scope-awareness:** the Compiler-API sidecar for
   resolution-graded TS findings, or tree-sitter symbol qualification
   as the zero-dependency middle step.
3. **Slice 4 - native engines by demand:** go/types, Roslyn, Clang or
   JDT behind the same IR, each gated by a slice that needs it and a
   capability-matrix row that stops saying "not claimed".
4. **Rust `semcore`:** only when a trigger criterion in section 5
   fires, with the benchmark as the referee.
