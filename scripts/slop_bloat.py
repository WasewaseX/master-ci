"""The anti-bloatware budget checks (file length, params, comment pressure, banners)."""

from __future__ import annotations

import re
from pathlib import Path

from slop_files import is_test_path
from slop_model import Comment, Finding
from slop_structural import (
    BANNER_DECO,
    BLOAT_ARROW_STYLE,
    BLOAT_BANNER_GAP,
    BLOAT_BANNER_MIN,
    BLOAT_COMMENT_MIN_LINES,
    BLOAT_COMMENT_RATIO,
    BLOAT_FILE_LINES,
    BLOAT_HEADER_EXCLUDE,
    BLOAT_KW_FUNCS,
    BLOAT_MAX_PARAMS,
    BLOAT_METHOD_STYLE,
    GOD_FUNCTION_LINES,
    MAX_NESTING,
    RUBY_CLOSER,
    RUBY_OPENER,
    _brace_depths,
    _code_lines,
    _last_param_group,
)


def _param_count(header: str) -> int:
    group = _last_param_group(header)
    if group is None or not group.strip():
        return 0
    depth = 0
    params = 1
    for ch in group:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            params += 1
    return params


def _header_openers(code_lines: list[str]) -> list[tuple[int, str]]:
    """Lines that may open a function block: (index, header_text).

    A header is a signature line that carries its own `{` or is followed by
    a line whose first token is `{` (Allman style). Control, expression and
    declaration keywords never open blocks.
    """
    n = len(code_lines)
    headers: list[tuple[int, str]] = []
    for idx, code in enumerate(code_lines):
        stripped = code.strip()
        if not stripped or BLOAT_HEADER_EXCLUDE.match(stripped):
            continue
        has_parens = "(" in code and ")" in code
        is_kw = bool(BLOAT_KW_FUNCS.search(code))
        is_method = bool(BLOAT_METHOD_STYLE.match(code))
        is_arrow = bool(BLOAT_ARROW_STYLE.search(code))
        if not (has_parens and (is_kw or is_method or is_arrow)):
            continue
        if "{" in code:
            headers.append((idx, code))
            continue
        for nxt in range(idx + 1, min(idx + 3, n)):
            nxt_stripped = code_lines[nxt].strip()
            if not nxt_stripped:
                continue
            if nxt_stripped.startswith("{"):
                headers.append((idx, code))
            break
    return headers


def _brace_blocks(code_lines: list[str]) -> list[dict]:
    """Function-ish brace blocks with length and control-nesting depth."""
    depth_end, max_depth, max_control = _brace_depths(code_lines)
    blocks: list[dict] = []
    for idx, header in _header_openers(code_lines):
        open_idx: int
        if "{" in code_lines[idx]:
            open_idx = idx
        else:
            found: int | None = None
            for nxt in range(idx + 1, min(idx + 3, len(code_lines))):
                if code_lines[nxt].strip().startswith("{"):
                    found = nxt
                    break
            if found is None:
                continue
            open_idx = found
        open_depth = depth_end[open_idx] - 1
        if open_depth < 0:
            open_depth = 0
        close_idx = None
        for j in range(open_idx, len(code_lines)):
            if depth_end[j] <= open_depth and j >= open_idx:
                close_idx = j
                break
        if close_idx is None:
            continue
        inner_ctl = max(max_control[open_idx : close_idx + 1]) - (open_depth + 1)
        blocks.append(
            {
                "header": idx,
                "open": open_idx,
                "close": close_idx,
                "length": close_idx - idx + 1,
                "depth": max(0, inner_ctl),
                "header_text": header,
            }
        )
    return blocks


def _ruby_def_blocks(code_lines: list[str]) -> list[dict]:
    """def/end blocks with length and nesting via keyword pairing."""
    blocks: list[dict] = []
    n = len(code_lines)
    idx = 0
    while idx < n:
        if re.match(r"^\s*def\s", code_lines[idx]):
            depth = 0
            max_depth = 0
            close = None
            for j in range(idx, n):
                if j > idx and RUBY_OPENER.match(code_lines[j]):
                    depth += 1
                    max_depth = max(max_depth, depth)
                if RUBY_CLOSER.match(code_lines[j]):
                    if depth == 0:
                        close = j
                        break
                    depth -= 1
            if close is not None:
                blocks.append(
                    {
                        "header": idx,
                        "close": close,
                        "length": close - idx + 1,
                        "depth": max_depth,
                        "header_text": code_lines[idx],
                    }
                )
                idx = close + 1
                continue
        idx += 1
    return blocks


def _is_banner(comment: Comment) -> bool:
    core = re.sub(r"^(?:#|//|/\*|\*)+\s*", "", comment.text.strip())
    core = core.rstrip("*/").strip()
    if not core:
        return False
    return bool(BANNER_DECO.match(core))


def _comment_pressure_finding(
    path: Path,
    comments: list[Comment],
    non_blank: int,
) -> Finding | None:
    """Flag files where comments outweigh code (AI padding tell)."""
    if non_blank < BLOAT_COMMENT_MIN_LINES:
        return None
    comment_lines = {c.line for c in comments}
    ratio = len(comment_lines) / non_blank
    if ratio <= BLOAT_COMMENT_RATIO:
        return None
    return Finding(
        path=str(path),
        line=1,
        check="bloat-comment-ratio",
        message=(
            f"{len(comment_lines)}/{non_blank} lines are comments "
            f"({ratio:.0%} - budget {BLOAT_COMMENT_RATIO:.0%})"
        ),
        weight=1,
        advisory=True,
    )


def _banner_cluster_starts(comments: list[Comment]) -> list[int]:
    """First line of each banner cluster (a titled block is one unit)."""
    starts: list[int] = []
    prev = 0
    for c in comments:
        if not _is_banner(c):
            continue
        if not starts or c.line - prev > 2:
            starts.append(c.line)
        prev = c.line
    return starts


def _banner_padding_finding(
    path: Path,
    comments: list[Comment],
    non_blank: int,
) -> Finding | None:
    """Flag bunched banner blocks; spread-out dividers are navigation."""
    clusters = _banner_cluster_starts(comments)
    if len(clusters) < BLOAT_BANNER_MIN:
        return None
    gaps = sorted(b - a for a, b in zip(clusters, clusters[1:]))
    median_gap = gaps[len(gaps) // 2] if gaps else non_blank
    if median_gap >= BLOAT_BANNER_GAP:
        return None
    return Finding(
        path=str(path),
        line=clusters[0],
        check="bloat-banner-comments",
        message=(
            f"{len(clusters)} decorative banner blocks pad the file; "
            "structure should carry the sections"
        ),
        weight=1,
        advisory=True,
    )


def _file_bloat_findings(
    path: Path,
    text: str,
    comments: list[Comment],
    rel: str,
) -> list[Finding]:
    """File-level budgets: length, comment pressure, banner padding."""
    findings: list[Finding] = []
    non_blank = sum(1 for line in text.splitlines() if line.strip())
    if non_blank > BLOAT_FILE_LINES and not is_test_path(rel):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="bloat-long-file",
                message=f"file spans {non_blank} non-blank lines (budget {BLOAT_FILE_LINES})",
                weight=1,
                advisory=True,
            )
        )
    pressure = _comment_pressure_finding(path, comments, non_blank)
    if pressure:
        findings.append(pressure)
    padding = _banner_padding_finding(path, comments, non_blank)
    if padding:
        findings.append(padding)
    return findings


def _worst_block_findings(path: Path, blocks: list[dict]) -> list[Finding]:
    """One finding per rule for the worst function block in the file."""
    findings: list[Finding] = []
    worst_len = max(blocks, key=lambda b: b["length"])
    worst_depth = max(blocks, key=lambda b: b["depth"])
    worst_params = max(blocks, key=lambda b: _param_count(b["header_text"]))
    if worst_len["length"] > GOD_FUNCTION_LINES:
        findings.append(
            Finding(
                path=str(path),
                line=worst_len["header"] + 1,
                check="god-function",
                message=f"function block spans {worst_len['length']} lines",
                weight=1,
                advisory=True,
            )
        )
    if worst_depth["depth"] > MAX_NESTING:
        findings.append(
            Finding(
                path=str(path),
                line=worst_depth["header"] + 1,
                check="deep-nesting",
                message=f"nests more than {MAX_NESTING} levels",
                weight=1,
                advisory=True,
            )
        )
    params = _param_count(worst_params["header_text"])
    if params > BLOAT_MAX_PARAMS:
        findings.append(
            Finding(
                path=str(path),
                line=worst_params["header"] + 1,
                check="bloat-param-count",
                message=f"{params} parameters (budget {BLOAT_MAX_PARAMS})",
                weight=1,
                advisory=True,
            )
        )
    return findings


def check_bloat(
    path: Path,
    text: str,
    family: str,
    rel: str,
    comments: list[Comment],
) -> list[Finding]:
    """File-level and function-level bloat budget checks (all advisory)."""
    findings = _file_bloat_findings(path, text, comments, rel)
    if family == "py":
        return findings
    blocks = (
        _ruby_def_blocks(_code_lines(text, family))
        if family == "ruby"
        else _brace_blocks(_code_lines(text, family))
    )
    if blocks:
        findings.extend(_worst_block_findings(path, blocks))
    return findings
