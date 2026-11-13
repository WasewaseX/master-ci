#!/usr/bin/env python3
"""Analysis-coverage classification for the new-code gate.

The v2.8 answer to the external audit's parser-coverage finding: a
changed file a requested parser cannot measure must never read as
clean. Every changed file lands in one named state - parsed, no
functions, excluded by a documented rule, unsupported language, or
parser failure - and a parse failure under a requested ceiling is
fatal (exit 2). The gate renders the states in its step summary, so
the analysis boundary is always visible instead of guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slop_cognitive import TS_LANGUAGE_OF_EXT, cognitive_facts
from slop_files import read_text_lax
from slop_lizard import LIZARD_EXTS, parse_functions

# Documented non-source boundary: docs, configs, manifests, lockfiles,
# data and assets - excluded by rule, never silently (the coverage
# section counts them). Everything else outside the parser tables is
# "unsupported language": visible, reported, never a silent pass.
EXCLUDED_EXTS = frozenset(
    {
        ".md",
        ".rst",
        ".txt",
        ".adoc",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".lock",
        ".sum",
        ".mod",
        ".sln",
        ".csproj",
        ".vbproj",
        ".gradle",
        ".properties",
        ".env",
        ".csv",
        ".pem",
        ".key",
    }
)


@dataclass
class AnalysisCoverage:
    """Per-file analysis states for the parser-backed ceilings.

    The external audit's taxonomy, adopted verbatim: every changed file
    lands in exactly one of the five states, the section renders
    whenever a parser-backed ceiling is requested, and only a parse
    failure blocks. "Unanalyzed" can no longer read as "clean".
    """

    parsed: list[str] = field(default_factory=list)
    no_functions: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    parse_failed: list[str] = field(default_factory=list)


def _engine_state(path: Path, ext: str, want_lizard: bool, want_ts: bool) -> str:
    """Parse one file with the requested engines; classify the outcome.

    Returns "parse-failed", "no-functions" or "parsed". An engine that
    is not requested does not vote; lizard's None (could not lex) and
    empty list (parsed, genuinely no functions) are distinguished, and
    tree-sitter's None (no tree: parse error or syntax error) is fatal.
    """
    state = "parsed"
    text = read_text_lax(path)
    if want_lizard:
        facts = parse_functions(str(path), text)
        if facts is None:
            return "parse-failed"
        if not facts:
            state = "no-functions"
    if want_ts:
        cfacts = cognitive_facts(ext, text)
        if cfacts is None:
            return "parse-failed"
        if not cfacts and state == "parsed":
            state = "no-functions"
    return state


def analysis_coverage(
    root: Path, changed: dict[str, set[int]], want_cognitive: bool, want_cyclomatic: bool
) -> AnalysisCoverage:
    """Classify every changed file the requested ceilings touch."""
    coverage = AnalysisCoverage()
    for rel in sorted(changed):
        ext = Path(rel).suffix.lower()
        if not ext or ext in EXCLUDED_EXTS:
            coverage.excluded.append(rel)
            continue
        if ext not in LIZARD_EXTS and ext not in TS_LANGUAGE_OF_EXT:
            coverage.unsupported.append(rel)
            continue
        state = _engine_state(
            root / rel,
            ext,
            want_cyclomatic and ext in LIZARD_EXTS,
            want_cognitive and ext in TS_LANGUAGE_OF_EXT,
        )
        if state == "parse-failed":
            engine = "tree-sitter" if want_cognitive and ext in TS_LANGUAGE_OF_EXT else "lizard"
            coverage.parse_failed.append(f"{rel} ({engine})")
        elif state == "no-functions":
            coverage.no_functions.append(rel)
        else:
            coverage.parsed.append(rel)
    return coverage


def coverage_section(coverage: AnalysisCoverage) -> str:
    """Render the analysis-coverage report for the step summary."""
    lines = [
        "### New-code gate: analysis coverage",
        "",
        "Every changed file lands in one state. A parse failure under a",
        "requested ceiling is fatal; `n/a` cognitive cells in the complexity",
        "table mark languages with no tree-sitter grammar (e.g. `.groovy`).",
        "",
        "| State | Count | Files |",
        "|---|---|---|",
    ]
    rows = (
        ("Parsed and analyzed", coverage.parsed, False),
        ("Parsed, no functions", coverage.no_functions, False),
        ("Excluded (non-source)", coverage.excluded, False),
        ("Unsupported language", coverage.unsupported, True),
        ("Parse failure", coverage.parse_failed, True),
    )
    for label, files, show in rows:
        if show and files:
            names = ", ".join(f"`{f}`" for f in files[:10])
            more = f" (+{len(files) - 10} more)" if len(files) > 10 else ""
            cell = f"{names}{more}"
        else:
            cell = "-"
        lines.append(f"| {label} | {len(files)} | {cell} |")
    lines.append("")
    return "\n".join(lines)
