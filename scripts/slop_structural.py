"""Function-block scanners (brace families, Ruby) and the structural budgets.

The string-aware comment blanking and the shared code-line view live in
slop_code_lines.py - split out when this file outgrew the same 400-line
budget it enforces on its own source. What stayed here is the budget
law (the BLOAT_* constants every consumer imports), the brace and
signature bookkeeping, and the function-block extraction both slop_bloat
and slop_lcom4 build on.
"""

from __future__ import annotations

import re

from slop_code_lines import _code_lines

GOD_FUNCTION_LINES = 80  # scanaislop-aligned function budget
MAX_NESTING = 5

# Bloat budget: the "AI writes 300 lines for 100 lines of work" guards.
# All advisory - bloat is judgment, not a content defect; budgets are
# deliberately generous so well-factored code never trips them.
BLOAT_FILE_LINES = 400  # non-blank lines before a source file is flagged
BLOAT_MAX_PARAMS = 6  # parameters before a signature is unreadable
BLOAT_COMMENT_RATIO = 0.5  # comment share of non-blank lines
BLOAT_COMMENT_MIN_LINES = 60  # only judge the ratio in substantial files
BLOAT_BANNER_MIN = 4  # decorative banner fences before judging their density
BLOAT_BANNER_GAP = 20  # median gap under which banners are padding, not navigation
MAX_PARAMS = BLOAT_MAX_PARAMS

# Lines that may open a function block in a brace family. Conservative:
# anything starting with a control, expression or declaration keyword is
# out; what remains must carry a parameter list.
BLOAT_HEADER_EXCLUDE = re.compile(
    r"^\s*(?:if|else|for|while|switch|catch|do|try|with|match|case|default|"
    r"namespace|class|struct|enum|union|interface|impl|trait|mod|extension|"
    r"protocol|object|return|throw|new|import|package|using|require|include|"
    r"typedef|operator|await|yield|print|echo|puts|elsif|elif|when|foreach|"
    r"synchronized|loop)\b"
)
BLOAT_KW_FUNCS = re.compile(r"\b(?:function|fn|func|def|sub)\b")
BLOAT_METHOD_STYLE = re.compile(
    r"^\s*(?:@[\w.]+\s+)*(?:(?:public|private|protected|internal|static|"
    r"final|abstract|override|virtual|sealed|async|unsafe|extern|inline|"
    r"suspend|explicit|implicit|const|pub)\s+)*[\w<>\[\],:. ]*?\w+\s*\("
)
BLOAT_ARROW_STYLE = re.compile(r"=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{?\s*$")
BANNER_DECO = re.compile(r"^[=\-_~+ ]{6,}$")
RUBY_OPENER = re.compile(
    r"^\s*(?:def|if|unless|while|until|case|begin|class|module)\b|do\s*(?:\|[^|]*\|)?\s*$"
)
RUBY_CLOSER = re.compile(r"^\s*end\b")


def _brace_depths(code_lines: list[str]) -> tuple[list[int], list[int], list[int]]:
    """Per-line brace bookkeeping.

    Returns (depth_end, max_depth, max_control_depth) where max_depth is the
    highest brace depth reached on the line and max_control_depth counts
    only braces that open control blocks or closures (`) {`, `else {`,
    `do {`, `try {`) - the nesting a reader actually has to track.
    """
    n = len(code_lines)
    depth_end = [0] * n
    max_depth = [0] * n
    max_control = [0] * n
    depth = 0
    for idx, code in enumerate(code_lines):
        line_max = depth
        line_ctl = depth
        for ch in code:
            if ch == "{":
                depth += 1
                line_max = max(line_max, depth)
            elif ch == "}":
                depth = max(0, depth - 1)
        tail = code.rstrip()
        if tail.endswith("{"):
            head = tail[:-1].rstrip()
            if head.endswith(")") or re.search(r"\b(?:else|do|try)\s*$", head):
                line_ctl = max(line_ctl, depth)
        depth_end[idx] = depth
        max_depth[idx] = line_max
        max_control[idx] = line_ctl
    return depth_end, max_depth, max_control


def _last_param_group(header: str) -> str | None:
    """The last balanced (...) group of a signature line."""
    best: str | None = None
    start = -1
    nested = 0
    for pos, ch in enumerate(header):
        if ch == "(":
            if nested == 0:
                start = pos
            nested += 1
        elif ch == ")" and nested > 0:
            nested -= 1
            if nested == 0 and start != -1:
                best = header[start + 1 : pos]
                start = -1
    return best


# --- function-block scanners ----------------------------------------------
# Where does a function start and end, in the line-scanner view: the
# fallback path for families the parsers do not cover (SFC markup) and
# the nesting source everywhere (the parser does not carry that fact).


def _param_count(header: str) -> int:
    """Top-level parameters of a signature line.

    Commas only split parameters at bracket depth zero AND angle depth
    zero, so generic type arguments (`Map<String, Pair<A, B>>`) do not
    inflate the count.
    """
    group = _last_param_group(header)
    if group is None or not group.strip():
        return 0
    depth = 0
    angle = 0
    params = 1
    prev = ""
    for ch in group:
        depth, angle, params, prev = _scan_param_char(ch, depth, angle, params, prev)
    return params


def _scan_param_char(
    ch: str, depth: int, angle: int, params: int, prev: str
) -> tuple[int, int, int, str]:
    """One character of the signature walk.

    A `<` opens a generic only when glued to an identifier or a closing
    `>` (nested generics), never a bare comparison like `a < b`; a `>`
    closes only when a generic is open and the previous char is not `-`
    (return arrows).
    """
    if ch in "([{":
        depth += 1
    elif ch in ")]}":
        depth = max(0, depth - 1)
    elif ch == "<" and angle == 0 and (prev.isalnum() or prev in "_>"):
        angle = 1
    elif ch == ">" and angle > 0 and prev != "-":
        angle = 0
    elif ch == "," and depth == 0 and angle == 0:
        params += 1
    return depth, angle, params, ch


def _is_header_line(code: str) -> bool:
    """A signature line: parenthesized and function-shaped."""
    if BLOAT_HEADER_EXCLUDE.match(code.strip()):
        return False
    if "(" not in code or ")" not in code:
        return False
    return bool(
        BLOAT_KW_FUNCS.search(code)
        or BLOAT_METHOD_STYLE.match(code)
        or BLOAT_ARROW_STYLE.search(code)
    )


def _next_line_opens(code_lines: list[str], idx: int) -> bool:
    """True when the next non-blank line is an Allman-style `{`."""
    for nxt in code_lines[idx + 1 : idx + 3]:
        stripped = nxt.strip()
        if stripped:
            return stripped.startswith("{")
    return False


def _header_openers(code_lines: list[str]) -> list[tuple[int, str]]:
    """Lines that may open a function block: (index, header_text).

    A header carries its own `{` or is followed by a line whose first
    token is `{` (Allman style). Control, expression and declaration
    keywords never open blocks.
    """
    headers: list[tuple[int, str]] = []
    for idx, code in enumerate(code_lines):
        if _is_header_line(code) and ("{" in code or _next_line_opens(code_lines, idx)):
            headers.append((idx, code))
    return headers


def _open_line(code_lines: list[str], idx: int) -> int | None:
    """The line whose `{` opens this header's block, if any."""
    if "{" in code_lines[idx]:
        return idx
    for nxt in range(idx + 1, min(idx + 3, len(code_lines))):
        if code_lines[nxt].strip().startswith("{"):
            return nxt
    return None


def _close_line(depth_end: list[int], open_idx: int) -> int | None:
    """The first line whose brace depth falls back below the opener."""
    open_depth = max(0, depth_end[open_idx] - 1)
    for j in range(open_idx, len(depth_end)):
        if depth_end[j] <= open_depth:
            return j
    return None


def _brace_blocks(code_lines: list[str]) -> list[dict]:
    """Function-ish brace blocks with length and control-nesting depth."""
    depth_end, max_depth, max_control = _brace_depths(code_lines)
    blocks: list[dict] = []
    for idx, header in _header_openers(code_lines):
        open_idx = _open_line(code_lines, idx)
        if open_idx is None:
            continue
        close_idx = _close_line(depth_end, open_idx)
        if close_idx is None:
            continue
        open_depth = max(0, depth_end[open_idx] - 1)
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


def _ruby_def_close(code_lines: list[str], idx: int) -> tuple[int, int] | None:
    """(close_idx, max_depth) for the def block opening at idx."""
    depth = 0
    max_depth = 0
    for j in range(idx, len(code_lines)):
        if j > idx and RUBY_OPENER.match(code_lines[j]):
            depth += 1
            max_depth = max(max_depth, depth)
        if RUBY_CLOSER.match(code_lines[j]):
            if depth == 0:
                return j, max_depth
            depth -= 1
    return None


def _ruby_def_blocks(code_lines: list[str]) -> list[dict]:
    """def/end blocks with length and nesting via keyword pairing."""
    blocks: list[dict] = []
    idx = 0
    while idx < len(code_lines):
        if not re.match(r"^\s*def\s", code_lines[idx]):
            idx += 1
            continue
        found = _ruby_def_close(code_lines, idx)
        if found is None:
            idx += 1
            continue
        close, max_depth = found
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
    return blocks


def _family_blocks(text: str, family: str) -> list[dict]:
    """Function blocks for one family: def/end pairing or brace bookkeeping."""
    if family == "ruby":
        return _ruby_def_blocks(_code_lines(text, family))
    return _brace_blocks(_code_lines(text, family))
