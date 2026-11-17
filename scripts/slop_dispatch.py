"""Dispatch-shape detection: if/elif chains and guard-if runs.

One subject tested four-plus times is a dispatch table written the hard
way - scanaislop's ``repetitive-dispatch`` pattern. Two code shapes
dispatch: the if/elif chain, and the run of consecutive guard ``if``
statements that each end their branch. The guard-run shape shipped in
flaky_parsers.py and the elif-only walk missed it (scanaislop claim
triage, 2026-11-18); both shapes now live here under one budget.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from slop_model import Finding

LADDER_MIN = 4  # branches before a hand-rolled dispatch table is flagged


def _isinstance_subject(test: ast.expr) -> str | None:
    """Subject key when the test is isinstance(name, ...), else None."""
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and test.args
        and isinstance(test.args[0], ast.Name)
    ):
        return f"isinstance:{test.args[0].id}"
    return None


def _eq_string_subject(test: ast.expr) -> str | None:
    """Subject key when the test is name == 'literal', else None."""
    if (
        isinstance(test, ast.Compare)
        and test.ops
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Name)
        and test.comparators
        and isinstance(test.comparators[0], ast.Constant)
        and isinstance(test.comparators[0].value, str)
    ):
        return f"eq:{test.left.id}"
    return None


def _subject_of(test: ast.expr) -> str | None:
    """The dispatch subject one branch test compares on, or None."""
    return _isinstance_subject(test) or _eq_string_subject(test)


def _chain_subjects(root: ast.If) -> dict[str, int]:
    """Count branch tests per subject across one if/elif chain."""
    subjects: dict[str, int] = {}
    node: ast.If | None = root
    while node is not None:
        subject = _subject_of(node.test)
        if subject:
            subjects[subject] = subjects.get(subject, 0) + 1
        nxt = (
            node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
        )
        node = nxt
    return subjects


def _dispatch_finding(path: Path, line: int, subject: str, count: int) -> Finding:
    """One verdict; the check id follows the subject's kind."""
    return Finding(
        path=str(path),
        line=line,
        check=("isinstance-ladder" if subject.startswith("isinstance:") else "repetitive-dispatch"),
        message=f"{count} branches on one subject - use a dispatch table",
        weight=1,
        advisory=True,
    )


def _chain_members(tree: ast.AST) -> set[int]:
    """The ids of every elif-position node, which the chain root reports."""
    members: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and len(node.orelse) == 1
            and isinstance(node.orelse[0], ast.If)
        ):
            members.add(id(node.orelse[0]))
    return members


def _chain_findings(path: Path, tree: ast.AST) -> list[Finding]:
    """Verdicts for if/elif chains that dispatch on one subject."""
    members = _chain_members(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in members:
            continue
        for subject, count in _chain_subjects(node).items():
            if count >= LADDER_MIN:
                findings.append(_dispatch_finding(path, node.lineno, subject, count))
    return findings


def _branch_ends_flow(branch: ast.If) -> bool:
    """True when the branch's last statement leaves the block.

    Dispatch means control exits through every branch: return, raise,
    continue or break. Guards that do not exit (``if opts.verbose: ...``
    twice on the same subject) are independent conditional features, not
    a dispatch table, and stay unflagged for precision.
    """
    return bool(branch.body) and isinstance(
        branch.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)
    )


def _statement_lists(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    """Every sibling statement list the AST holds, once per owner node."""
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            stmts = getattr(node, attr, None)
            if isinstance(stmts, list) and stmts and all(isinstance(s, ast.stmt) for s in stmts):
                yield stmts


def _guard_member(stmt: ast.stmt) -> tuple[int, str] | None:
    """(line, subject) when the statement is a dispatch-shaped guard-if."""
    if not isinstance(stmt, ast.If) or stmt.orelse or not _branch_ends_flow(stmt):
        return None
    subject = _subject_of(stmt.test)
    return (stmt.lineno, subject) if subject else None


def _flush(runs: list[tuple[int, str, int]], run: list[tuple[int, str]]) -> None:
    """Record the run when it reached the dispatch-table budget."""
    if len(run) >= LADDER_MIN:
        runs.append((run[0][0], run[0][1], len(run)))


def _guard_runs(stmts: list[ast.stmt]) -> list[tuple[int, str, int]]:
    """(start line, subject, count) per guard run inside one sibling list.

    A run is consecutive dispatch-shaped guard-ifs on the same subject;
    the chain detector cannot see them - separate statements, no elif -
    but they dispatch identically, so the same LADDER_MIN budget applies.
    """
    runs: list[tuple[int, str, int]] = []
    run: list[tuple[int, str]] = []
    for stmt in stmts:
        member = _guard_member(stmt)
        if member is None:
            _flush(runs, run)
            run = []
            continue
        if run and run[-1][1] != member[1]:
            _flush(runs, run)
            run = []
        run.append(member)
    _flush(runs, run)
    return runs


def _guard_findings(path: Path, tree: ast.AST) -> list[Finding]:
    """Verdicts for runs of consecutive flow-exiting guard-ifs."""
    findings: list[Finding] = []
    for stmts in _statement_lists(tree):
        for line, subject, count in _guard_runs(stmts):
            findings.append(_dispatch_finding(path, line, subject, count))
    return findings


def check_dispatch_ladders(path: Path, tree: ast.AST) -> list[Finding]:
    """Four-plus isinstance or ==-string branches testing the same subject."""
    findings = _chain_findings(path, tree)
    findings.extend(_guard_findings(path, tree))
    return findings
