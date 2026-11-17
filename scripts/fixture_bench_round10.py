"""The round-10 fixture rows: the accuracy corpus deepening.

Data module for fixture_benchmark.PROBES - the tranche's slugs live
here so the runner module keeps its own 400-line budget. The tranche
pushes four checks to the five-judged-row promotion threshold
(csharp-async-void, csharp-broad-catch, rust-unwrap, debug-print) and
adds the exemption-protecting clean rows the corpus protocol asks for:
the On-prefix handler, the exception-reading catch, the expect() call
and the stderr writer. Two probes answer every row: the round-8 family
sweep for the C# and Rust columns, the round-9 Python-family sweep for
the print rows.
"""

from __future__ import annotations

ROUND10_FAMILY_DETECTED = (
    "csharp-async-void-queue-worker",
    "csharp-async-void-service-save",
    "csharp-async-void-handler-pair",
    "csharp-async-void-callback",
    "csharp-broad-catch-log-and-default",
    "csharp-broad-catch-system-qualified",
    "csharp-broad-catch-two-handlers",
    "csharp-broad-catch-cache-fallback",
    "rust-unwrap-config-lookup",
    "rust-unwrap-parse-result",
    "rust-unwrap-lock-poison",
    "rust-unwrap-before-cfg-test",
)

ROUND10_FAMILY_RENAMED = {
    "csharp-async-void-queue-worker": "csharp-async-void",
    "csharp-async-void-service-save": "csharp-async-void",
    "csharp-async-void-handler-pair": "csharp-async-void",
    "csharp-async-void-callback": "csharp-async-void",
    "csharp-broad-catch-log-and-default": "csharp-broad-catch",
    "csharp-broad-catch-system-qualified": "csharp-broad-catch",
    "csharp-broad-catch-two-handlers": "csharp-broad-catch",
    "csharp-broad-catch-cache-fallback": "csharp-broad-catch",
    "rust-unwrap-config-lookup": "rust-unwrap",
    "rust-unwrap-parse-result": "rust-unwrap",
    "rust-unwrap-lock-poison": "rust-unwrap",
    "rust-unwrap-before-cfg-test": "rust-unwrap",
}

ROUND10_PY_DETECTED = (
    "py-print-service-module",
    "py-print-model-module",
    "py-print-nested-helper",
    "py-print-multiple-sites",
)

ROUND10_PY_RENAMED = {
    "py-print-service-module": "debug-print",
    "py-print-model-module": "debug-print",
    "py-print-nested-helper": "debug-print",
    "py-print-multiple-sites": "debug-print",
}

ROUND10_FAMILY_CLEAN_RULES = {
    "csharp-async-void-on-prefix-clean": "csharp-async-void",
    "csharp-broad-catch-logs-exception-clean": "csharp-broad-catch",
    "rust-unwrap-expect-clean": "rust-unwrap",
}

ROUND10_PY_CLEAN_RULES = {
    "py-print-stderr-clean": "debug-print",
}
