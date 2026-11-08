"""Function-block scanners (brace families, Ruby) and the structural budgets."""

from __future__ import annotations

import re

from slop_patterns import _strip_strings

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


# Families whose line comments start with `//`. PHP also allows `#`
# (but `#[...]` attributes are code), shell only allows `#`.
SLASH_LINE_FAMILIES = frozenset(
    {"js", "go", "jvm", "csharp", "cpp", "php", "rust", "swift", "dart"}
)
# Rust and Swift block comments nest; C-family ones do not.
NESTED_BLOCK_FAMILIES = frozenset({"rust", "swift"})


class _CommentBlanker:
    """String-aware blanker for block and line comments.

    Strings and comments are tracked in one pass, so a ``/*`` inside a
    string never opens a comment and a quote inside a comment never
    opens a string. Braces inside comments can no longer distort the
    brace bookkeeping. Newlines are preserved so line indices stay
    valid for findings. One method per scan state, dispatched by dict.
    """

    # string state -> (terminator, ends-at-line-end); backtick strings
    # (JS template literals, Go raw strings) span lines
    _TERMS = {"squote": ("'", True), "dquote": ('"', True), "backtick": ("`", False)}

    def __init__(self, text: str, family: str) -> None:
        self.text = text
        self.n = len(text)
        self.out: list[str] = []
        self.i = 0
        self.state = "code"
        self.block_depth = 0
        self.line_openers: tuple[str, ...] = ("//",) if family in SLASH_LINE_FAMILIES else ()
        if family == "php":
            self.line_openers = ("//", "#")
        elif family == "sh":
            self.line_openers = ("#",)
        self.block_opener = "/*" if family in SLASH_LINE_FAMILIES else ""
        self.nested = family in NESTED_BLOCK_FAMILIES
        self.php = family == "php"
        self._dispatch = {
            "code": self._code_char,
            "block": self._block_char,
            "line": self._line_char,
            "squote": self._string_char,
            "dquote": self._string_char,
            "backtick": self._string_char,
        }

    def run(self) -> str:
        while self.i < self.n:
            self._dispatch[self.state]()
        return "".join(self.out)

    def _peek(self) -> tuple[str, str]:
        nxt = self.text[self.i + 1] if self.i + 1 < self.n else ""
        return self.text[self.i], nxt

    def _code_char(self) -> None:
        ch, nxt = self._peek()
        if self.block_opener and ch == "/" and nxt == "*":
            self.state = "block"
            self.block_depth = 0
            self.out.append("  ")
            self.i += 2
            return
        opener = self._at_line_open(ch, nxt)
        if opener:
            self.state = "line"
            self.out.append(" " * len(opener))
            self.i += len(opener)
            return
        self._punct_char(ch)

    def _at_line_open(self, ch: str, nxt: str) -> str:
        """The line-comment opener at this position, or an empty string."""
        if ch == "/" and nxt == "/" and "//" in self.line_openers:
            return "//"
        if ch == "#" and "#" in self.line_openers and not (self.php and nxt == "["):
            return "#"
        return ""

    def _punct_char(self, ch: str) -> None:
        if ch == '"':
            self.state = "dquote"
        elif ch == "'":
            self.state = "squote"
        elif ch == "`":
            self.state = "backtick"
        self.out.append(ch)
        self.i += 1

    def _block_char(self) -> None:
        ch, nxt = self._peek()
        if self.nested and ch == "/" and nxt == "*":
            self.block_depth += 1
            self.out.append("  ")
            self.i += 2
            return
        if ch == "*" and nxt == "/":
            if not self.nested or self.block_depth == 0:
                self.state = "code"
                self.block_depth = 0
            else:
                self.block_depth -= 1
            self.out.append("  ")
            self.i += 2
            return
        self.out.append(ch if ch == "\n" else " ")
        self.i += 1

    def _line_char(self) -> None:
        ch = self.text[self.i]
        self.out.append(ch if ch == "\n" else " ")
        if ch == "\n":
            self.state = "code"
        self.i += 1

    def _string_char(self) -> None:
        ch, nxt = self._peek()
        self.out.append(ch)
        if ch == "\\" and nxt:
            self.out.append(nxt)
            self.i += 2
            return
        term, eol_ends = self._TERMS[self.state]
        if ch == term or (eol_ends and ch == "\n"):
            self.state = "code"
        self.i += 1


def _blank_block_comments(text: str, family: str) -> str:
    """Blank block and line comments with string-aware tokenization."""
    if family == "ruby":
        return _blank_ruby_pods(text)
    return _CommentBlanker(text, family).run()


def _blank_ruby_pods(text: str) -> str:
    """Blank `=begin`/`=end` comment pods, preserving line structure."""
    out: list[str] = []
    in_pod = False
    for line in text.splitlines():
        if in_pod:
            out.append("" if not line.strip() else " ")
            if re.match(r"^=end\b", line):
                in_pod = False
        elif re.match(r"^=begin\b", line):
            in_pod = True
            out.append(" ")
        else:
            out.append(line)
    joined = "\n".join(out)
    return joined + "\n" if text.endswith("\n") else joined


def _code_lines(text: str, family: str) -> list[str]:
    """Lines with block comments, strings and line comments blanked for brace counting."""
    blanked = _blank_block_comments(text, family)
    prefixes: tuple[str, ...] = ("//", "#") if family == "php" else ("//",)
    if family in ("ruby", "sh"):
        prefixes = ("#",)
    out: list[str] = []
    for raw in blanked.splitlines():
        line = _strip_strings(raw)
        for prefix in prefixes:
            pos = line.find(prefix)
            if pos != -1:
                line = line[:pos]
                break
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
