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

import fnmatch
import re
from pathlib import Path

from slop_files import is_ignored, is_test_path
from slop_model import Comment, Finding
from slop_patterns import (
    CODE_EXTS,
    CREDIT_PLACEHOLDER_VALUE,
    DIRECTIVE_TOOLS,
    HARDCODED_RECORD_ID,
    JS_EMPTY_FUNCTION,
    JS_THIN_WRAPPER,
    PROVIDER_ID_VALUE,
    SELF_TOOL_DIRECTIVE,
    SUPPRESSION_DIRECTIVE,
    TOOL_CONFIG_GLOBS,
    TOOL_MANIFEST_FILES,
)

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


def check_js_empty_functions(path: Path, text: str, family: str) -> Finding | None:
    """Named brace-family functions whose body is literally empty."""
    if family != "js":
        return None
    hits: list[int] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        code = raw.split("//")[0]
        if JS_EMPTY_FUNCTION.search(code):
            hits.append(idx)
    if not hits:
        return None
    return Finding(
        path=str(path),
        line=hits[0],
        check="empty-function",
        message=f"{len(hits)} named function(s) with an empty body - stub or residue",
        weight=1,
        advisory=True,
    )


def _forward_params_usable(params: str) -> bool:
    """True when a parameter list forwards cleanly (no defaults/rest)."""
    stripped = params.strip()
    if not stripped:
        return False  # zero-arg forwarders are too noisy (entry points)
    return not any(ch in stripped for ch in "={}[]") and "..." not in stripped


def check_js_thin_wrappers(path: Path, text: str, family: str) -> Finding | None:
    """Brace-family functions that only forward their parameters onward."""
    if family != "js":
        return None
    hits: list[int] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        code = raw.split("//")[0]
        match = JS_THIN_WRAPPER.search(code)
        if match and _forward_params_usable(match.group(1) or match.group(2) or ""):
            hits.append(idx)
    if not hits:
        return None
    return Finding(
        path=str(path),
        line=hits[0],
        check="thin-wrapper",
        message=(
            f"{len(hits)} function(s) only forward their arguments - "
            "inline them or give them a reason to exist"
        ),
        weight=1,
        advisory=True,
    )


def _hardcoded_id_value_ok(value: str) -> bool:
    """Filter placeholder and digit-only tokens from record-id matches."""
    if value.isdigit() or CREDIT_PLACEHOLDER_VALUE.search(value):
        return False
    has_digit = any(ch.isdigit() for ch in value)
    has_alpha = any(ch.isalpha() for ch in value)
    return has_digit and has_alpha


def check_hardcoded_ids(path: Path, text: str, rel: str) -> Finding | None:
    """Environment-specific record/provider identifiers baked into source."""
    if is_test_path(rel):
        return None
    hits: list[int] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith(_COMMENT_LINE_PREFIXES):
            continue
        if PROVIDER_ID_VALUE.search(stripped):
            hits.append(idx)
            continue
        record = HARDCODED_RECORD_ID.search(stripped)
        if record and _hardcoded_id_value_ok(record.group(2)):
            hits.append(idx)
    if not hits:
        return None
    return Finding(
        path=str(path),
        line=hits[0],
        check="hardcoded-id",
        message=f"{len(hits)} record/provider identifier(s) baked into source",
        weight=1,
        advisory=True,
    )


def _directive_tool(line: str) -> str | None:
    """The lint tool a suppression comment names, if any."""
    if SELF_TOOL_DIRECTIVE.search(line):
        return "master-ci"
    for tool in DIRECTIVE_TOOLS:
        if SUPPRESSION_DIRECTIVE.search(line) and re.search(
            rf"\b{tool}[\s:=-]+(?:ignore|disable|skip|off)\b", line, re.IGNORECASE
        ):
            return tool
    return None


def _configured_tools(root: Path) -> set[str]:
    """Tools the repo configures: root config files or manifest mentions."""
    configured: set[str] = set()
    try:
        names = {p.name for p in root.iterdir()}
    except OSError:
        names = set()
    for tool in DIRECTIVE_TOOLS:
        if any(fnmatch.fnmatch(name, glob) for name in names for glob in TOOL_CONFIG_GLOBS):
            configured.add(tool)
    candidates = [root / name for name in TOOL_MANIFEST_FILES]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        candidates.extend(workflows.glob("*.yml"))
        candidates.extend(workflows.glob("*.yaml"))
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for tool in DIRECTIVE_TOOLS:
            if re.search(rf"\b{tool}\b", text, re.IGNORECASE):
                configured.add(tool)
    return configured


def check_unknown_directives(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """Suppression comments naming tools the repo does not configure."""
    configured = _configured_tools(root)
    findings: list[Finding] = []
    for path in files:
        if path.suffix.lower() not in CODE_EXTS:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_ignored(rel, globs):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, raw in enumerate(text.splitlines(), start=1):
            tool = _directive_tool(raw)
            if tool is not None and tool not in configured:
                findings.append(
                    Finding(
                        path=str(path),
                        line=idx,
                        check="unknown-directive",
                        message=(
                            f"suppression names {tool}, but {tool} is not "
                            "configured in this repo - it suppresses nothing"
                        ),
                        weight=1,
                        advisory=True,
                    )
                )
                if len(findings) >= 3:
                    return findings
                break
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
    id_finding = check_hardcoded_ids(path, text, rel)
    if id_finding is not None:
        findings.append(id_finding)
    empty_finding = check_js_empty_functions(path, text, family)
    if empty_finding is not None:
        findings.append(empty_finding)
    thin_finding = check_js_thin_wrappers(path, text, family)
    if thin_finding is not None:
        findings.append(thin_finding)
    return findings
