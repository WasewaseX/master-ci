"""String-aware comment blanking and the shared code-line view.

The structural scanners count braces and signatures, not prose: every
block-budget and function-block scanner needs source with comments,
strings and line comments blanked while line structure stays intact.
The blanker is one state machine shared by every family - a ``/*``
inside a string never opens a comment and a quote inside a comment
never opens a string, so braces inside either can no longer distort
the brace bookkeeping. (Split out of slop_structural.py when that
file outgrew the same 400-line budget it enforces on its own source.)
"""

from __future__ import annotations

import re
from typing import ClassVar

from slop_patterns import _strip_strings

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
    # (JS template literals, Go raw strings) span lines. ClassVar is the
    # declared-intentional exit the reliability dimension honors: every
    # instance shares this read-only dispatch table by design.
    _TERMS: ClassVar[dict[str, tuple[str, bool]]] = {
        "squote": ("'", True),
        "dquote": ('"', True),
        "backtick": ("`", False),
    }

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
