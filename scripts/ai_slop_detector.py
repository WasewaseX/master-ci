#!/usr/bin/env python3
"""Master CI integrity engine: AI slop, structural slop and provenance.

A linter, not an oracle. It reports deterministic signals that correlate
with machine-written filler and with agent-introduced defects: telltale
vocabulary in comments, emoji padding, elided code, stub functions, empty
tests, swallowed exceptions, phantom and undeclared imports, debug residue,
placeholder credentials, plus - advisory only - prose rhythm patterns in
documents and UI-gradient slop in frontend files.

Design borrowed in spirit from three public slop detectors: flamehaven01's
structural deficit model (purity = exp(-0.5 * blocking weight), per-file
deficit, 0.6*worst + 0.4*mean aggregation, CLEAN/SUSPICIOUS/INFLATED/
CRITICAL verdicts), FixMySlop's rule-with-remedy + baseline + SARIF flow,
and slopdetector.org's explainable, evidence-first prose checks.

Contract:
- Exit code 1 only when the *blocking* score exceeds --max-score.
- Blocking-grade signals are deterministic content defects (elided code,
    stubs, empty tests, swallowed exceptions, chatbot residue in comments).
    Everything stylistic or speculative is advisory: reported and annotated,
    never failing a build.
- Provenance signals (`ai-provenance`) and the test inventory
    (`missing-tests`) are traceability signals, never authorship verdicts;
    advisory. Disable with `--no-provenance`.
- Every finding carries a check id and a remediation hint.
- Repos opt out per path or per check via `.ai-integrity-ignore` (glob
    lines, or `check:<id>` lines), and can accept existing findings via
    `--baseline` / record them with `--write-baseline`.
- `--sarif out.sarif` emits SARIF 2.1.0 for the GitHub Security tab.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# The tests import this file by path (importlib), so the sibling slop_*
# modules may not be importable yet - anchor the script's own directory.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slop_bloat import (  # noqa: F401
    check_bloat,
)
from slop_comments import (  # noqa: F401
    extract_comments,
    phrase_findings,
    scan_comment_vocabulary,
)
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
from slop_imports import (  # noqa: F401
    DepContext,
    check_phantom_imports,
    check_undeclared_imports,
    collect_js_dependencies,
    collect_py_dependencies,
)
from slop_model import (  # noqa: F401
    ADVISORY_ONLY_CHECKS,
    BLOCKING_CHECKS,
    REMEDIES,
    Comment,
    Finding,
    _finding,
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
from slop_reporting import (  # noqa: F401
    advisory_score,
    annotate,
    apply_baseline,
    blocking_score,
    file_deficit,
    fingerprint,
    project_score,
    write_baseline,
    write_report,
    write_sarif,
)
from slop_ui_prose import (  # noqa: F401
    check_doc_extras,
    check_ui_slop,
    scan_doc_file,
)

# ---------------------------------------------------------------------------
# Orchestration, CLI and the re-exported test surface. The checker
# implementations live in the slop_* sibling modules; this file stays the
# single entry point the workflow invokes and the tests import.
# ---------------------------------------------------------------------------


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
    findings.extend(check_bloat(path, text, family, rel, comments))
    findings.extend(check_exceptions_js_family(path, text, family))
    findings.extend(check_go(path, text, family, rel))
    findings.extend(check_rust(path, text, family))
    findings.extend(check_debug_left(path, text, family, rel))
    findings.extend(check_js_slop(path, text, family, rel))
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


def inventory_findings(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    sources: list[str] = []
    tests = 0
    base = root if root.is_dir() else root.parent
    for path in files:
        if path.suffix.lower() not in CODE_EXTS:
            continue
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        parts = PurePosixPath(rel).parts
        if any("fixture" in part.lower() for part in parts):
            continue
        if is_test_path(rel):
            tests += 1
        else:
            sources.append(rel)
    if len(sources) >= MIN_SOURCES_FOR_INVENTORY and tests == 0:
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


def scan(
    root: Path,
    phrases: dict,
    extra_globs: list[str] | None = None,
    provenance: bool = True,
) -> list[Finding]:
    files = iter_files(root)
    globs, disabled, scoped = load_ignore_rules(root)
    globs = globs + list(extra_globs or [])
    py_manifests = collect_py_dependencies(root if root.is_dir() else root.parent)
    js_deps = collect_js_dependencies(root if root.is_dir() else root.parent)
    findings: list[Finding] = []
    for path in files:
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        findings.extend(
            _scan_one_file(
                path,
                path.suffix.lower(),
                rel,
                ScanContext(phrases, py_manifests, js_deps, provenance),
            )
        )
    findings.extend(scan_duplicates(files))
    if provenance:
        findings.extend(inventory_findings(root, files, globs))
    if disabled:
        findings = [f for f in findings if f.check.lower() not in disabled]
    if scoped:
        findings = [f for f in findings if not _scoped_out(f, scoped)]
    return findings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Repo path to scan")
    parser.add_argument(
        "--max-score",
        type=float,
        default=8,
        help="Fail when the blocking score exceeds this value",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip files matching this glob (repeatable, also via .ai-integrity-ignore)",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Write a markdown report to this path",
    )
    parser.add_argument(
        "--sarif",
        default="",
        help="Write SARIF 2.1.0 results to this path (for the Security tab)",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Suppress findings recorded in this baseline file",
    )
    parser.add_argument(
        "--write-baseline",
        default="",
        help="Record current findings as the new baseline and exit 0",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Emit GitHub ::warning annotations on stdout",
    )
    parser.add_argument(
        "--provenance",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Report advisory provenance markers (ai-provenance) and the "
            "repository test inventory (missing-tests); default on"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phrases = load_phrases()
    findings = scan(args.root, phrases, extra_globs=args.ignore, provenance=args.provenance)
    suppressed = 0
    if args.baseline:
        findings, suppressed = apply_baseline(findings, args.baseline)
    if args.write_baseline:
        write_baseline(args.write_baseline, findings)
        print(
            f"ai-integrity baseline written: {len(findings)} fingerprint(s) "
            f"-> {args.write_baseline}"
        )
        return 0
    score, grade, verdict = project_score(
        [file_deficit([f for f in findings if f.path == p]) for p in {f.path for f in findings}]
    )
    if args.report_file:
        write_report(args.report_file, args.root, findings, suppressed=suppressed)
    if args.sarif:
        write_sarif(args.sarif, findings)
    if args.annotate:
        annotate(findings)
    print(
        f"ai-integrity blocking={blocking_score(findings)} max={args.max_score} "
        f"advisory={advisory_score(findings)} findings={len(findings)} "
        f"score={score}/100 grade={grade} verdict={verdict}"
    )
    return 1 if blocking_score(findings) > args.max_score else 0


if __name__ == "__main__":
    sys.exit(main())
