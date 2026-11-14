"""Cross-file identical-line duplication detection."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from slop_files import is_ignored
from slop_model import Finding
from slop_patterns import CODE_EXTS, DUP_LINE_MIN_LEN, DUP_LINE_THRESHOLD


def _scoped_files(files: list[Path], root: Path | None, globs: list[str] | None) -> list[Path]:
    """files minus the ignore-file paths, when a scan root is known."""
    if root is None or globs is None:
        return files
    return [path for path in files if not _ignored(path, root, globs)]


def scan_duplicates(
    files: list[Path],
    root: Path | None = None,
    globs: list[str] | None = None,
) -> list[Finding]:
    """Report the most repeated stripped code lines, worst three first.

    root + globs honor .ai-integrity-ignore: the repo's own fixtures
    deliberately repeat lines (the benchmark's duplicate-implementation
    tree exists to fire this rule), and a detector must not judge the
    stage props it is pointed at. Both optional - direct callers that
    already scoped their file list stay untouched."""
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    for path in _scoped_files(files, root, globs):
        if path.suffix.lower() not in CODE_EXTS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith(("#", "//", "*", "<!--")):
                continue
            if len(line) >= DUP_LINE_MIN_LEN:
                counter[line] += 1
                first_seen.setdefault(line, (str(path), lineno))
    findings: list[Finding] = []
    for line, count in counter.most_common():
        if count < DUP_LINE_THRESHOLD:
            break
        where = first_seen[line]
        findings.append(
            Finding(
                path=where[0],
                line=where[1],
                check="boilerplate-duplication",
                message=f"identical line repeated {count}x: {line[:60]}...",
                weight=4,
            )
        )
    return findings[:3]


def _ignored(path: Path, root: Path, globs: list[str]) -> bool:
    """The same relative-path rule the per-file scan applies."""
    try:
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
    except ValueError:
        rel = path.as_posix()
    return is_ignored(rel, globs)


# Provenance markers: traceability hints, not authorship verdicts.
PROVENANCE_MARKERS = (
    (
        re.compile(r"generated\s+(?:by|with|using)\s+(?:github\s+)?copilot", re.IGNORECASE),
        "Copilot generation banner",
    ),
    (
        re.compile(
            r"generated\s+(?:by|with|using)\s+"
            r"(?:claude|anthropic|gpt|openai|chatgpt|cursor|aider|gemini|deepseek)",
            re.IGNORECASE,
        ),
        "LLM generation banner",
    ),
    (
        re.compile(
            r"co-?authored-?by:.*(?:copilot|claude|gpt|openai|anthropic|"
            r"cursor|aider|gemini|deepseek)",
            re.IGNORECASE,
        ),
        "AI co-author trailer",
    ),
    (re.compile(r"\bai[- ]generated\b", re.IGNORECASE), "AI label"),
    (re.compile(r"@\s?generated\b", re.IGNORECASE), "@" + "generated tag"),
    (
        re.compile(
            r"this (?:file|code|module) (?:was |is )?(?:auto[- ])?generated (?:by|with)",
            re.IGNORECASE,
        ),
        "generated banner",
    ),
)
MIN_SOURCES_FOR_INVENTORY = 8
