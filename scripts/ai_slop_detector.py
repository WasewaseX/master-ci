#!/usr/bin/env python3
"""Master CI integrity engine: the scan orchestration and library surface.

The check catalog lives in the slop_* modules; the command-line contract
(exit codes, flags, gates) lives in slop_cli.py, re-exported here so tests
and the MCP server keep their import surface.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
from slop_cognitive import check_cognitive  # noqa: F401
from slop_commentary import (  # noqa: F401
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
    density_findings,
    measure,
)
from slop_deps import check_unused_dependencies  # noqa: F401
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
)
from slop_hotspot import (  # noqa: F401
    HOTSPOT_RISK_BUDGET,
    check_hotspot_metrics,
    churn_map,
)
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
    check_residue,
)
from slop_ui_prose import (  # noqa: F401
    check_doc_extras,
    check_ui_slop,
    scan_doc_file,
)

# Orchestration, CLI and the re-exported test surface: the checker
# implementations live in the slop_* sibling modules; this file stays the
# single entry point the workflow invokes and the tests import.


def scan_code_file(
    path: Path,
    ext: str,
    rel: str,
    phrases: dict,
    py_manifests: tuple[set[str], set[str]],
    js_deps: set[str],
) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    family = FAMILY_OF_EXT.get(ext, "")
    findings: list[Finding] = []
    comments = extract_comments(text, family)
    findings.extend(scan_comment_vocabulary(path, comments, phrases))
    findings.extend(check_elided_code(path, text))
    findings.extend(check_placeholder_impl(path, text))
    findings.extend(check_py_ast(path, text, family))
    findings.extend(check_abstraction(path, text, family))
    if family == "py":
        # Reliability's per-file defect checks are AST-backed Python;
        # other families lean on the family scanners' own oracles.
        findings.extend(check_unclosed_resource(path, text))
        findings.extend(check_shared_mutable_state(path, text))
    findings.extend(check_bloat(path, text, family, rel, comments))
    findings.extend(check_cognitive(path, text, ext))
    findings.extend(check_exceptions_js_family(path, text, family))
    findings.extend(check_go(path, text, family, rel))
    findings.extend(check_rust(path, text, family))
    findings.extend(check_debug_left(path, text, family, rel))
    findings.extend(check_js_slop(path, text, family, rel))
    findings.extend(check_jvm_slop(path, text, family, rel))
    findings.extend(check_residue(path, text, family, rel, comments))
    findings.extend(check_ui_slop(path, text, ext))
    findings.extend(check_blanket_suppression(path, text))
    findings.extend(check_placeholder_names(path, text))
    findings.extend(check_placeholder_credential(path, text))
    findings.extend(check_phantom_imports(path, text, family))
    findings.extend(
        check_undeclared_imports(
            path, text, family, rel, DepContext(path.parent, py_manifests, js_deps)
        )
    )
    todos = len(TODO_PATTERN.findall(text))
    code_lines = max(len([line for line in text.splitlines() if line.strip()]), 1)
    if code_lines >= 20 and todos / code_lines > 0.05:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="todo-density",
                message=f"TODO/FIXME density {todos / code_lines:.1%} exceeds 5%",
                weight=3,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Duplication, provenance, test inventory
# ---------------------------------------------------------------------------


def provenance_findings(path: Path, text: str) -> list[Finding]:
    for pattern, label in PROVENANCE_MARKERS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "ai-provenance",
                    f"machine-generation marker: {label}",
                    1,
                )
            ]
    return []


def _classify_source(base: Path, path: Path, globs: list[str], counts: dict) -> None:
    """Bucket one source file into the test inventory counters."""
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        rel = path.as_posix()
    if is_ignored(rel, globs):
        return
    parts = PurePosixPath(rel).parts
    if any("fixture" in part.lower() for part in parts):
        return
    if is_test_path(rel):
        counts["tests"] += 1
    else:
        counts["sources"].append(rel)


def inventory_findings(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    base = root if root.is_dir() else root.parent
    counts: dict = {"tests": 0, "sources": []}
    for path in files:
        if path.suffix.lower() in CODE_EXTS:
            _classify_source(base, path, globs, counts)
    sources: list[str] = counts["sources"]
    if len(sources) >= MIN_SOURCES_FOR_INVENTORY and counts["tests"] == 0:
        return [
            Finding(
                path=sources[0],
                line=1,
                check="missing-tests",
                message=(
                    f"{len(sources)} source files and no test files in the "
                    "repository (test inventory, advisory)"
                ),
                weight=2,
                advisory=True,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Scoring: purity -> per-file deficit -> conservative project score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanContext:
    """Everything one file scan needs, resolved once per run."""

    phrases: dict
    py_manifests: tuple[set[str], set[str]]
    js_deps: set[str]
    provenance: bool


def _scan_one_file(path: Path, suffix: str, rel: str, ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []
    if suffix in DOC_EXTS:
        findings.extend(scan_doc_file(path, suffix, ctx.phrases))
    elif suffix in CODE_EXTS or suffix in UI_EXTS:
        findings.extend(
            scan_code_file(path, suffix, rel, ctx.phrases, ctx.py_manifests, ctx.js_deps)
        )
    else:
        return findings
    if ctx.provenance:
        findings.extend(
            provenance_findings(path, path.read_text(encoding="utf-8", errors="replace"))
        )
    return findings


def _file_findings(
    root: Path,
    files: list[Path],
    globs: list[str],
    ctx: ScanContext,
) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        findings.extend(_scan_one_file(path, path.suffix.lower(), rel, ctx))
    return findings


def _repo_level_findings(
    root: Path,
    files: list[Path],
    globs: list[str],
    provenance: bool,
) -> list[Finding]:
    """Whole-repo views: duplication, dependency and architecture checks."""
    findings: list[Finding] = []
    findings.extend(scan_duplicates(files))
    findings.extend(check_unused_declarations(root, files, globs))
    findings.extend(check_unused_dependencies(root, files, globs))
    findings.extend(check_orphan_modules(root, files, globs))
    findings.extend(check_import_cycles(root, files, globs))
    findings.extend(check_class_cohesion(root, files, globs))
    facts = measure(root, files, globs)
    coupling = measure_coupling(root, files, globs)
    findings.extend(check_coupling_metrics(root, files, globs, facts=coupling))
    if facts is not None:
        # The hotspot shares the one measured corpus and graph: churn
        # reads git once, complexity and dependents arrive pre-computed.
        churn, defects = churn_map(root)
        findings.extend(check_hotspot_metrics(facts, coupling, churn, defects))
        findings.extend(density_findings(facts))
    findings.extend(check_architecture_rules(root, files, globs))
    findings.extend(check_unknown_directives(root, files, globs))
    if provenance:
        findings.extend(inventory_findings(root, files, globs))
    if facts is not None:
        # Reliability grades first; debt prices last, so the ratio
        # prices every other detector's findings, the rating's included.
        findings.extend(check_reliability_rating(facts, findings))
        findings.extend(check_debt_metrics(facts, findings))
    return findings


def _apply_suppression(
    findings: list[Finding],
    disabled: set[str],
    scoped: dict,
) -> list[Finding]:
    if disabled:
        findings = [f for f in findings if f.check.lower() not in disabled]
    if scoped:
        findings = [f for f in findings if not _scoped_out(f, scoped)]
    return findings


def scan(
    root: Path,
    phrases: dict,
    extra_globs: list[str] | None = None,
    provenance: bool = True,
) -> list[Finding]:
    files = iter_files(root)
    globs, disabled, scoped = load_ignore_rules(root)
    globs = globs + list(extra_globs or [])
    base = root if root.is_dir() else root.parent
    ctx = ScanContext(
        phrases,
        collect_py_dependencies(base),
        collect_js_dependencies(base),
        provenance,
    )
    findings = _file_findings(root, files, globs, ctx)
    findings.extend(_repo_level_findings(root, files, globs, provenance))
    return _apply_suppression(findings, disabled, scoped)


# The CLI layer (argument parsing, exit-code contract, gate math) lives in
# slop_cli.py so this module stays inside the same 400-line file budget it
# enforces on consumers. Re-exported for the tests and MCP surface.
from slop_cli import main, parse_args  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
