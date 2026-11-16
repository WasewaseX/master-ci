"""JavaScript/TypeScript binding-aware import parsing.

Split from slop_imports.py when v2.18's binding-aware rewrite (audit
finding A) pushed the module past its own 400-line budget. This module
owns the import-shape grammar: ES module clauses (named lists with
multi-line and trailing-comma forms, aliases, type specifiers, default
and namespace clauses), CommonJS const and destructured require, and
TypeScript import-equals.

The conservative contract lives here too: a clause the parser cannot
parse confidently yields None, and a None is never flagged - a false
negative is the quieter failure direction for an advisory check, the
lesson the audit's finding A taught the hard way.
"""

from __future__ import annotations

import re
from pathlib import Path

from slop_model import Finding, _finding

# The pre-v2.18 version deleted the import statement and searched for
# the PACKAGE NAME in the remaining text, so every shape that used the
# imported binding without spelling the package string false-positived
# - 8 of 10 repro cases were wrong, including side-effect imports,
# which bind nothing and are intentional by definition.

# ES module import with a binding clause. The clause is everything
# between `import` and `from`; [^'"] spans newlines for multi-line
# named-import lists. `import type` (TS) carries the same bindings.
_JS_IMPORT_RE = re.compile(
    r"\bimport\s+(?:type\s+)?([^'\"]*?)\s*from\s*[\"']([^\"']+)[\"']", re.DOTALL
)
# Side-effect import - no bindings, never a phantom candidate.
_JS_SIDE_EFFECT_RE = re.compile(r"\bimport\s+[\"']([^\"']+)[\"']")
# CommonJS binding: const x = require("spec") (await tolerated).
_JS_REQUIRE_RE = re.compile(
    r"\b(?:const|let|var)\s+([^=]+?)\s*=\s*(?:await\s+)?"
    r"require\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
)
# TypeScript's import-equals form: import x = require("spec").
_JS_TS_REQUIRE_RE = re.compile(
    r"\bimport\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
)

_IDENTIFIER = r"[A-Za-z_$][\w$]*"
_IDENTIFIER_RE = re.compile(rf"{_IDENTIFIER}")
_DEFAULT_AS_RE = re.compile(rf"({_IDENTIFIER})\s+as\s+({_IDENTIFIER})")
_STRING_AS_RE = re.compile(rf"[\"']([^\"']+)[\"']\s+as\s+({_IDENTIFIER})")
_NAMESPACE_RE = re.compile(rf"\*\s+as\s+({_IDENTIFIER})")


def _named_bindings(inner: str) -> list[str] | None:
    """Bindings of a named-import list, or None when it is not understood.

    A shape this module cannot parse confidently yields None: the import
    is then never flagged (a false negative is a quieter failure than
    the false positive this check just had its named audit fix).
    """
    items = inner.split(",")
    if items and items[-1].strip() == "":
        items = items[:-1]  # a trailing comma is legal JS, not a hole
    out: list[str] = []
    for item in items:
        piece = item.strip()
        if piece.startswith("type "):  # TS inline type specifier
            piece = piece[5:].strip()
        if _IDENTIFIER_RE.fullmatch(piece):
            out.append(piece)
            continue
        default_as = _DEFAULT_AS_RE.fullmatch(piece)
        if default_as:
            out.append(default_as.group(2))
            continue
        string_as = _STRING_AS_RE.fullmatch(piece)
        if string_as:
            out.append(string_as.group(2))
            continue
        return None
    return out or None


def _brace_head_tail(clause: str) -> tuple[list[str], str, bool]:
    """The named list's bindings, the head before it, and a valid tail flag.

    Returns ([], clause, False) when the clause carries no brace - the
    caller then treats the whole clause as the head.
    """
    brace = re.search(r"\{([^}]*)\}", clause, re.DOTALL)
    if brace is None:
        return [], clause, True
    named = _named_bindings(brace.group(1))
    if named is None:
        return [], clause, False
    head = clause[: brace.start()].strip().rstrip(",").strip()
    tail = clause[brace.end() :].strip()
    return named, head, not tail


def _head_piece_binding(piece: str) -> str | None:
    """One head piece's binding, or None when the piece is not understood."""
    if not piece:
        return ""
    namespace = _NAMESPACE_RE.fullmatch(piece)
    if namespace:
        return namespace.group(1)
    if piece.startswith("*"):
        return None
    if _IDENTIFIER_RE.fullmatch(piece):
        return piece
    return None


def _clause_bindings(clause: str) -> list[str] | None:
    """Bound names of one ES import clause, or None when not understood.

    Handles the default binding, the named list, the namespace clause
    and their combinations (`Default, { a as b }`, `Default, * as ns`).
    """
    clause = clause.strip()
    if not clause:
        return None
    if clause.startswith("type "):  # import type { X } from "spec"
        clause = clause[5:].strip()
    bindings: list[str] = []
    named, head, valid_tail = _brace_head_tail(clause)
    if not valid_tail:
        return None
    bindings.extend(named)
    for piece in (p.strip() for p in head.split(",")):
        binding = _head_piece_binding(piece)
        if binding is None:
            return None
        if binding:
            bindings.append(binding)
    return bindings or None


def _require_bindings(pattern: str) -> list[str] | None:
    """Bound names of a require() destructuring pattern, or None."""
    pattern = pattern.strip()
    if pattern.startswith("{"):
        return _named_bindings(pattern.strip("{}"))
    if pattern.startswith("["):
        inner = pattern.strip("[]").strip("{}")
        out: list[str] = []
        for item in inner.split(","):
            piece = item.strip()
            if not piece:
                continue  # array holes are legal
            if _IDENTIFIER_RE.fullmatch(piece):
                out.append(piece)
                continue
            return None
        return out or None
    if _IDENTIFIER_RE.fullmatch(pattern):
        return [pattern]
    return None


def _js_import_matches(text: str) -> list[tuple[re.Match[str], list[str] | None, str]]:
    """Every import/require statement with its bindings and module spec.

    The bindings list is None for side-effect imports (intentionally
    binding-free) and for shapes the parser does not trust (never
    flagged, per the conservative contract above).
    """
    matches: list[tuple[re.Match[str], list[str] | None, str]] = []
    for match in _JS_IMPORT_RE.finditer(text):
        matches.append((match, _clause_bindings(match.group(1)), match.group(2)))
    for match in _JS_SIDE_EFFECT_RE.finditer(text):
        matches.append((match, None, match.group(1)))
    for match in _JS_REQUIRE_RE.finditer(text):
        matches.append((match, _require_bindings(match.group(1)), match.group(2)))
    for match in _JS_TS_REQUIRE_RE.finditer(text):
        matches.append((match, [match.group(1)], match.group(2)))
    return matches


def _js_phantom_imports(path: Path, text: str) -> list[Finding]:
    """Imports whose bindings are never referenced (advisory).

    Binding-aware, per audit finding A: an import is flagged only when
    NONE of the names it binds appears outside the file's import and
    require statements. Relative, absolute and node: specs stay exempt
    (the unused-dependency and dead-declaration checks own those); the
    check remains file-scoped textual analysis - symbol resolution for
    JS/TS is the language-native P2 roadmap row, not this check.
    """
    matches = _js_import_matches(text)
    if not matches:
        return []
    body = text
    for match, _bindings, _spec in matches:
        body = body.replace(match.group(0), "")
    findings: list[Finding] = []
    seen_specs: set[str] = set()
    for match, bindings, spec in matches:
        if bindings is None or spec.startswith((".", "/", "node:")) or spec in seen_specs:
            continue
        seen_specs.add(spec)
        if any(re.search(rf"\b{re.escape(b)}\b", body) for b in bindings):
            continue
        names = ", ".join(bindings[:4]) + ("..." if len(bindings) > 4 else "")
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "phantom-import",
                f"import {spec!r} is never used: no binding ({names}) "
                "appears outside the import statements",
                1,
            )
        )
    return findings[:5]
