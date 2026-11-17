#!/usr/bin/env python3
"""Master CI integrity engine: the scan orchestration and library surface.

The check catalog lives in the slop_* modules; the command-line contract
(exit codes, flags, gates) lives in slop_cli.py, re-exported here so tests
and the MCP server keep their import surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The tests import this file by path (importlib), so the sibling slop_*
# modules may not be importable yet - anchor the script's own directory.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slop_abstraction import check_abstraction  # noqa: F401
from slop_arch import ArchConfigError, check_architecture_rules  # noqa: F401
from slop_bloat import (  # noqa: F401
    check_bloat,
)
from slop_categories import (  # noqa: F401
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    CHECK_CATEGORIES,
    category_of,
)

# Orchestration, CLI and the re-exported test surface: the checker
# implementations live in the slop_* sibling modules; this file stays the
# single entry point the workflow invokes and the tests import.
# The scan pipeline lives in slop_scan (v2.18: the file had outgrown its
# own 400-line budget - the structural law applies to its own source).
# This module stays the single entry point the workflow invokes and the
# tests import: every public name is re-exported here.
# The CLI layer (argument parsing, exit-code contract, gate math) lives in
# slop_cli.py so this module stays inside the same 400-line file budget it
# enforces on consumers. Re-exported for the tests and MCP surface.
from slop_cli import main, parse_args  # noqa: E402,F401
from slop_cognitive import check_cognitive  # noqa: F401
from slop_commentary import (  # noqa: F401
    check_commentary,
    check_hardcoded_ids,
    check_hardcoded_urls,
    check_js_dead_patterns,
    check_js_empty_functions,
    check_js_thin_wrappers,
    check_meta_comments,
    check_prose_blocks,
    check_unknown_directives,
)
from slop_comments import (  # noqa: F401
    extract_comments,
    phrase_findings,
    scan_comment_vocabulary,
)
from slop_coupling import (  # noqa: F401
    COUPLING_FAN_OUT_BUDGET,
    HUB_FAN_IN_MIN,
    UNSTABLE_HUB_MAX_INSTABILITY,
    check_coupling_metrics,
    measure_coupling,
)
from slop_cpp import check_cpp_slop  # noqa: F401
from slop_csharp import check_csharp_slop  # noqa: F401
from slop_cycles import check_import_cycles  # noqa: F401
from slop_dead import (  # noqa: F401
    check_orphan_modules,
    check_unused_declarations,
)
from slop_debt import (  # noqa: F401
    BLOCKING_MINUTES,
    DEBT_RATIO_BUDGET,
    DEFAULT_MINUTES,
    DESIGN_CHECKS,
    DESIGN_MINUTES,
    DEV_MIN_PER_LINE,
    STRUCTURAL_MINUTES,
    check_debt_metrics,
)
from slop_density import (  # noqa: F401
    COMPLEXITY_DENSITY_BUDGET,
    DUP_DENSITY_BUDGET,
    check_analysis_coverage,
    density_findings,
    measure,
    measure_corpus,
)
from slop_deps import check_unused_dependencies  # noqa: F401
from slop_deps_stacks import check_stack_dependencies  # noqa: F401
from slop_dispatch import check_dispatch_ladders  # noqa: F401
from slop_duplicates import (  # noqa: F401
    MIN_SOURCES_FOR_INVENTORY,
    PROVENANCE_MARKERS,
    scan_duplicates,
)
from slop_family import (  # noqa: F401
    check_blanket_suppression,
    check_debug_left,
    check_exceptions_js_family,
    check_go,
    check_js_slop,
    check_jvm_slop,
    check_placeholder_credential,
    check_placeholder_names,
    check_rust,
)
from slop_files import (  # noqa: F401
    _scoped_out,
    is_ignored,
    is_test_path,
    iter_files,
    load_ignore_rules,
    load_phrases,
    read_text_lax,
)
from slop_hotspot import (  # noqa: F401
    HOTSPOT_RISK_BUDGET,
    check_hotspot_metrics,
    churn_map,
)
from slop_identities import check_of  # noqa: F401
from slop_imports import (  # noqa: F401
    DepContext,
    check_phantom_imports,
    check_undeclared_imports,
    collect_js_dependencies,
    collect_py_dependencies,
)
from slop_lcom4 import MAX_LCOM4_FINDINGS, check_class_cohesion  # noqa: F401
from slop_model import (  # noqa: F401
    ADVISORY_ONLY_CHECKS,
    BLOCKING_CHECKS,
    ENFORCED_CHECKS,
    REMEDIES,
    STRUCTURAL_BUDGET_CHECKS,
    Comment,
    Finding,
    _finding,
    enforce_structural_budget,
    is_blocking,
)
from slop_patterns import (  # noqa: F401
    CODE_EXTS,
    DOC_EXTS,
    FAMILY_OF_EXT,
    IGNORE_FILE,
    MAX_ANNOTATIONS,
    SKIP_DIRS,
    SKIP_PATTERN,
    TODO_PATTERN,
    TOOL_NAME,
    UI_EXTS,
)
from slop_py_checks import (  # noqa: F401
    check_elided_code,
    check_placeholder_impl,
    check_py_ast,
)
from slop_py_family import (  # noqa: F401
    check_py_broad_except_handler,
    check_py_print_debug,
)
from slop_reliability import (  # noqa: F401
    RELIABILITY_CHECKS,
    check_reliability_rating,
    check_shared_mutable_state,
    check_unclosed_resource,
    reliability_grade,
)
from slop_reporting import (  # noqa: F401
    advisory_score,
    ai_integrity_score,
    annotate,
    apply_baseline,
    blocking_score,
    category_breakdown,
    check_tier,
    file_deficit,
    fingerprint,
    project_score,
    write_baseline,
    write_report,
    write_sarif,
)
from slop_residue import (  # noqa: F401
    check_constant_condition,
    check_duplicate_imports,
    check_generic_names,
    check_handler_debts,
    check_range_len_loop,
    check_residue,
    check_tautological_tests,
    check_unreachable,
)
from slop_scan import (  # noqa: E402,F401
    ScanContext,
    _apply_suppression,
    _file_findings,
    _repo_level_findings,
    _scan_one_file,
    inventory_findings,
    provenance_findings,
    scan,
    scan_code_file,
)
from slop_test_redundancy import check_test_redundancy  # noqa: F401
from slop_ts_checks import check_ts_slop  # noqa: F401
from slop_type_decls import check_duplicate_type_decls  # noqa: F401
from slop_ui_prose import (  # noqa: F401
    check_doc_extras,
    check_ui_slop,
    scan_doc_file,
)

if __name__ == "__main__":
    sys.exit(main())
