#!/usr/bin/env python3
"""Inline suppression directives - the scanaislop round-11 doctrine change.

Until round 11 this detector rejected every inline self directive: the
opt-out mechanism was the ignore file, and ``SELF_TOOL_DIRECTIVE`` flagged
any inline ``master-ci-ignore`` as an invented suppression that suppresses
nothing. That doctrine was safe but coarse - silencing one false positive
on one line forced a whole-path ignore entry, and a path ignore outlives
the defect it hid.

Round 11 adopts scanaislop's suppression design instead: inline directives
are a real, scoped, explicit mechanism.

- ``master-ci-ignore-line``        suppress findings on the same line
- ``master-ci-ignore``             same as -line (the bare form)
- ``master-ci-ignore-next-line``   suppress findings on the following line
- ``master-ci-ignore-file``        suppress advisory findings file-wide

The file scope never suppresses blocking-grade findings: a one-line
opt-out is an intent statement about one line, but a file-wide opt-out
that could bury an elided-code or swallowed-exception finding would make
the blocking score negotiable. The ignore file remains the only mechanism
that silences blocking checks file-wide, and it does so in a reviewed,
diff-visible file.

Every other self-prefixed form is an invented directive that suppresses
nothing and is reported as ``unknown-directive`` with a did-you-mean hint
(the compact-scope comparison scanaislop uses: ``-nextline`` suggests
``-next-line``). Directives count only inside real comments - string
literals and docstrings are never suppression positions - so a regex
table that mentions the directive in prose neither suppresses nor flags.

The suppression is visible, not silent: ``apply_inline_suppression``
returns the suppressed count, the scan stashes it in the metrics bundle,
and ``--json`` / the summary line report it beside the baseline count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from slop_comments import _line_prefixes, extract_comments
from slop_files import is_ignored, read_text_lax
from slop_model import Finding
from slop_patterns import CODE_EXTS, FAMILY_OF_EXT, _strip_strings

PREFIX = "master-ci-ignore"
CANONICAL_SCOPES = ("line", "next-line", "file")
_BARE_SCOPE = "line"  # the bare form suppresses its own line

# One regex for the whole self namespace; the scope group decides whether
# the directive is functional or invented.
DIRECTIVE_RE = re.compile(
    re.escape(PREFIX) + r"(?:-([a-z][a-z0-9-]*))?", re.IGNORECASE
)


def _compact(scope: str) -> str:
    return scope.lower().replace("-", "").replace("_", "")


_COMPACT_KNOWN = {_compact(scope): scope for scope in CANONICAL_SCOPES}


def closest_scope(scope: str) -> str | None:
    """The canonical scope the writer probably meant, when one is close."""
    return _COMPACT_KNOWN.get(_compact(scope))


def is_functional(line: str) -> bool:
    """True when the line carries a canonical (known-scope) directive.

    Bare ``master-ci-ignore`` counts: it is the documented same-line form.
    Trailing prose after the bare form does not change the meaning. The
    compact comparison powers only the did-you-mean hint, never this
    verdict: ``-nextline`` is an invented scope, not a functional one.
    """
    for match in DIRECTIVE_RE.finditer(line):
        scope = match.group(1)
        if scope is None or scope.lower() in CANONICAL_SCOPES:
            return True
    return False


@dataclass
class FileSuppress:
    """One file's suppression positions, parsed from its comments."""

    same: set[int] = field(default_factory=set)
    following: set[int] = field(default_factory=set)
    whole_file: bool = False


def _directive_lines(comment_text: str, start_line: int) -> list[tuple[int, str | None, str | None]]:
    """(line, canonical_scope, unknown_scope) for each directive in one comment.

    Block comments span lines: the offset of the matched line inside the
    comment text is added to the comment's start line, so a directive on
    the third line of a /* */ block reports its real file line.
    """
    out: list[tuple[int, str | None, str | None]] = []
    for offset, text_line in enumerate(comment_text.splitlines()):
        for match in DIRECTIVE_RE.finditer(text_line):
            scope = match.group(1)
            if scope is None:
                out.append((start_line + offset, _BARE_SCOPE, None))
            elif scope.lower() in CANONICAL_SCOPES:
                # Exact scope only: the compact comparison powers the
                # did-you-mean hint, never the functionality. aislop's
                # own docs pin this - "aislop-ignore-nextline" suppresses
                # nothing, and so does "master-ci-ignore-nextline".
                out.append((start_line + offset, scope.lower(), None))
            else:
                out.append((start_line + offset, None, scope))
    return out


def parse_file_suppression(path: Path) -> tuple[FileSuppress, list[Finding]]:
    """Parse one file's comments into suppression positions + unknown findings.

    Returns (positions, unknown_directive_findings). Non-code extensions
    return empty: the directive is defined for the language families the
    comment extractor knows, the same scope scanaislop claims.

    Directive positions: any full-line comment (extract_comments covers
    line and block comments, docstrings excluded) plus trailing comments
    after code on the same line, found on the string-masked text so a
    directive quoted inside a string literal never counts.
    """
    positions = FileSuppress()
    unknown: list[Finding] = []
    ext = path.suffix.lower()
    if ext not in CODE_EXTS:
        return positions, unknown
    family = FAMILY_OF_EXT.get(ext, "")
    if not family:
        return positions, unknown
    try:
        text = read_text_lax(path)
    except OSError:
        return positions, unknown
    prefixes = _line_prefixes(family)
    comment_lines = {
        c.line for c in extract_comments(text, family) if c.kind != "docstring"
    }
    for idx, masked in enumerate(_strip_strings(text).splitlines(), start=1):
        if idx in comment_lines:
            tail = masked
        else:
            cut = _comment_cut(masked, prefixes)
            if cut is None:
                continue
            tail = masked[cut:]
        for line, canonical, unknown_scope in _directive_lines(tail, idx):
            _register(positions, unknown, path, line, canonical, unknown_scope)
    return positions, unknown


def _comment_cut(masked: str, prefixes: tuple[str, ...]) -> int | None:
    """Where the trailing comment starts on a code line, or None.

    The earliest of any line-comment prefix or a block-comment opener
    wins; a full-line comment never reaches here (comment_lines check).
    """
    cuts = [masked.find(prefix) for prefix in prefixes]
    cuts.append(masked.find("/*"))
    present = [cut for cut in cuts if cut != -1]
    return min(present) if present else None


def _register(
    positions: FileSuppress,
    unknown: list[Finding],
    path: Path,
    line: int,
    canonical: str | None,
    unknown_scope: str | None,
) -> None:
    """File one parsed directive occurrence into positions or findings."""
    if unknown_scope is not None:
        suggestion = closest_scope(unknown_scope)
        hint = (
            f' Did you mean "{PREFIX}-{suggestion}"?'
            if suggestion
            else f" Use {', '.join(PREFIX + '-' + s for s in CANONICAL_SCOPES)},"
            f" or a bare {PREFIX} for the same line."
        )
        unknown.append(
            Finding(
                path=str(path),
                line=line,
                check="unknown-directive",
                message=(
                    f'"{PREFIX}-{unknown_scope}" is not a suppression'
                    f" directive, so it suppresses nothing.{hint}"
                ),
                weight=1,
                advisory=True,
            )
        )
    elif canonical == "file":
        positions.whole_file = True
    elif canonical == "next-line":
        positions.following.add(line)
    else:
        positions.same.add(line)


def collect_suppression(
    base: Path,
    files: list[Path],
    globs: list[str],
) -> tuple[dict[str, FileSuppress], list[Finding]]:
    """The whole-file suppression map plus every invented-directive finding.

    Keys are the repo-relative posix paths the findings carry; ignored
    paths are skipped, the same scope every other pass honors.
    """
    table: dict[str, FileSuppress] = {}
    unknown: list[Finding] = []
    for path in files:
        ext = path.suffix.lower()
        if ext not in CODE_EXTS:
            continue
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        positions, file_unknown = parse_file_suppression(path)
        if positions.whole_file or positions.same or positions.following:
            table[rel] = positions
        unknown.extend(file_unknown)
    return table, unknown


def apply_inline_suppression(
    findings: list[Finding],
    table: dict[str, FileSuppress],
    base: Path,
) -> tuple[list[Finding], int]:
    """Drop suppressed findings; return (kept, suppressed_count).

    Line scopes (same/next-line) suppress any finding on that line -
    the writer pointed at one line, blocking grade included. The file
    scope suppresses advisory findings only (see the module docstring):
    the blocking grade survives so a stray file directive cannot hide a
    deterministic content defect.

    Findings carry whatever path their engine passed (absolute for the
    per-file passes, repo-relative for some repo-level ones), so the
    lookup normalizes each path against the scan base before reading
    the table.
    """
    kept: list[Finding] = []
    suppressed = 0

    def _rel(path: str) -> str:
        candidate = Path(path)
        bases = [base]
        try:
            bases.append(base.resolve())
            candidates = [candidate, candidate.resolve()]
        except OSError:
            candidates = [candidate]
        for item in candidates:
            for candidate_base in bases:
                try:
                    return item.relative_to(candidate_base).as_posix()
                except ValueError:
                    continue  # next (item, base) pair - a real fallback
        return candidate.as_posix()

    for finding in findings:
        positions = table.get(finding.path) or table.get(_rel(finding.path))
        if positions is None:
            kept.append(finding)
            continue
        if positions.whole_file and finding.advisory:
            suppressed += 1
            continue
        if (
            finding.line in positions.same
            or (finding.line - 1) in positions.following
        ):
            suppressed += 1
            continue
        kept.append(finding)
    return kept, suppressed
