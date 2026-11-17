# scanaislop claims triage (2026-11-18)

The scanaislop product (`npx aislop scan`) reported 18 claims against
this repository. Every claim was triaged against the real source before
any fix landed; nothing below is taken from the report on trust. The
claims arrived in three groups, reproduced verbatim in the tables.

## Group 1 - complexity/file-too-large (4 claims, all metric disagreements)

| Claim | File | Claimed lines | Total lines | Non-blank lines | Verdict |
| --- | --- | --- | --- | --- | --- |
| file-too-large | scripts/fixture_benchmark.py | 441 | 458 | 389 | false positive under our budget |
| file-too-large | scripts/slop_cycles.py | 441 | 460 | 385 | false positive under our budget |
| file-too-large | scripts/slop_dead.py | 441 | 441 | 388 | false positive under our budget |
| file-too-large | scripts/slop_structural.py | 441 | 451 | 392 | false positive under our budget |

Our rule is `bloat-long-file` with `BLOAT_FILE_LINES = 400` counting
**non-blank** lines (`slop_structural.py:16`), the metric the verdict
message itself states ("file spans N non-blank lines (budget 400)"),
with the `long-but-cohesive` downgrade when LCOM4 proves the file is
one responsibility. scanaislop counts **total** lines. All four files
pass the documented budget; the `.ai-integrity-ignore` contract ("no
scope-offs: scripts/ is a package of slop_*/mcp_*/fix_* modules, each
inside the 400-line file budget") is satisfied. The stale 441s in the
claims also show the scan ran against an older commit.

## Group 2 - ai-slop/unused-import (11 claims, all false positives, one real defect found nearby)

`scripts/ai_slop_detector.py` is the vendored facade: master-ci.yml
probes exactly `scripts/ai_slop_detector.py`, foreign repos import it
by path, and its docstring documents "every public name is re-exported
here". Every flagged line carries `# noqa: F401`, and the flagged
symbols resolve to real consumers that tests reach through importlib
(`mod.check_class_cohesion`, `mod.ArchConfigError`,
`mod.MAX_LCOM4_FINDINGS`) - dynamic attribute access a static import
graph cannot see. scanaislop flagged precisely the names whose
consumers are dynamic.

| Claim | Verdict | Why |
| --- | --- | --- |
| check_abstraction unused | false positive | tests/test_abstraction.py, fixture_bench_fixtures.py |
| ArchConfigError unused | false positive | tests/test_detector.py via importlib |
| check_architecture_rules unused | false positive | facade contract (used in slop_scan.py:309 directly) |
| parse_args unused | false positive | facade contract, re-exported from slop_cli |
| check_cognitive unused | false positive | facade contract |
| check_import_cycles unused | false positive | fixture_bench_fixtures.py |
| check_unused_dependencies unused | false positive | fixture_bench_fixtures.py |
| MAX_LCOM4_FINDINGS unused | false positive | tests/test_lcom4.py via importlib |
| check_class_cohesion unused | false positive | fixture_bench_fixtures.py |
| check_test_redundancy unused | false positive | tests/test_redundancy.py |
| slop_scan.py:23 ArchConfigError unused | **true, fixed** | dead binding masked by blanket noqa |

Two contract-level fixes landed with the triage:

1. `slop_scan.py` no longer imports `ArchConfigError` (it never used
   it; the CLI layer catches it).
2. `tests/test_facade_surface.py` asserts the facade re-exports every
   top-level `check_*` in every `slop_*` module. The assertion found
   **24 names** the narrative contract claimed but did not deliver
   (check_commentary family, check_cpp_slop, check_csharp_slop,
   check_dispatch_ladders, the residue family, check_ts_slop, ...);
   the facade now delivers them, and the test keeps the surface
   machine-checked.

Our own unused-import ability already exists as `phantom-import`
(`slop_imports.py`): binding-aware (fixed in v2.18 after audit finding
A), noqa-respecting, py + js. It produced zero false positives on this
repo's facade while the competitor's checker produced eleven.

## Group 3 - ai-slop/python-repetitive-dispatch (3 claims: 1 already caught by us, 2 real gaps, all fixed)

| Claim | Verdict | Action |
| --- | --- | --- |
| slop_corpus.py:211 5 branches on `state` | true; our scan already flagged it | refactored to `_EXCLUSION_COUNTERS` dict |
| slop_corpus.py:213 4 branches on `state` | true but double-reported (same ladder, counted from the second elif) | same refactor removes it |
| flaky_parsers.py:73 4 branches on `suffix` | **true and invisible to us** | guard-run detector added; file refactored to `_PARSERS` dict |

The real gap: `check_dispatch_ladders` only walked if/elif chains.
`parse_file` dispatched through four separate guard-ifs with returns -
the shape scanaislop caught, ours could not. The family now lives in
`slop_dispatch.py` (slop_residue.py had reached 452 non-blank lines
against its own 400 budget): a run is consecutive sibling ifs, empty
else clauses, one dispatch subject, every branch leaving the block.
Branches that do not exit are conditional features, not dispatch, and
stay unflagged - the precision guard that keeps this rule from
becoming the false-positive factory the group-2 claims demonstrate.

## Bonus: the engine's own false positive, fixed

The self-scan flagged `undeclared-import` on
`scripts/accuracy_benchmark.py:8` - which is inside the module
docstring. The inventory ran a line regex
(`^\s*(?:import|from)\s+...`) over raw text and read the docstring
sentence "from a small seed:" as an import of a module named `a`. The
inventory is AST now (`_py_import_tops`); prose cannot become an
import, relative imports are skipped, and parse failures still report
through analysis-coverage instead of passing silently.

## Why we "did not have" these abilities and gates - the honest answer

- **file-too-large**: we have it (`bloat-long-file`, 400 non-blank +
  cohesion downgrade). The claims were metric disagreement.
- **unused-import**: we have it (`phantom-import`, binding-aware,
  noqa-aware, py + js). The claims were facade false positives the
  noqa contract already handles. What we lacked was proof the facade
  contract is real - now `test_facade_surface.py` provides it.
- **repetitive-dispatch**: we had it for elif chains only. The
  guard-run shape is the genuine gap this round closed.
- **holding PRs to it**: the pieces existed (blocking-score gate,
  slop-score ceiling, quality ratchet) but the ratchet had no
  committed floor, so nothing actually bit. The floor is recorded now.

## The hold-every-PR wiring

`.quality-baseline.json` (schema v2) records the post-fix floor: 5
check floors with per-finding identities (analysis-coverage 1,
boilerplate-duplication 1, high-cognitive 1, hotspot-risk 1,
prose-block 7; every other check floor 0). The workflow's ratchet
defaults to `auto`, so the committed file activates the enforce step
for every pull request. Proven in both directions during this round:

- a planted four-branch dispatch ladder -> ratchet exits 1 (CI fails);
- the clean tree -> exits 0.

A regression in dispatch shapes, unused imports, file budgets or
undeclared imports now fails CI instead of joining the advisory pile.
When debt is legitimately paid or adopted, re-record the floor in the
reviewed commit: `python3 scripts/ai_slop_detector.py .
--write-quality-baseline .quality-baseline.json`.

## Evidence

- Tests: 1109 passed, 0 failed (1043 before the round; +61 facade
  contract, +5 dispatch/undeclared regressions).
- Self-scan: blocking 4 / advisory 12 / findings 11 / verdict CLEAN /
  maintainability A; the diff against the lizard-complete baseline
  shows exactly the two claim findings removed and zero findings
  added by the round.
- Hosted-gate parity in the sandbox: pinned lizard 1.25.1,
  tree-sitter language pack, ruff 0.17.4, mypy 2.4.1-era invocation;
  `ruff check .` clean, `ruff format --check .` 145/145 (nine files -
  seven pre-existing drifts plus two from this round - reformatted),
  mypy clean over 89 source files.
