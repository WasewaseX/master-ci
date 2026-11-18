# Accuracy corpus and protocol (v2.18, audit finding H)

The fixture benchmark proves declared outcomes; the performance
benchmark proves speed. Neither proves detection accuracy. This
directory is the seed of the instrument that does:
`scripts/accuracy_benchmark.py` judges every labeled row - the 110
`fixtures/bench` manifests as positives (the v2.18 seed twelve, the
round-8 tranche, the round-9 Python pair, the round-10 deepening that
pushed `csharp-async-void`, `csharp-broad-catch`, `rust-unwrap` and
`debug-print` to the five-judged-row promotion threshold, and the
round-13 C/C++ column deepening that pushed all eight `cpp-*` checks
to the same threshold - one labeled row per new fixture and, for the
oracles whose exemption logic a future edit could break, the nearest
negative: the named cast, the constexpr constant, the newline
literal, the main()-owning CLI, the unique_ptr owner, the real error
throw, the nullptr boundary call, the translation-unit directive, the
loud catch, the test-gated unwrap, the package-main panic, the
drifted type shape, the On-prefix handler, the exception-reading
catch, the documented `expect()`, the stderr writer), and the five
`clean_*` projects (python, js, go, csharp, cpp) as negative controls
where any quality-dimension finding is a false
positive - and reports per-check precision, recall and a promotion
verdict. Uncertain rules stay advisory until the evidence justifies a
blocking promotion; the verdict column says which.

## What is here

- `corpus.json` - the manifest: schema, and the corpus-declared known
  false positives, each with a reason. Known FPs stay visible in every
  report; they do not fail the run.
- `clean_python/` - a complete warehouse package: models, service, a
  CLI entry that consumes the public API, no third-party deps.
- `clean_js/` - a complete ES module with a package entry point and a
  consumer; the single declared dependency is used.
- `clean_go/` - a runnable CLI with parsing, a report and error paths.

## Reproduce

```bash
python3 scripts/accuracy_benchmark.py --json accuracy-report.json
```

The report records the dataset/engine version (`git describe`), the
promotion rule (precision >= 0.8 across >= 5 judged rows), and every
false positive with its location. Exit 1 on any label violation or
unexpected false positive; exit 2 on corpus misconfiguration.

## Widening the corpus (the holdout protocol)

1. Real public repositories enter as subdirectories with a `corpus.json`
   entry naming their upstream URL and the exact commit hash; vendored
   trees are read-only evidence, never modified to fit the engines.
2. Labels are per check per fixture, written before the engine runs on
   the new material; a label the engine disagrees with becomes a
   violation row, not a relabeled pass.
3. Clean and flawed projects alternate: every analyzer gains rows in
   both directions, so precision and recall both mean something.
4. Runs record dataset version, commit hashes, analyzer versions
   (`configs/tool-versions.yml` pins) and the exact command. The JSON
   report (`--json`) is the artifact a promotion PR attaches.
5. Competitor comparisons (ruff F401 vs phantom-import, jscpd vs the
   duplication metrics) run on the same corpus with the same labels and
   land in the report as separate rows - same evidence standard, no
   special pleading.
6. A check earns "promotion-ready evidence" only through this corpus;
   promotion itself stays a reviewed decision, never an automatic flip.

## Round-8 rows (2026-11-17)

The round-8 checks each carry one judged positive row and, where the
oracle has exemption logic worth pinning, one judged clean row - 34
rows over 34 fixture trees, plus two new negative controls
(`clean_csharp`, `clean_cpp`) hand-shaped to pass every quality
dimension. All rows read precision 1.00, recall 1.00. The promotion
verdict stays honest: five judged rows per check is the floor, and
one labeled row is not five - the verdict text says
`advisory-only (insufficient accuracy evidence)` until the corpus is
widened per the protocol above.


## Round-15 rows (2026-11-18)

The C# accuracy column deepening: the eleven one-judged-row csharp
checks (console-leftover, empty-catch-rethrow, if-ladder, index-loop,
linq-count, not-implemented, null-forgiving, string-concat-in-loop,
suppressed-warning, sync-over-async, redundant-doc-comment) each gained
four diverse real-shaped positives and one nearest-negative clean row
pinning the rule's exemption logic - the injected logger, the
handled-and-flagged catch, the switch statement, the foreach loop, the
.Any() guard, the real method body, the explicit null check, the
StringBuilder, the awaited call, the justified suppression, and the
informative summary. 55 new fixtures; the benchmark table is 165 rows.
Labels were written before the engine ran; every row agreed on the
first run (165/165 PASS), and the accuracy report reads precision 1.00
recall 1.00 judged 5 on all eleven columns - promotion-ready evidence
under the same floor (precision >= 0.8 across >= 5 judged rows).
Twenty-four checks now carry promotion-ready evidence; the promotion
act itself stays the reviewed decision the protocol asks for, with
this report (`python3 scripts/accuracy_benchmark.py --json`) as the
artifact a promotion change attaches.
