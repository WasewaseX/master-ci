"""TypeScript precision checks: coercion repeats and the hidden fallback.

Adopted from the scanaislop TS column; the cast/directive rules were
already covered by the combined ``ts-suppression`` check, so this module
holds the two oracles that check semantics rather than syntax:

- redundant-type-coercion: String()/Number()/Boolean() applied to a
    parameter the signature already types as that primitive. The oracle
    is signature-scan based, not a TypeScript checker: a coercion is
    flagged only within 30 lines after the signature that declares the
    typed parameter, and every finding is advisory.
- hidden-fallback: a catch block that returns or assigns a safe-looking
    default (0, [], {}, null, { ok: true }) - a loud error becomes a
    quiet wrong answer. JS family only; the oracle cannot model control
    flow, so legitimate default-structs outside a failed call are out
    of scope.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from slop_model import _finding

if TYPE_CHECKING:
    from slop_model import Finding
from slop_patterns import _strip_strings

TS_ONLY_EXTS = {".ts", ".tsx", ".mts", ".cts"}

CS_PARAM = re.compile(r"[(,]\s*([A-Za-z_]\w*)\s*:\s*(string|number|boolean)\b")
PRIMITIVE_COERCION = {
    "string": re.compile(r"\bString\s*\(\s*(\w+)\s*\)"),
    "number": re.compile(r"\bNumber\s*\(\s*(\w+)\s*\)"),
    "boolean": re.compile(r"\bBoolean\s*\(\s*(\w+)\s*\)"),
}
COERCION_WINDOW = 30  # lines a typed parameter's coercion may follow

CATCH_BLOCK = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
SAFE_DEFAULT_RETURN = re.compile(
    r"return\s+(?:0|\"\"|''|\[\]|\{\s*\}|null|undefined|true|false"
    r"|\{\s*ok\s*:\s*true\s*\})\s*;?"
)
SAFE_DEFAULT_ASSIGN = re.compile(
    r"\b(?:\w+)\s*=\s*(?:0|\"\"|''|\[\]|\{\s*\}|null|undefined|false)\s*;"
)

MAX_FINDINGS_PER_CHECK = 5


def _drop_comments(text: str) -> str:
    """Comment text blanked, newlines kept - line numbers stay true."""
    return re.sub(
        r"//[^\n]*",
        lambda m: " " * len(m.group(0)),
        re.sub(
            r"/\*[\s\S]*?\*/",
            lambda m: re.sub(r"[^\n]", " ", m.group(0)),
            text,
        ),
    )


def check_ts_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """The TS-precision column: advisory findings, zero dependencies."""
    if family != "js":
        return []
    findings: list[Finding] = []
    if path.suffix.lower() in TS_ONLY_EXTS:
        findings.extend(_redundant_type_coercion(path, text))
    findings.extend(_hidden_fallback(path, text))
    return findings


def _typed_params(lines: list[str]):
    """(1-based signature line, param name, primitive) per typed parameter."""
    for lineno, line in enumerate(lines, start=1):
        for m in CS_PARAM.finditer(line):
            yield lineno, m.group(1), m.group(2)


def _coercions_for_param(
    path: Path,
    code: str,
    matches: list[tuple[int, str]],
    param: str,
    lineno: int,
    seen: set,
) -> list[Finding]:
    """Coercions of one typed parameter inside the signature's window."""
    out: list[Finding] = []
    for pos, name in matches:
        if name != param:
            continue
        coerced_at = code[:pos].count("\n") + 1
        if not 0 <= coerced_at - lineno <= COERCION_WINDOW:
            continue
        key = (coerced_at, param)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            _finding(
                path,
                code,
                pos,
                "redundant-type-coercion",
                f"String/Number/Boolean conversion repeats what the "
                f"signature guarantees ({param}) - the type system already "
                "guarantees it",
                1,
            )
        )
    return out


def _redundant_type_coercion(path: Path, text: str) -> list[Finding]:
    code = _strip_strings(text)
    coercions = {
        primitive: [(m.start(), m.group(1)) for m in rx.finditer(code)]
        for primitive, rx in PRIMITIVE_COERCION.items()
    }
    findings: list[Finding] = []
    seen: set = set()
    for lineno, param, primitive in _typed_params(code.splitlines()):
        findings.extend(_coercions_for_param(path, code, coercions[primitive], param, lineno, seen))
    return findings[:MAX_FINDINGS_PER_CHECK]


def _catch_body_span(code: str, open_brace: int) -> tuple[int, int]:
    depth = 0
    for idx in range(open_brace, min(open_brace + 2000, len(code))):
        if code[idx] == "{":
            depth += 1
        elif code[idx] == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, idx
    return open_brace, open_brace


def _hidden_fallback(path: Path, text: str) -> list[Finding]:
    """Comment-free original text: the quoted empty-string default is part
    of the signal, so strings stay and catch-scoping bounds the search."""
    code = _drop_comments(text)
    findings: list[Finding] = []
    for m in CATCH_BLOCK.finditer(code):
        start, end = _catch_body_span(code, m.end() - 1)
        body = code[start:end]
        hit = SAFE_DEFAULT_RETURN.search(body)
        if not hit and SAFE_DEFAULT_ASSIGN.fullmatch(body.strip()):
            hit = SAFE_DEFAULT_ASSIGN.search(body)
        if hit:
            findings.append(
                _finding(
                    path,
                    code,
                    m.start(),
                    "hidden-fallback",
                    "catch block answers a failed call with a safe-looking "
                    "default - the loud error becomes a quiet wrong answer",
                    1,
                )
            )
    return findings[:MAX_FINDINGS_PER_CHECK]
