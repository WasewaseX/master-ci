"""UI slop and prose/document checks."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from slop_comments import phrase_findings
from slop_model import Finding, _finding
from slop_patterns import DOC_EXTS, EMOJI_PATTERN, MD_EMOJI_HEADING, MD_RESIDUE, WORD_PATTERN
from slop_ui_patterns import (
    UI_CARD_CLASS,
    UI_DECORATIVE_NUMBERS,
    UI_EM_DASH_MIN,
    UI_EXTS,
    UI_EYEBROW,
    UI_FAKE_STATS,
    UI_GENERIC_TESTIMONIAL,
    UI_GLASS,
    UI_GRADIENT,
    UI_GRADIENT_TEXT,
    UI_ITALIC_HEADLINE,
    UI_MARKETING,
    UI_NOT_JUST,
    UI_PLACEHOLDER,
    UI_PLACEHOLDER_IMAGE,
    UI_TRIPLE_SLOGAN,
)


def check_ui_slop(path: Path, text: str, ext: str) -> list[Finding]:
    if ext not in UI_EXTS and ext not in {
        ".jsx",
        ".tsx",
        ".vue",
        ".svelte",
        ".astro",
        ".html",
        ".htm",
    }:
        return []
    findings: list[Finding] = []
    if UI_GRADIENT.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-ai-gradient",
                message="AI-default purple/pink gradient",
                weight=2,
                advisory=True,
            )
        )
    if UI_GRADIENT_TEXT.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-gradient-text",
                message="gradient clip-text headline",
                weight=1,
                advisory=True,
            )
        )
    if UI_MARKETING.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-marketing-copy",
                message="marketing buzzwords in UI copy",
                weight=1,
                advisory=True,
            )
        )
    if UI_PLACEHOLDER.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-placeholder-copy",
                message="placeholder copy (lorem / your-name-here)",
                weight=1,
                advisory=True,
            )
        )
    if ext in UI_EXTS:
        emojis = len(EMOJI_PATTERN.findall(text))
        if emojis >= 3:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="ui-emoji-decor",
                    message=f"{emojis} emoji used as decoration",
                    weight=1,
                    advisory=True,
                )
            )
    findings.extend(_design_sign_findings(path, text))
    return findings


def _design_sign_findings(path: Path, text: str) -> list[Finding]:
    """Generic-AI design tells (tenex '8 signs', noqode 'design IA generique')."""
    findings: list[Finding] = []

    def add(check: str, message: str) -> None:
        findings.append(
            Finding(path=str(path), line=1, check=check, message=message, weight=1, advisory=True)
        )

    if UI_EYEBROW.search(text):
        add("ui-eyebrow-label", "eyebrow/overline/kicker label above headlines")
    if UI_ITALIC_HEADLINE.search(text):
        add("ui-italic-serif-hero", "italic word inside a headline (AI hero gesture)")
    if UI_FAKE_STATS.search(text):
        add("ui-fake-stats", "unsourced marketing figures (10,000+ customers)")
    if UI_GENERIC_TESTIMONIAL.search(text):
        add("ui-generic-testimonial", "testimonial with first name + role only")
    if UI_NOT_JUST.search(text):
        add("ui-not-just-x", "'not just X, it's Y' copy pattern")
    if UI_TRIPLE_SLOGAN.search(text):
        add("ui-triple-slogan", "'Simple. Fast. Efficient.' three-word slogan")
    if len(UI_GLASS.findall(text)) >= 2:
        add("ui-glass-glow", "2+ backdrop-blur glass effects in one file")
    if len(UI_DECORATIVE_NUMBERS.findall(text)) >= 3:
        add("ui-decorative-numbering", "3+ decorative zero-padded numbers (01/02/03)")
    if UI_PLACEHOLDER_IMAGE.search(text):
        add("ui-placeholder-image", "placeholder image service stands in for a real photo")
    dashes = text.count("\u2014")
    if dashes >= UI_EM_DASH_MIN:
        add("ui-em-dash-overuse", f"{dashes} em dashes in one file (the em dash, everywhere)")
    for cls, count in Counter(UI_CARD_CLASS.findall(text)).most_common(1):
        if count >= 4:
            add(
                "ui-identical-cards",
                f"same class string repeated {count}x - one template, not {count} designs",
            )
    return findings


def check_doc_extras(path: Path, text: str, ext: str) -> list[Finding]:
    if ext not in DOC_EXTS:
        return []
    findings: list[Finding] = []
    if ext in (".md", ".mdx") and MD_EMOJI_HEADING.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="md-emoji-headings",
                message="emoji in markdown headings",
                weight=1,
                advisory=True,
            )
        )
    residue = MD_RESIDUE.search(text)
    if residue:
        findings.append(
            _finding(
                path,
                text,
                residue.start(),
                "md-residue",
                "chatbot residue or placeholder in document",
                2,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Prose rhythm (docs) - always advisory
# ---------------------------------------------------------------------------


def prose_rhythm_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    prose_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            prose_lines.append(line)
    prose = "\n".join(prose_lines)
    words = max(len(WORD_PATTERN.findall(prose)), 1)
    emdashes = prose.count("\u2014")
    if emdashes / words * 1000 > 4:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="em-dash-density",
                message=f"em-dash density {emdashes / words * 1000:.1f}/1k words",
                weight=3,
                advisory=True,
            )
        )
    groups = collect_bullet_groups(prose_lines)
    if len(groups) >= 3:
        threes = sum(1 for group in groups if len(group) == 3)
        if threes / len(groups) > 0.4:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="rule-of-three",
                    message=(f"{threes}/{len(groups)} bullet lists have exactly 3 items"),
                    weight=2,
                    advisory=True,
                )
            )
    questions = prose.count("?")
    if questions / words * 500 > 3:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="rhetorical-questions",
                message=f"{questions} question marks in prose",
                weight=2,
                advisory=True,
            )
        )
    bangs = len(re.findall(r"!(?![\[(=])", prose))  # skip ![img], != operators
    if bangs > 5:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="exclamations",
                message=f"{bangs} exclamation marks in prose",
                weight=1,
                advisory=True,
            )
        )
    return findings


def collect_bullet_groups(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^\s*[-*]\s+", line):
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def scan_doc_file(path: Path, ext: str, phrases: dict) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    findings.extend(
        phrase_findings(path, text, phrases["doc_phrases"], "ai-prose", 1, advisory=True)
    )
    findings.extend(
        phrase_findings(path, text, phrases["humanizer_phrases"], "humanizer", 1, advisory=True)
    )
    findings.extend(prose_rhythm_findings(path, text))
    findings.extend(check_doc_extras(path, text, ext))
    return findings


# ---------------------------------------------------------------------------
# Code file scan
# ---------------------------------------------------------------------------
