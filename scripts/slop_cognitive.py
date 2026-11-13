"""Sonar-style cognitive complexity, measured on real syntax trees.

Cyclomatic complexity counts paths; cognitive complexity counts the
effort a reader spends: control structures cost 1 plus their nesting
depth, branch labels (else, case arms) cost 1, every new run of the
same logical operator costs 1, direct recursion costs 1, and a nested
function resets the nesting counter - a reader starts over inside a
closure. The tree comes from tree-sitter (one mature parser, the same
facts for every language); when no tree is available the check reports
nothing rather than guessing from line shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from slop_cognitive_langs import (
    GRAMMARS,
    NAMED_OP_TOKENS,
    OP_KINDS,
    TS_LANGUAGE_OF_EXT,
    Grammar,
)
from slop_model import Finding

COGNITIVE_BUDGET = 15  # Sonar's own "moderately complex" threshold

try:  # the parser is optional; the scanner stays importable without it
    from tree_sitter_language_pack import get_parser  # type: ignore[import-untyped]

    _TS_OK = True
except ImportError:  # pragma: no cover - exercised via the flag, not the import
    get_parser = None  # type: ignore[assignment]
    _TS_OK = False


@dataclass(frozen=True)
class CognitiveFact:
    """One measured function-like root."""

    name: str
    line: int  # 1-based start line
    score: int


def tree_sitter_available() -> bool:
    """True when the tree-sitter language pack is importable.

    Same contract as lizard_available(): a caller that enforces a
    cognitive ceiling must demand the parser instead of accepting
    silent "no facts".
    """
    return _TS_OK


def _op_kind(node) -> str | None:
    """The && / || / and / or sequence kind of one operator node."""
    for child in node.children:
        text = child.text.decode("utf-8", errors="replace") if child.text else ""
        if not child.is_named:
            kind = OP_KINDS.get(text)
            if kind:
                return kind
        elif child.type in NAMED_OP_TOKENS:
            kind = OP_KINDS.get(text) or OP_KINDS.get(child.type)
            if kind:
                return kind
    return None


_ELIF_TYPES = frozenset({"elif_clause", "else_if_clause"})
_IF_TYPES = frozenset({"if_statement", "if_expression"})


def _is_else_label(child) -> bool:
    """The else keyword in any shape: anonymous token or bare named node.

    Swift marks the keyword as a named childless node typed "else";
    Ruby's named else clause carries the branch body, so it is a real
    label scored by the grammar table, never this check.
    """
    if not child.is_named:
        return child.text == b"else"
    return child.type == "else" and not any(c.is_named for c in child.children)


def _flat_or_op_bumps(node, grammar: Grammar, ops: list[str]) -> int:
    """Bumps for non-nesting nodes: flat labels cost 1, operators collect."""
    if node.type in grammar.flat:
        return 1
    if node.type in grammar.ops:
        op = _op_kind(node)
        if op:
            ops.append(op)
    return 0


def _else_bumps(node) -> int:
    """+1 for a plain `else` label; none when it opens an else-if chain."""
    children = node.children
    for i, child in enumerate(children):
        if _is_else_label(child):
            nxt = next((x for x in children[i + 1 :] if x.is_named), None)
            if nxt is not None and nxt.type in _IF_TYPES:
                return 0  # the chained if scores instead; the label is free
            return 1
    return 0


def _score(node, grammar: Grammar, nesting: int, ops: list[str]) -> int:
    """Cognitive bumps for one subtree; nested function roots are skipped.

    Anonymous tokens never score: in Ruby the keyword `if` is an
    anonymous token whose type is the literal text "if", identical to
    the named `if` node - only named nodes carry structure. An elif or
    `else if` chain continues the enclosing if at its own nesting
    level, the way a reader tracks it.
    """
    if not node.is_named:
        return 0
    kind = node.type
    if kind in grammar.funcs:
        return 0  # a reader restarts inside a closure; it is scored alone
    total = 0
    inner = nesting
    if kind in grammar.nest:
        total += 1 + nesting + _else_bumps(node)
        inner = nesting + 1
    else:
        total += _flat_or_op_bumps(node, grammar, ops)
    prev_else = False
    for child in node.children:
        child_nesting = inner
        if child.is_named and (
            child.type in _ELIF_TYPES or (prev_else and child.type in _IF_TYPES)
        ):
            child_nesting = nesting  # the chain continues at the if's level
        total += _score(child, grammar, child_nesting, ops)
        prev_else = _is_else_label(child)
    return total


def _sequence_bumps(ops: list[str]) -> int:
    """One bump per run of the same logical operator."""
    if not ops:
        return 0
    return 1 + sum(1 for a, b in zip(ops, ops[1:]) if a != b)


def _recursion_bump(root) -> int:
    """+1 when the function calls itself by its own simple name."""
    name_node = root.child_by_field_name("name")
    body = root.child_by_field_name("body")
    if name_node is None or body is None or not name_node.text:
        return 0
    name = re.escape(name_node.text.decode("utf-8", errors="replace"))
    body_text = body.text.decode("utf-8", errors="replace")
    if re.search(rf"(?<![\w.]){name}\s*\(", body_text):
        return 1
    return 0


def _func_roots(root, funcs: frozenset[str]) -> list:
    roots = []
    if root.type in funcs:
        roots.append(root)
    for child in root.children:
        roots.extend(_func_roots(child, funcs))
    return roots


def cognitive_facts(ext: str, text: str) -> list[CognitiveFact] | None:
    """Measure every function-like root in one file.

    Returns None when no tree is available (parser missing, extension
    outside the grammar table, source the parser rejects outright) -
    the caller reports nothing rather than guessing.
    """
    if not _TS_OK:
        return None
    lang = TS_LANGUAGE_OF_EXT.get(ext)
    if lang is None or lang not in GRAMMARS:
        return None
    try:
        tree = get_parser(lang).parse(text.encode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - any parser failure just means "no tree"
        return None
    if tree.root_node.has_error:
        return None
    grammar = GRAMMARS[lang]
    facts: list[CognitiveFact] = []
    for root in _func_roots(tree.root_node, grammar.funcs):
        ops: list[str] = []
        score = 0
        for child in root.children:
            score += _score(child, grammar, 0, ops)
        # operator runs are collected during the walk; add their bumps now
        score += _sequence_bumps(ops) + _recursion_bump(root)
        name_node = root.child_by_field_name("name")
        name = (
            name_node.text.decode("utf-8", errors="replace")
            if name_node is not None and name_node.text
            else "(anonymous)"
        )
        facts.append(CognitiveFact(name=name, line=root.start_point[0] + 1, score=score))
    return facts


def check_cognitive(path: Path, text: str, ext: str) -> list[Finding]:
    """Advisory findings for functions over the cognitive budget."""
    facts = cognitive_facts(ext, text)
    if facts is None:
        return []
    return [
        Finding(
            path=str(path),
            line=fact.line,
            check="high-cognitive",
            message=(
                f"function {fact.name!r} scores {fact.score} cognitive "
                f"(budget {COGNITIVE_BUDGET}) - nesting and branching "
                "beyond what a reader tracks"
            ),
            weight=1,
            advisory=True,
        )
        for fact in facts
        if fact.score > COGNITIVE_BUDGET
    ]
