"""Cross-file identical-line duplication detection."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from slop_model import Finding
from slop_patterns import CODE_EXTS, DUP_LINE_MIN_LEN, DUP_LINE_THRESHOLD


def scan_duplicates(files: list[Path]) -> list[Finding]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    for path in files:
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
