"""The scan pipeline: file classification, repo-level verdicts and scan().

The orchestration half of the old ai_slop_detector, split out when the
v2.18 round grew the file past its own 400-line budget - the detector's
structural law applies to its own source first. This module owns the
pipeline; ai_slop_detector stays the single entry point that re-exports
the whole surface the tests and workflows import.

scan() is the one full scan: per-file findings through ScanContext, the
repo-level views over one measured corpus and graph, then suppression.
`budgets` carries an analyzer profile's overrides; `metrics_out`, when
a dict, receives the measured bundle for the structured metrics
document - passing it changes no findings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from slop_abstraction import check_abstraction
from slop_arch import ArchConfigError, check_architecture_rules  # noqa: F401
from slop_bloat import check_bloat
from slop_cognitive import check_cognitive
from slop_commentary import check_unknown_directives
from slop_comments import extract_comments, scan_comment_vocabulary
from slop_corpus import measure_corpus
from slop_coupling import check_coupling_metrics, measure_coupling
from slop_cpp import check_cpp_slop
from slop_csharp import check_csharp_slop
from slop_cycles import check_import_cycles
from slop_dead import check_orphan_modules, check_unused_declarations
from slop_debt import check_debt_metrics
from slop_density import check_analysis_coverage, density_findings
from slop_deps import check_unused_dependencies
from slop_duplicates import MIN_SOURCES_FOR_INVENTORY, PROVENANCE_MARKERS, scan_duplicates
from slop_family import (
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
from slop_files import (
    _scoped_out,
    is_ignored,
    is_test_path,
    iter_files,
    load_ignore_rules,
    read_text_lax,
)
from slop_hotspot import check_hotspot_metrics, churn_map
from slop_imports import (
    DepContext,
    check_phantom_imports,
    check_undeclared_imports,
    collect_js_dependencies,
    collect_py_dependencies,
)
from slop_lcom4 import check_class_cohesion
from slop_model import Finding, _finding
from slop_patterns import CODE_EXTS, DOC_EXTS, FAMILY_OF_EXT, TODO_PATTERN, UI_EXTS
from slop_py_checks import check_elided_code, check_placeholder_impl, check_py_ast
from slop_reliability import (
    check_reliability_rating,
    check_shared_mutable_state,
    check_unclosed_resource,
)
from slop_residue import check_residue
from slop_test_redundancy import check_test_redundancy
from slop_ts_checks import check_ts_slop
from slop_type_decls import check_duplicate_type_decls
from slop_ui_prose import check_ui_slop, scan_doc_file


def scan_code_file(
    path: Path,
    ext: str,
    rel: str,
    phrases: dict,
    py_manifests: tuple[set[str], set[str]],
    js_deps: set[str],
) -> list[Finding]:
    text = read_text_lax(path)
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
        # Test redundancy scopes itself to test paths via is_test_path;
        # production modules return empty before the AST is parsed.
        findings.extend(check_test_redundancy(path, text, rel))
    findings.extend(check_bloat(path, text, family, rel, comments))
    findings.extend(check_cognitive(path, text, ext))
    findings.extend(check_exceptions_js_family(path, text, family))
    findings.extend(check_go(path, text, family, rel))
    findings.extend(check_rust(path, text, family))
    findings.extend(check_debug_left(path, text, family, rel))
    findings.extend(check_js_slop(path, text, family, rel))
    findings.extend(check_ts_slop(path, text, family, rel))
    findings.extend(check_jvm_slop(path, text, family, rel))
    findings.extend(check_csharp_slop(path, text, family, rel))
    findings.extend(check_cpp_slop(path, text, family))
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


# ---- the pipeline, lifted from ai_slop_detector (v2.18 budget split) ----


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
        findings.extend(provenance_findings(path, read_text_lax(path)))
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
    budgets: Mapping[str, float] | None = None,
    metrics_out: dict | None = None,
) -> list[Finding]:
    """Whole-repo views: duplication, dependency and architecture checks.

    `budgets` carries an analyzer profile's budget overrides (v2.17) so
    the density verdicts judge against the profile's lines, not the
    shipped constants; None keeps the shipped budgets byte-identical.
    v2.18 (audit finding B): the coupling and hotspot verdicts take the
    same overrides - every budget a profile accepts is functional.
    v2.18 (audit finding E): the corpus's coverage states ride with the
    facts and speak through the analysis-coverage check - a parse
    failure or an unsupported language can no longer vanish into an
    apparently clean ratio. `metrics_out`, when a dict, receives the
    measured bundle (density facts, coverage, coupling, churn) for the
    structured metrics document; it never changes the findings.
    """
    findings: list[Finding] = []
    findings.extend(scan_duplicates(files, root, globs))
    findings.extend(check_duplicate_type_decls(root, files, globs))
    findings.extend(check_unused_declarations(root, files, globs))
    findings.extend(check_unused_dependencies(root, files, globs))
    findings.extend(check_orphan_modules(root, files, globs))
    findings.extend(check_import_cycles(root, files, globs))
    findings.extend(check_class_cohesion(root, files, globs))
    facts, coverage = measure_corpus(root, files, globs)
    findings.extend(check_analysis_coverage(coverage))
    coupling = measure_coupling(root, files, globs)
    findings.extend(check_coupling_metrics(root, files, globs, facts=coupling, budgets=budgets))
    churn: dict[str, int] = {}
    defects: dict[str, int] = {}
    if facts is not None:
        # The hotspot shares the one measured corpus and graph: churn
        # reads git once, complexity and dependents arrive pre-computed.
        churn, defects = churn_map(root)
        findings.extend(check_hotspot_metrics(facts, coupling, churn, defects, budgets=budgets))
        findings.extend(density_findings(facts, budgets=budgets))
    if metrics_out is not None:
        metrics_out["density"] = facts
        metrics_out["coverage"] = coverage
        metrics_out["coupling"] = coupling
        metrics_out["churn"] = churn
        metrics_out["defects"] = defects
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
    budgets: Mapping[str, float] | None = None,
    metrics_out: dict | None = None,
) -> list[Finding]:
    """One full scan; `budgets` is the analyzer profile's overrides.

    `metrics_out`, when a dict, is filled with the measured bundle the
    structured metrics document (audit finding G) is rendered from:
    density facts, corpus coverage, coupling facts, churn and defects.
    Passing it changes no findings.
    """
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
    findings.extend(_repo_level_findings(root, files, globs, provenance, budgets, metrics_out))
    return _apply_suppression(findings, disabled, scoped)
