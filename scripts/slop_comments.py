"""Language-aware comment extraction and comment-quality checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from slop_model import Comment, Finding
from slop_patterns import (
    BLOCK_CLOSE,
    BLOCK_OPEN,
    CHATBOT_COMMENT_PHRASES,
    EMOJI_PATTERN,
    STEP_COMMENT_PATTERN,
    TRIVIAL_COMMENT,
    _line_of,
)


def _py_docstring_step(
    idx: int,
    stripped: str,
    in_doc: bool,
    doc_delim: str,
) -> tuple[Comment, bool, str] | None:
    """Handle one line while in/entering Python docstring state.

    Returns (comment, still_in_doc, delim), or None when the line is not
    docstring state and the caller should fall through to other rules.
    """
    if in_doc:
        closing = doc_delim in stripped
        return Comment(idx, stripped, "docstring"), not closing, doc_delim
    if not stripped.startswith(('"""', "'''")):
        return None
    delim = stripped[:3]
    if stripped.count(delim) >= 2 and len(stripped) > 3:
        return Comment(idx, stripped, "docstring"), False, doc_delim
    return Comment(idx, stripped, "docstring"), True, delim


def extract_comments(text: str, family: str) -> list[Comment]:
    """Extract comments with a small state machine per language family."""
    line_prefixes = {
        "py": ("#",),
        "sh": ("#",),
        "ruby": ("#",),
        "php": ("//", "#"),
    }.get(family, ("//",))
    comments: list[Comment] = []
    in_block = False
    in_doc = False
    doc_delim = ""
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if in_block:
            comments.append(Comment(idx, stripped, "block"))
            if BLOCK_CLOSE in stripped:
                in_block = False
            continue
        if family == "py":
            step = _py_docstring_step(idx, stripped, in_doc, doc_delim)
            if step is not None:
                comments.append(step[0])
                in_doc, doc_delim = step[1], step[2]
                continue
        if family == "ruby" and stripped.startswith("=begin"):
            in_block = True
            comments.append(Comment(idx, stripped, "block"))
            continue
        if any(stripped.startswith(prefix) for prefix in line_prefixes):
            comments.append(Comment(idx, stripped, "line"))
            continue
        if stripped.startswith(BLOCK_OPEN):
            comments.append(Comment(idx, stripped, "block"))
            if BLOCK_CLOSE not in stripped[len(BLOCK_OPEN) :]:
                in_block = True
    return comments


# ---------------------------------------------------------------------------
# Phrase-based checks
# ---------------------------------------------------------------------------


def phrase_findings(
    path: Path,
    text: str,
    phrases: list[str],
    check: str,
    weight: int,
    advisory: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    lowered = text.lower()
    for phrase in phrases:
        first = lowered.find(phrase.lower())
        if first == -1:
            continue
        line = _line_of(text, first)
        count = lowered.count(phrase.lower())
        findings.append(
            Finding(
                path=str(path),
                line=line,
                check=check,
                message=f"{phrase!r} found ({count}x)",
                weight=weight,
                advisory=advisory,
            )
        )
    return findings


def _phrase_in_any(comment_texts: list[str], phrase: str) -> int:
    return sum(1 for text in comment_texts if phrase.lower() in text.lower())


@dataclass(frozen=True)
class PhraseRule:
    """Bundle for one phrase-family scan (keeps the helper at few params)."""

    path: Path
    comments: list[Comment]
    texts: list[str]
    phrases: Sequence[str]
    check: str
    message_fmt: str
    weight: int


def _phrase_rule_findings(rule: PhraseRule) -> list[Finding]:
    """One finding per phrase family that appears in any comment."""
    path, comments, texts = rule.path, rule.comments, rule.texts
    check, message_fmt, weight = rule.check, rule.message_fmt, rule.weight
    findings: list[Finding] = []
    for phrase in rule.phrases:
        count = _phrase_in_any(texts, phrase)
        if not count:
            continue
        line = next(c.line for c in comments if phrase.lower() in c.text.lower())
        findings.append(
            Finding(
                path=str(path),
                line=line,
                check=check,
                message=message_fmt.format(phrase=phrase, count=count),
                weight=weight,
            )
        )
    return findings


def _comment_style_findings(path: Path, comments: list[Comment]) -> list[Finding]:
    """Emoji padding, trivial restatements and step-by-step narration."""
    findings: list[Finding] = []
    emoji_hits = sum(bool(EMOJI_PATTERN.search(c.text)) for c in comments)
    if emoji_hits:
        findings.append(
            Finding(
                path=str(path),
                line=comments[0].line,
                check="emoji-comments",
                message=f"emoji in code comments ({emoji_hits}x)",
                weight=min(emoji_hits, 5),
            )
        )
    line_comments = [c.text for c in comments if c.kind == "line"]
    if len(line_comments) >= 5:
        trivial = sum(bool(TRIVIAL_COMMENT.match(c.strip())) for c in line_comments)
        if trivial / len(line_comments) > 0.4:
            findings.append(
                Finding(
                    path=str(path),
                    line=comments[0].line,
                    check="trivial-comments",
                    message=(f"{trivial}/{len(line_comments)} comments merely restate the code"),
                    weight=3,
                )
            )
    step_lines = [c for c in comments if STEP_COMMENT_PATTERN.match(c.text)]
    if len(step_lines) >= 3:
        findings.append(
            Finding(
                path=str(path),
                line=step_lines[0].line,
                check="step-comments",
                message=f"{len(step_lines)} step-by-step narration comments",
                weight=1,
                advisory=True,
            )
        )
    return findings


def scan_comment_vocabulary(path: Path, comments: list[Comment], phrases: dict) -> list[Finding]:
    texts = [c.text for c in comments]
    findings = _phrase_rule_findings(
        PhraseRule(
            path,
            comments,
            texts,
            phrases["code_phrases"],
            "ai-vocab",
            "{phrase!r} in comment ({count}x)",
            2,
        )
    )
    findings += _phrase_rule_findings(
        PhraseRule(
            path,
            comments,
            texts,
            CHATBOT_COMMENT_PHRASES,
            "chatbot-residue",
            "assistant-voice {phrase!r} in comment ({count}x)",
            2,
        )
    )
    findings += _comment_style_findings(path, comments)
    return findings


# ---------------------------------------------------------------------------
# Structural checks (per language family)
# ---------------------------------------------------------------------------
