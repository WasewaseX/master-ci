"""Commentary-side scanaislop checks: meta comments, prose, env leakage.

The comment doctrine from scanaislop.com's writing-comments guide,
deterministically enforced:

- meta comments record the conversation instead of the code
  (task-history narration: requirement references, stale call notes)
- prose blocks are five-plus consecutive narrating comments; the code
  already shows the what, so keep only the non-obvious why
- hardcoded URLs bake one environment (localhost, staging, internal
  hosts) into source that runs in all the others
- brace-family dead residue: constant conditions in if/while heads

All findings are advisory; aggregate pressure is enforced through the
deficit score and the workflow's slop-score ceiling.
"""

from __future__ import annotations

import re
from pathlib import Path

from slop_files import is_test_path
from slop_model import Comment, Finding

PROSE_BLOCK_MIN = 5  # consecutive narrating comments that become prose

META_COMMENT_PATTERN = re.compile(
    r"(?i)\b(?:as per (?:the )?(?:requirements?|request|spec|instructions|ticket)"
    r"|per the (?:request|spec)|previously (?:this|the|we|called|named)"
    r"|no longer needed|changed (?:my|our) mind|was previously"
    r"|updated (?:per|to match)|removed (?:per|since|as) (?:the|request))"
)

# Deployment-specific hosts: the code works in one environment and
# quietly calls it from all the others. Public API/docs hosts are fine.
HARDCODED_URL_PATTERN = re.compile(
    r"(?i)https?://(?:"
    r"localhost(?::\d+)?|127\.0\.0\.1(?::\d+)?|0\.0\.0\.0(?::\d+)?"
    r"|192\.168\.\d+\.\d+(?::\d+)?|10\.\d+\.\d+\.\d+(?::\d+)?"
    r"|[\w.-]+\.local(?:[:/)\s\"']|$)|[\w.-]+\.internal(?:[:/)\s\"']|$)"
    r"|[\w-]+\.ngrok(?:-free)?\.(?:app|io|dev)"
    r"|[a-z0-9.-]*staging[a-z0-9.-]*\.[a-z]{2,})"
)

_COMMENT_LINE_PREFIXES = ("#", "//")

JS_FALSE_CONDITION = re.compile(r"(?i)[^.\w](?:if|while)\s*\(\s*(?:false|\d+)\s*\)")
JS_TRUE_CONDITION = re.compile(r"(?i)[^.\w]if\s*\(\s*true\s*\)")


def check_meta_comments(path: Path, comments: list[Comment]) -> Finding | None:
    """Comments that record the conversation instead of the code."""
    hits = [c for c in comments if META_COMMENT_PATTERN.search(c.text)]
    if not hits:
        return None
    return Finding(
        path=str(path),
        line=hits[0].line,
        check="meta-comment",
        message=f"{len(hits)} comment(s) about the task/change, not the code",
        weight=1,
        advisory=True,
    )


def check_prose_blocks(path: Path, comments: list[Comment]) -> list[Finding]:
    """Runs of five-plus narrating comments that walk through the code."""
    findings: list[Finding] = []
    run: list[Comment] = []

    def flush(current: list[Comment]) -> None:
        if len(current) >= PROSE_BLOCK_MIN:
            findings.append(
                Finding(
                    path=str(path),
                    line=current[0].line,
                    check="prose-block",
                    message=(
                        f"{len(current)}-line comment block narrates the code "
                        "- keep only the non-obvious why"
                    ),
                    weight=1,
                    advisory=True,
                )
            )

    previous_line = -2
    for comment in comments:
        if comment.kind not in ("line", "block"):
            flush(run)
            run = []
            previous_line = -2
            continue
        if comment.line == previous_line + 1 and run and run[0].kind == comment.kind:
            run.append(comment)
        else:
            flush(run)
            run = [comment]
        previous_line = comment.line
    flush(run)
    return findings


def check_hardcoded_urls(path: Path, text: str, rel: str) -> Finding | None:
    """Deployment-specific URLs baked into non-test source."""
    if is_test_path(rel):
        return None
    hits: list[int] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith(_COMMENT_LINE_PREFIXES):
            continue
        if HARDCODED_URL_PATTERN.search(stripped):
            hits.append(idx)
    if not hits:
        return None
    return Finding(
        path=str(path),
        line=hits[0],
        check="hardcoded-url",
        message=f"{len(hits)} environment-specific URL(s) baked into source",
        weight=1,
        advisory=True,
    )


def check_js_dead_patterns(path: Path, text: str, family: str) -> list[Finding]:
    """Brace-family residue: constant conditions in if/while heads."""
    if family != "js":
        return []
    findings: list[Finding] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        code = raw.split("//")[0]
        if JS_FALSE_CONDITION.search(code) or JS_TRUE_CONDITION.search(code):
            findings.append(
                Finding(
                    path=str(path),
                    line=idx,
                    check="constant-condition",
                    message="branch condition is a literal constant",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def check_commentary(
    path: Path,
    text: str,
    family: str,
    rel: str,
    comments: list[Comment],
) -> list[Finding]:
    """Commentary-side checks for one file."""
    findings: list[Finding] = []
    findings.extend(check_js_dead_patterns(path, text, family))
    findings.extend(check_prose_blocks(path, comments))
    meta_finding = check_meta_comments(path, comments)
    if meta_finding is not None:
        findings.append(meta_finding)
    url_finding = check_hardcoded_urls(path, text, rel)
    if url_finding is not None:
        findings.append(url_finding)
    return findings
