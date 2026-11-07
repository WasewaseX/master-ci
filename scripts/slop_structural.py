"""Function-block scanners (brace families, Ruby) and the structural budgets."""

from __future__ import annotations

import re

from slop_patterns import _strip_strings

GOD_FUNCTION_LINES = 80  # scanaislop-aligned function budget
MAX_NESTING = 5

# ---------------------------------------------------------------------------
# Bloat budget: the "AI writes 300 lines for 100 lines of work" guards.
# All advisory - bloat is judgment, not a content defect. Budgets are
# deliberately generous so normal, well-factored code never trips them.
# ---------------------------------------------------------------------------
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


def _code_lines(text: str, family: str) -> list[str]:
    """Lines with strings and trailing comments blanked for brace counting."""
    prefixes: tuple[str, ...] = ("//", "#") if family == "php" else ("//",)
    if family == "ruby":
        prefixes = ("#",)
    out: list[str] = []
    for raw in text.splitlines():
        line = _strip_strings(raw)
        for prefix in prefixes:
            pos = line.find(prefix)
            if pos != -1:
                line = line[:pos]
        out.append(line)
    return out


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
