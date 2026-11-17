"""The round-9 fixture rows: the last two Python columns of the catalog.

Data module for fixture_benchmark.PROBES - the tranche's slugs live here
so the runner module keeps its own 400-line budget. The probe is one
Python-family sweep (``slop_py_family``): the AST walk drives broad-except
through check_py_ast and print-debug through check_py_print_debug, so a
single probe answers every row.
"""

from __future__ import annotations

ROUND9_DETECTED = ("py-broad-except", "py-print-debug")

ROUND9_RENAMED = {
    "py-broad-except": "broad-except",
    "py-print-debug": "debug-print",
}

ROUND9_CLEAN_RULES = {
    "py-broad-declared-clean": "broad-except",
    "py-print-main-guard-clean": "debug-print",
}
