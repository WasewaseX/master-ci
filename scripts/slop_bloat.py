"""The anti-bloatware budget checks (file length, params, comment pressure, banners).

Function-level facts come from the unified lizard parse (slop_lizard);
this module turns them into findings, judges file-level budgets, and
falls back to the line-scanner block view in slop_structural where no
parse exists. Nesting is always judged from the line scanner - it is
the one function fact the parser does not carry.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from slop_files import is_test_path
from slop_lizard import COMPLEXITY_BUDGET, FunctionFact, parse_functions
from slop_model import Comment, Finding
from slop_structural import (
    BANNER_DECO,
    BLOAT_BANNER_GAP,
    BLOAT_BANNER_MIN,
    BLOAT_COMMENT_MIN_LINES,
    BLOAT_COMMENT_RATIO,
    BLOAT_FILE_LINES,
    BLOAT_MAX_PARAMS,
    GOD_FUNCTION_LINES,
    MAX_NESTING,
    _family_blocks,
    _param_count,
)


def _is_banner(comment: Comment) -> bool:
    core = re.sub(r"^(?:#|//|/\*|\*)+\s*", "", comment.text.strip())
    core = core.rstrip("*/").strip()
    if not core:
        return False
    return bool(BANNER_DECO.match(core))


def _py_docstring_lines(text: str) -> set[int]:
    """Real docstring spans from the AST, not the string-guessing scanner.

    The py comment extractor treats every triple-quote as a docstring,
    which makes multi-line string literals (test fixtures, embedded
    YAML) count as comments and can push a fixture-heavy test file over
    the comment-pressure budget. The AST names the real docstrings.
    """
    lines: set[int] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(
                range(first.value.lineno, (first.value.end_lineno or first.value.lineno) + 1)
            )
    return lines


def _comment_lines_of(family: str, comments: list[Comment], text: str) -> set[int]:
    """Comment line numbers per family; py docstrings come from the AST."""
    lines = {c.line for c in comments if family != "py" or c.kind != "docstring"}
    if family == "py":
        lines |= _py_docstring_lines(text)
    return lines


def _comment_pressure_finding(
    path: Path,
    comment_lines: set[int],
    non_blank: int,
) -> Finding | None:
    """Flag files where comments outweigh code (AI padding tell)."""
    if non_blank < BLOAT_COMMENT_MIN_LINES:
        return None
    ratio = len(comment_lines) / non_blank
    if ratio <= BLOAT_COMMENT_RATIO:
        return None
    return Finding(
        path=str(path),
        line=1,
        check="bloat-comment-ratio",
        message=(
            f"{len(comment_lines)}/{non_blank} lines are comments "
            f"({ratio:.0%} - budget {BLOAT_COMMENT_RATIO:.0%})"
        ),
        weight=1,
        advisory=True,
    )


def _banner_cluster_starts(comments: list[Comment]) -> list[int]:
    """First line of each banner cluster (a titled block is one unit)."""
    starts: list[int] = []
    prev = 0
    for c in comments:
        if not _is_banner(c):
            continue
        if not starts or c.line - prev > 2:
            starts.append(c.line)
        prev = c.line
    return starts


def _banner_padding_finding(
    path: Path,
    comments: list[Comment],
    non_blank: int,
) -> Finding | None:
    """Flag bunched banner blocks; spread-out dividers are navigation."""
    clusters = _banner_cluster_starts(comments)
    if len(clusters) < BLOAT_BANNER_MIN:
        return None
    gaps = sorted(b - a for a, b in zip(clusters, clusters[1:]))
    median_gap = gaps[len(gaps) // 2] if gaps else non_blank
    if median_gap >= BLOAT_BANNER_GAP:
        return None
    return Finding(
        path=str(path),
        line=clusters[0],
        check="bloat-banner-comments",
        message=(
            f"{len(clusters)} decorative banner blocks pad the file; "
            "structure should carry the sections"
        ),
        weight=1,
        advisory=True,
    )


def _file_bloat_findings(
    path: Path,
    text: str,
    family: str,
    comments: list[Comment],
    rel: str,
) -> list[Finding]:
    """File-level budgets: length, comment pressure, banner padding."""
    findings: list[Finding] = []
    non_blank = sum(1 for line in text.splitlines() if line.strip())
    if non_blank > BLOAT_FILE_LINES and not is_test_path(rel):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="bloat-long-file",
                message=f"file spans {non_blank} non-blank lines (budget {BLOAT_FILE_LINES})",
                weight=1,
                advisory=True,
            )
        )
    pressure = _comment_pressure_finding(path, _comment_lines_of(family, comments, text), non_blank)
    if pressure:
        findings.append(pressure)
    padding = _banner_padding_finding(path, comments, non_blank)
    if padding:
        findings.append(padding)
    return findings


def _length_param_findings(path: Path, blocks: list[dict]) -> list[Finding]:
    """Line-scanner budgets: block length and signature parameter count.

    Reporting only the worst function hid the second, third and fourth
    offender; every function past a budget gets its own line so a PR
    review sees the full workload.
    """
    findings: list[Finding] = []
    for block in sorted(blocks, key=lambda b: b["header"]):
        if block["length"] > GOD_FUNCTION_LINES:
            findings.append(
                Finding(
                    path=str(path),
                    line=block["header"] + 1,
                    check="god-function",
                    message=f"function block spans {block['length']} lines",
                    weight=1,
                    advisory=True,
                )
            )
        params = _param_count(block["header_text"])
        if params > BLOAT_MAX_PARAMS:
            findings.append(
                Finding(
                    path=str(path),
                    line=block["header"] + 1,
                    check="bloat-param-count",
                    message=f"{params} parameters (budget {BLOAT_MAX_PARAMS})",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def _nesting_findings(path: Path, blocks: list[dict]) -> list[Finding]:
    """Control-nesting budget, shared by the parse and scanner paths."""
    findings: list[Finding] = []
    for block in sorted(blocks, key=lambda b: b["header"]):
        if block["depth"] > MAX_NESTING:
            findings.append(
                Finding(
                    path=str(path),
                    line=block["header"] + 1,
                    check="deep-nesting",
                    message=f"nests more than {MAX_NESTING} levels",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def _block_findings(path: Path, blocks: list[dict]) -> list[Finding]:
    """Every scanner-path budget for one file's function blocks."""
    return _length_param_findings(path, blocks) + _nesting_findings(path, blocks)


def _fact_findings(
    path: Path,
    facts: list[FunctionFact],
    kinds: tuple[str, ...],
) -> list[Finding]:
    """Parse-backed budgets: honest length, real parameter count, complexity.

    nloc counts non-comment, non-blank lines, so comment padding cannot
    pass a length budget and blank lines cannot convict dense code.
    `kinds` selects the budgets: Python keeps AST-backed length/nesting/
    parameter checks and takes only the complexity budget from the parse.
    """
    findings: list[Finding] = []
    for fact in facts:
        findings.extend(_one_fact_findings(path, fact, kinds))
    return findings


def _one_fact_findings(
    path: Path,
    fact: FunctionFact,
    kinds: tuple[str, ...],
) -> list[Finding]:
    """The budgets one parsed function trips, in report order."""
    budget = {
        "god-function": (fact.nloc, GOD_FUNCTION_LINES),
        "bloat-param-count": (fact.params, BLOAT_MAX_PARAMS),
        "high-cyclomatic": (fact.ccn, COMPLEXITY_BUDGET),
    }
    messages = {
        "god-function": (
            f"function {fact.name!r} uses {fact.nloc} "
            f"non-comment lines (budget {GOD_FUNCTION_LINES})"
        ),
        "bloat-param-count": f"{fact.params} parameters (budget {BLOAT_MAX_PARAMS})",
        "high-cyclomatic": (
            f"cyclomatic complexity {fact.ccn} (budget {COMPLEXITY_BUDGET})"
            " - split along its decision axes"
        ),
    }
    return [
        Finding(
            path=str(path),
            line=fact.line,
            check=check,
            message=messages[check],
            weight=1,
            advisory=True,
        )
        for check in ("god-function", "bloat-param-count", "high-cyclomatic")
        if check in kinds and budget[check][0] > budget[check][1]
    ]


def check_bloat(
    path: Path,
    text: str,
    family: str,
    rel: str,
    comments: list[Comment],
) -> list[Finding]:
    """File-level and function-level bloat budget checks (all advisory).

    Function-level budgets run on the unified lizard parse wherever a
    parse exists; the line scanner takes over where it does not. Nesting
    is judged from the line scanner either way: it is the one function
    fact the parser does not carry.
    """
    findings = _file_bloat_findings(path, text, family, comments, rel)
    facts = parse_functions(str(path), text)
    if family == "py":
        # Python's length, nesting and parameter budgets are AST-backed in
        # slop_py_checks; the parse adds only the complexity budget.
        if facts is not None:
            findings.extend(_fact_findings(path, facts, kinds=("high-cyclomatic",)))
        return findings
    if facts is not None:
        findings.extend(
            _fact_findings(
                path,
                facts,
                kinds=("god-function", "bloat-param-count", "high-cyclomatic"),
            )
        )
        findings.extend(_nesting_findings(path, _family_blocks(text, family)))
        return findings
    blocks = _family_blocks(text, family)
    if blocks:
        findings.extend(_block_findings(path, blocks))
    return findings
