"""Repo-level duplicate-type-declaration: one shape, two sources of truth.

The scanaislop rule reversed: the same exported TypeScript ``interface``
or ``type`` alias, with the same name and body, declared in two files.
Two sources of truth drift apart silently, so the finding names every
file holding the duplicate. The oracle is textual and normalized (bodies
compared after comment, string and whitespace removal) - it cannot see
structural equality the way a type checker would, so a same-name-different
body pair is deliberately not flagged and every finding stays advisory.
"""

from __future__ import annotations

import re
from pathlib import Path

from slop_files import is_ignored, read_text_lax
from slop_model import Finding

TS_TYPE_EXTS = {".ts", ".tsx", ".mts", ".cts"}
EXPORTED_TYPE = re.compile(
    r"\bexport\s+(?:interface\s+(\w+)\s*\{|type\s+(\w+)\s*=\s*)"
)
NORMALIZE_WS = re.compile(r"\s+")
STRIP_COMMENT = re.compile(r"//.*$|/\*.*?\*/", re.MULTILINE | re.DOTALL)

MAX_FINDINGS = 10


def _decl_body(text: str, start: int) -> str:
    """The declaration's text up to the balanced end of its first brace."""
    brace = text.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    for idx in range(brace, min(brace + 4000, len(text))):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return ""


def _normalized(decl: str) -> str:
    stripped = STRIP_COMMENT.sub(" ", decl)
    return NORMALIZE_WS.sub(" ", stripped).strip()


def _type_groups(
    root: Path, files: list[Path], globs: list[str]
) -> dict[tuple[str, str], list[str]]:
    """(name, normalized shape) -> the relative paths holding that shape."""
    groups: dict[tuple[str, str], list[str]] = {}
    for path in files:
        if path.suffix.lower() not in TS_TYPE_EXTS:
            continue
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        if is_ignored(rel, globs):
            continue
        text = read_text_lax(path)
        for m in EXPORTED_TYPE.finditer(text):
            name = m.group(1) or m.group(2)
            decl = _decl_body(text, m.start())
            if not decl:
                continue
            groups.setdefault((name, _normalized(decl)), []).append(rel)
    return groups


def _dup_type_findings(groups: dict[tuple[str, str], list[str]]) -> list[Finding]:
    """One finding per shape held by two or more files, bounded."""
    findings: list[Finding] = []
    for (name, _shape), rels in groups.items():
        distinct = sorted(set(rels))
        if len(distinct) < 2:
            continue
        others = ", ".join(f"`{r}`" for r in distinct[1:4])
        more = "" if len(distinct) <= 4 else f" and {len(distinct) - 4} more"
        findings.append(Finding(
            path=distinct[0],
            line=1,
            check="duplicate-type-declaration",
            message=(
                f"exported type {name!r} declared with the same shape in "
                f"{len(distinct)} files ({others}{more}) - one source of truth"
            ),
            weight=1,
        ))
        if len(findings) >= MAX_FINDINGS:
            break
    return findings


def check_duplicate_type_decls(
    root: Path, files: list[Path], globs: list[str]
) -> list[Finding]:
    """Exported type/interface shapes held by two or more files."""
    return _dup_type_findings(_type_groups(root, files, globs))
