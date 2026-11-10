"""Unified syntax-tree facts via lizard - the parse behind the budgets.

The structural budgets (function length, parameter count, cyclomatic
complexity) are parse-backed, not regex-guessed: lizard ships real
per-language lexers for every brace family plus Ruby and Python, and one
function-fact shape for all of them. This is the "unified syntax tree"
layer the external review asked for - one mature analyzer instead of a
per-language pile of line heuristics.

Where a parse is unavailable - the optional analyzer is not installed,
the extension is outside the curated set, or the source is too malformed
for the lexer - the caller falls back to the line scanner in
slop_structural. A missing analyzer degrades the measurement; it never
breaks the scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Same ceiling the CI complexity gate enforces (`lizard -C 15`): one
# budget everywhere, so the detector and the gate can never disagree.
COMPLEXITY_BUDGET = 15

# Extensions the bundled lexers actually parse (probed against lizard's
# language table). SFC markup (.vue/.svelte/.astro) is excluded on
# purpose: a template file is not a script, and the line scanner handles
# its embedded JS better than a lexer that never sees past the markup.
LIZARD_EXTS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".groovy",
        ".cs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".hh",
        ".rb",
        ".php",
        ".rs",
        ".swift",
        ".dart",
    }
)

try:  # the analyzer is optional; the scanner stays importable without it
    import lizard  # type: ignore[import-untyped]

    _LIZARD_OK = True
except ImportError:  # pragma: no cover - exercised via the flag, not the import
    lizard = None  # type: ignore[assignment]
    _LIZARD_OK = False


def lizard_available() -> bool:
    """True when the lizard analyzer is importable in this interpreter.

    Gates that promise parse-backed metrics call this before trusting a
    zero-function result: absent facts must mean "parsed, none found",
    never "could not parse".
    """
    return _LIZARD_OK


_TRAILING_PARAMS = re.compile(r"\s*\([^()]*\)\s*$")


def _display_name(long_name: str) -> str:
    """A readable name; lizard's long_name carries the JS parameter list."""
    return _TRAILING_PARAMS.sub("", long_name) or long_name


@dataclass(frozen=True)
class FunctionFact:
    """One parsed function, in the shared shape across all languages."""

    name: str
    line: int  # 1-based start line
    nloc: int  # non-comment, non-blank lines - the honest length
    ccn: int  # cyclomatic complexity: decision points + 1
    params: int


def parse_functions(filename: str, text: str) -> list[FunctionFact] | None:
    """Parse one file into function facts.

    Returns None when no parse is possible (analyzer missing, extension
    outside LIZARD_EXTS, source the lexer rejects); an empty list means
    the file parsed fine and genuinely holds no functions. Callers must
    distinguish the two - None falls back to the line scanner, [] means
    "no functions, no function-level findings".
    """
    if not _LIZARD_OK or Path(filename).suffix not in LIZARD_EXTS:
        return None
    try:
        file_info = lizard.analyze_file.analyze_source_code(filename, text)
    except Exception:  # noqa: BLE001 - any lexer failure just means "no parse"
        return None
    return [
        FunctionFact(
            name=_display_name(fn.long_name or fn.name),
            line=fn.start_line,
            nloc=fn.nloc,
            ccn=fn.cyclomatic_complexity,
            params=fn.parameter_count,
        )
        for fn in file_info.function_list
    ]
