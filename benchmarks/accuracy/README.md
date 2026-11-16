# Accuracy corpus and protocol (v2.18, audit finding H)

The fixture benchmark proves declared outcomes; the performance
benchmark proves speed. Neither proves detection accuracy. This
directory is the seed of the instrument that does:
`scripts/accuracy_benchmark.py` judges every labeled row - the twelve
`fixtures/bench` manifests as positives, the `clean_*` projects as
negative controls where any quality-dimension finding is a false
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

```
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
