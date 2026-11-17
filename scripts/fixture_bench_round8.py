"""The round-8 fixture rows: slugs, rules and the exemption negatives.

Data module for fixture_benchmark.PROBES - the tranche's table lives
here so the runner module keeps its own 400-line budget. Every detected
slug's rule is its own name except the two TS rows, whose check ids
differ from their slugs; every clean row maps to the rule it must keep
silent.
"""

from __future__ import annotations

_ROUND8_DETECTED = (
    "csharp-async-void",
    "csharp-broad-catch",
    "csharp-console-leftover",
    "csharp-empty-catch-rethrow",
    "csharp-if-ladder",
    "csharp-index-loop",
    "csharp-linq-count",
    "csharp-not-implemented",
    "csharp-null-forgiving",
    "csharp-redundant-doc-comment",
    "csharp-string-concat-in-loop",
    "csharp-suppressed-warning",
    "csharp-sync-over-async",
    "cpp-c-style-cast",
    "cpp-define-constant",
    "cpp-endl-in-stream",
    "cpp-iostream-leftover",
    "cpp-manual-delete",
    "cpp-not-implemented",
    "cpp-null-literal",
    "cpp-using-namespace-std-in-header",
    "go-library-panic",
    "rust-unwrap",
    "scala-broad-catch",
    "ts-redundant-coercion",
    "ts-hidden-fallback",
)

_ROUND8_RENAMED = {
    "ts-redundant-coercion": "redundant-type-coercion",
    "ts-hidden-fallback": "hidden-fallback",
}

_ROUND8_CLEAN_RULES = {
    "csharp-catch-reads-exception-clean": "csharp-broad-catch",
    "csharp-null-guard-clean": "csharp-null-forgiving",
    "cpp-modern-clean": "cpp-c-style-cast",
    "ts-loud-catch-clean": "hidden-fallback",
    "go-main-panic-clean": "go-library-panic",
    "rust-test-unwrap-clean": "rust-unwrap",
    "duplicate-type-clean": "duplicate-type-declaration",
}
