#!/usr/bin/env python3
"""Master CI integrity engine: the scan orchestration and library surface.

The check catalog lives in the slop_* modules; the command-line contract
(exit codes, flags, gates) lives in slop_cli.py, re-exported here so tests
and the MCP server keep their import surface.

Every re-export below uses the PEP 484 redundant-alias convention
(``from X import Y as Y``): the alias is the explicit, machine-readable
declaration that this module publishes the name rather than consumes it.
Static import-graph scanners honor that convention directly (scanaislop's
unused-import rule skips a redundant alias), and the ``# noqa: F401``
directive keeps ruff and the engine's own phantom-import check quiet -
three scanners, one honest re-export surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The tests import this file by path (importlib), so the sibling slop_*
# modules may not be importable yet - anchor the script's own directory.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slop_abstraction import check_abstraction as check_abstraction  # noqa: F401,E402
from slop_arch import (  # noqa: F401,E402
    ArchConfigError as ArchConfigError,
    check_architecture_rules as check_architecture_rules,
)
from slop_bloat import check_bloat as check_bloat  # noqa: F401,E402
from slop_categories import (  # noqa: F401,E402
    CATEGORY_LABELS as CATEGORY_LABELS,
    CATEGORY_ORDER as CATEGORY_ORDER,
    CHECK_CATEGORIES as CHECK_CATEGORIES,
    category_of as category_of,
)

# Orchestration, CLI and the re-exported test surface: the checker
# implementations live in the slop_* sibling modules; this file stays the
# single entry point the workflow invokes and the tests import.
# The scan pipeline lives in slop_scan (v2.18: the file had outgrown its
# own 400-line budget - the structural law applies to its own source).
# The CLI layer (argument parsing, exit-code contract, gate math) lives in
# slop_cli.py for the same budget reason. Re-exported for the tests and
# MCP surface.
from slop_cli import main as main, parse_args as parse_args  # noqa: F401,E402
from slop_cognitive import check_cognitive as check_cognitive  # noqa: F401,E402
from slop_commentary import (  # noqa: F401,E402
    check_commentary as check_commentary,
    check_hardcoded_ids as check_hardcoded_ids,
    check_hardcoded_urls as check_hardcoded_urls,
    check_js_dead_patterns as check_js_dead_patterns,
    check_js_empty_functions as check_js_empty_functions,
    check_js_thin_wrappers as check_js_thin_wrappers,
    check_meta_comments as check_meta_comments,
    check_prose_blocks as check_prose_blocks,
    check_unknown_directives as check_unknown_directives,
)
from slop_comments import (  # noqa: F401,E402
    extract_comments as extract_comments,
    phrase_findings as phrase_findings,
    scan_comment_vocabulary as scan_comment_vocabulary,
)
from slop_coupling import (  # noqa: F401,E402
    COUPLING_FAN_OUT_BUDGET as COUPLING_FAN_OUT_BUDGET,
    HUB_FAN_IN_MIN as HUB_FAN_IN_MIN,
    UNSTABLE_HUB_MAX_INSTABILITY as UNSTABLE_HUB_MAX_INSTABILITY,
    check_coupling_metrics as check_coupling_metrics,
    measure_coupling as measure_coupling,
)
from slop_cpp import check_cpp_slop as check_cpp_slop  # noqa: F401,E402
from slop_csharp import check_csharp_slop as check_csharp_slop  # noqa: F401,E402
from slop_cycles import check_import_cycles as check_import_cycles  # noqa: F401,E402
from slop_dead import check_unused_declarations as check_unused_declarations  # noqa: F401,E402
from slop_debt import (  # noqa: F401,E402
    BLOCKING_MINUTES as BLOCKING_MINUTES,
    DEBT_RATIO_BUDGET as DEBT_RATIO_BUDGET,
    DEFAULT_MINUTES as DEFAULT_MINUTES,
    DESIGN_CHECKS as DESIGN_CHECKS,
    DESIGN_MINUTES as DESIGN_MINUTES,
    DEV_MIN_PER_LINE as DEV_MIN_PER_LINE,
    STRUCTURAL_MINUTES as STRUCTURAL_MINUTES,
    check_debt_metrics as check_debt_metrics,
)
from slop_density import (  # noqa: F401,E402
    COMPLEXITY_DENSITY_BUDGET as COMPLEXITY_DENSITY_BUDGET,
    DUP_DENSITY_BUDGET as DUP_DENSITY_BUDGET,
    check_analysis_coverage as check_analysis_coverage,
    density_findings as density_findings,
    measure as measure,
    measure_corpus as measure_corpus,
)
from slop_deps import check_unused_dependencies as check_unused_dependencies  # noqa: F401,E402
from slop_deps_stacks import check_stack_dependencies as check_stack_dependencies  # noqa: F401,E402
from slop_dispatch import check_dispatch_ladders as check_dispatch_ladders  # noqa: F401,E402
from slop_duplicates import (  # noqa: F401,E402
    MIN_SOURCES_FOR_INVENTORY as MIN_SOURCES_FOR_INVENTORY,
    PROVENANCE_MARKERS as PROVENANCE_MARKERS,
    scan_duplicates as scan_duplicates,
)

# scanaislop round-16 parity column: the security sinks, the ecosystem
# oracles and the TS assertions ride the same facade contract as every
# other public check - redundant-alias re-exports, machine-readable.
from slop_ecosystem import (  # noqa: F401,E402
    check_unused_css as check_unused_css,
    check_vulnerable_dependencies as check_vulnerable_dependencies,
)
from slop_family import (  # noqa: F401,E402
    check_blanket_suppression as check_blanket_suppression,
    check_debug_left as check_debug_left,
    check_exceptions_js_family as check_exceptions_js_family,
    check_go as check_go,
    check_js_slop as check_js_slop,
    check_jvm_slop as check_jvm_slop,
    check_placeholder_credential as check_placeholder_credential,
    check_placeholder_names as check_placeholder_names,
    check_rust as check_rust,
)
from slop_files import (  # noqa: F401,E402
    _scoped_out as _scoped_out,
    is_ignored as is_ignored,
    is_test_path as is_test_path,
    iter_files as iter_files,
    load_ignore_rules as load_ignore_rules,
    load_phrases as load_phrases,
    read_text_lax as read_text_lax,
)
from slop_hotspot import (  # noqa: F401,E402
    HOTSPOT_RISK_BUDGET as HOTSPOT_RISK_BUDGET,
    check_hotspot_metrics as check_hotspot_metrics,
    churn_map as churn_map,
)
from slop_identities import check_of as check_of  # noqa: F401,E402
from slop_imports import (  # noqa: F401,E402
    DepContext as DepContext,
    check_phantom_imports as check_phantom_imports,
    check_undeclared_imports as check_undeclared_imports,
    collect_js_alias_prefixes as collect_js_alias_prefixes,
    collect_js_dependencies as collect_js_dependencies,
    collect_py_dependencies as collect_py_dependencies,
)
from slop_js_shapes import (  # noqa: F401,E402
    check_no_new_array as check_no_new_array,
    check_set_state_in_effect as check_set_state_in_effect,
)
from slop_lcom4 import (  # noqa: F401,E402
    MAX_LCOM4_FINDINGS as MAX_LCOM4_FINDINGS,
    check_class_cohesion as check_class_cohesion,
)
from slop_lint_shapes import (  # noqa: F401,E402
    check_bare_node_builtin as check_bare_node_builtin,
    check_impure_render_call as check_impure_render_call,
    check_prefer_starts_ends_with as check_prefer_starts_ends_with,
    check_self_assign as check_self_assign,
    check_useless_empty_export as check_useless_empty_export,
    check_useless_escape as check_useless_escape,
)
from slop_model import (  # noqa: F401,E402
    ADVISORY_ONLY_CHECKS as ADVISORY_ONLY_CHECKS,
    BLOCKING_CHECKS as BLOCKING_CHECKS,
    ENFORCED_CHECKS as ENFORCED_CHECKS,
    REMEDIES as REMEDIES,
    STRUCTURAL_BUDGET_CHECKS as STRUCTURAL_BUDGET_CHECKS,
    Comment as Comment,
    Finding as Finding,
    _finding as _finding,
    enforce_structural_budget as enforce_structural_budget,
    is_blocking as is_blocking,
)
from slop_orphan import check_orphan_modules as check_orphan_modules  # noqa: F401,E402
from slop_patterns import (  # noqa: F401,E402
    CODE_EXTS as CODE_EXTS,
    DOC_EXTS as DOC_EXTS,
    FAMILY_OF_EXT as FAMILY_OF_EXT,
    IGNORE_FILE as IGNORE_FILE,
    MAX_ANNOTATIONS as MAX_ANNOTATIONS,
    SKIP_DIRS as SKIP_DIRS,
    SKIP_PATTERN as SKIP_PATTERN,
    TODO_PATTERN as TODO_PATTERN,
    TOOL_NAME as TOOL_NAME,
    UI_EXTS as UI_EXTS,
)
from slop_py_checks import (  # noqa: F401,E402
    check_elided_code as check_elided_code,
    check_placeholder_impl as check_placeholder_impl,
    check_py_ast as check_py_ast,
)
from slop_py_family import (  # noqa: F401,E402
    check_py_broad_except_handler as check_py_broad_except_handler,
    check_py_print_debug as check_py_print_debug,
)
from slop_reliability import (  # noqa: F401,E402
    RELIABILITY_CHECKS as RELIABILITY_CHECKS,
    check_reliability_rating as check_reliability_rating,
    check_shared_mutable_state as check_shared_mutable_state,
    check_unclosed_resource as check_unclosed_resource,
    reliability_grade as reliability_grade,
)
from slop_reporting import (  # noqa: F401,E402
    advisory_score as advisory_score,
    ai_integrity_score as ai_integrity_score,
    annotate as annotate,
    apply_baseline as apply_baseline,
    blocking_score as blocking_score,
    category_breakdown as category_breakdown,
    check_tier as check_tier,
    file_deficit as file_deficit,
    fingerprint as fingerprint,
    project_score as project_score,
    write_baseline as write_baseline,
    write_report as write_report,
    write_sarif as write_sarif,
)
from slop_residue import (  # noqa: F401,E402
    check_constant_condition as check_constant_condition,
    check_duplicate_imports as check_duplicate_imports,
    check_generic_names as check_generic_names,
    check_handler_debts as check_handler_debts,
    check_range_len_loop as check_range_len_loop,
    check_residue as check_residue,
    check_tautological_tests as check_tautological_tests,
    check_unreachable as check_unreachable,
)
from slop_scan import (  # noqa: F401,E402
    ScanContext as ScanContext,
    _apply_suppression as _apply_suppression,
    _file_findings as _file_findings,
    _repo_level_findings as _repo_level_findings,
    _scan_one_file as _scan_one_file,
    inventory_findings as inventory_findings,
    provenance_findings as provenance_findings,
    scan as scan,
    scan_code_file as scan_code_file,
)
from slop_security import (  # noqa: F401,E402
    check_dangerously_set_innerhtml as check_dangerously_set_innerhtml,
    check_hardcoded_secret as check_hardcoded_secret,
    check_innerhtml as check_innerhtml,
    check_security_column as check_security_column,
    check_shell_injection as check_shell_injection,
    check_sql_injection as check_sql_injection,
    check_unsafe_c_call as check_unsafe_c_call,
    check_unsafe_deserialization as check_unsafe_deserialization,
)
from slop_similarity import check_near_duplicates as check_near_duplicates  # noqa: E402,F401
from slop_symbol_graph import check_semantic_dead_symbols as check_semantic_dead_symbols  # noqa: E402,F401
from slop_test_redundancy import check_test_redundancy as check_test_redundancy  # noqa: F401,E402
from slop_ts_checks import check_ts_slop as check_ts_slop  # noqa: F401,E402
from slop_type_decls import check_duplicate_type_decls as check_duplicate_type_decls  # noqa: E402,F401
from slop_ui_prose import (  # noqa: F401,E402
    check_doc_extras as check_doc_extras,
    check_ui_slop as check_ui_slop,
    scan_doc_file as scan_doc_file,
)

if __name__ == "__main__":
    sys.exit(main())
