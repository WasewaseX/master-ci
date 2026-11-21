"""The length-preserving span mask shared by the shape-oracle scans.

Scans that must map a regex match back to a real line cannot read over
``_strip_strings`` (its single-space replacement moves every offset
after the match) and cannot read raw text (a string or comment
mentioning the shape is not code). The mask blanks string, comment and
regular-expression literal contents in place - quotes, slashes and
newlines kept - so the masked copy has the same length, and therefore
the same offsets and the same lines, as the original. ``quotes``
selects the string forms (all three for JS/TS, only the double quote
for JSONC); ``keep_strings`` flips the string role for a JSONC reader,
which needs the literals intact - they are the data - while the walk
still passes through each one, so a ``//`` inside a string value never
starts a comment.

A ``/`` the walk can prove opens a regular expression is consumed as
one literal before any quote inside it can open a string: the
apostrophe in ``/'/g`` would otherwise blank everything up to the next
quote - the second half of the file, and any check reading it, gone.
The position heuristic reads the masked copy's last significant
character (operators, openers and the divider keywords start a regex;
identifiers and closers divide), and a ``/`` that never closes on its
line falls back to plain division - a misdetection blanks nothing.
"""

from __future__ import annotations

# Words after which a ``/`` opens a regex literal, never a division.
_REGEX_KEYWORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
    }
)

# Characters after which a ``/`` always opens a regex literal.
_REGEX_OPENERS = frozenset("(,=:[!&|?{};+-*/%<>=~^")


def mask_span_forms(text: str, quotes: str = "\"'`", keep_strings: bool = False) -> str:
    """String, comment and regex contents blanked, every offset preserved."""
    out = list(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char in quotes:
            index = _advance_string(text, out, index, quotes, keep_strings)
        elif text[index : index + 2] in ("//", "/*"):
            index = _advance_comment(text, out, index)
        elif char == "/":
            index = _advance_regex(text, out, index)
        else:
            index += 1
    return "".join(out)


def _advance_string(text: str, out: list[str], index: int, quotes: str, keep_strings: bool) -> int:
    """The walk's step over one quote: kept whole for a JSONC reader."""
    if keep_strings:
        return _skip_quoted(text, index)
    return _blank_quoted(text, out, index, quotes)


def _advance_comment(text: str, out: list[str], index: int) -> int:
    """The walk's step over ``//`` or ``/*`` - the contents blanked."""
    if text[index + 1] == "/":
        return _blank_line_comment(text, out, index)
    return _blank_block_comment(text, out, index)


def _advance_regex(text: str, out: list[str], index: int) -> int:
    """The walk's step over a ``/``: literal, or plain division.

    A ``/`` the position heuristic reads as a regex opener is consumed
    whole - its body blanked so no quote inside it can open a string -
    and a ``/`` that never closes on its line falls back to division,
    which blanks nothing.
    """
    if not _regex_position(out, index):
        return index + 1
    end = _regex_end(text, index)
    if end is None:
        return index + 1
    _blank_range(out, index + 1, end)
    return end


def _regex_position(out: list[str], index: int) -> bool:
    """True when the ``/`` at ``index`` can open a regex literal.

    The judgment reads the masked copy - string, comment and regex
    contents before ``index`` are already blanks, so the last
    significant character is the code the ``/`` actually follows:
    operators and openers start a literal, a divider keyword starts
    one, an identifier or a closer divides. Ambiguity divides - the
    quiet direction is a regex missed, never code blanked.
    """
    position = index - 1
    while position >= 0 and out[position] in " \t\r\n":
        position -= 1
    if position < 0:
        return True
    char = out[position]
    if char in _REGEX_OPENERS:
        return True
    if char.isalnum() or char in "_$":
        start = position
        while start > 0 and (out[start - 1].isalnum() or out[start - 1] in "_$"):
            start -= 1
        return "".join(out[start : position + 1]).lower() in _REGEX_KEYWORDS
    return False


def _regex_end(text: str, start: int) -> int | None:
    """The index just past a closing ``/`` (before its flags), or None.

    Character classes hold their own ``/`` unescaped, escapes hide
    one, and a literal never spans lines - a ``/`` that hits a newline
    first was a division, and the caller leaves it alone.
    """
    index = start + 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return None
        if in_class:
            if char == "]":
                in_class = False
        elif char == "/":
            return index + 1
        elif char == "[":
            in_class = True
        index += 1
    return None


def string_spans(masked: str) -> list[tuple[int, int]]:
    """(start, end) spans of the string literals in a keep-strings mask.

    Callers pass the ``keep_strings=True`` masked copy: comments and
    regex bodies are already blanks there, so every quote that remains
    belongs to a real literal - a commented-out ``// import x from
    'pkg'`` carries no quotes at all. The spans let a scanner tell a
    specifier whose quote opens a top-level literal (real code) from
    the same text quoted inside a longer string or template (example
    prose, not a declaration). Escapes ride along; a quote that hits a
    newline before its close is not a literal, and a backtick template
    spans lines - interpolations included - to its close.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(masked):
        char = masked[index]
        if char in "\"'":
            end = _quoted_end(masked, index)
            if end is None:
                index += 1
            else:
                spans.append((index, end))
                index = end
        elif char == "`":
            end = _template_end(masked, index)
            spans.append((index, end))
            index = end
        else:
            index += 1
    return spans


def _quoted_end(masked: str, start: int) -> int | None:
    """The index just past the closing quote, None when the line ends first."""
    quote = masked[start]
    index = start + 1
    while index < len(masked):
        char = masked[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        if char == "\n":
            return None
        index += 1
    return None


def _template_end(masked: str, start: int) -> int:
    """The index just past the template's closing backtick (or the end)."""
    index = start + 1
    while index < len(masked):
        char = masked[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            return index + 1
        index += 1
    return len(masked)


def _skip_quoted(text: str, index: int) -> int:
    """The index just past the literal; its contents left alone."""
    quote = text[index]
    end = index + 1
    while end < len(text):
        char = text[end]
        if char == "\\":
            end += 2
            continue
        if char == quote:
            break
        end += 1
    return end + 1


def _blank_quoted(text: str, out: list[str], index: int, quotes: str) -> int:
    """Blank one literal's contents; the index just past its close."""
    quote = text[index]
    end = index + 1
    while end < len(text):
        char = text[end]
        if char == "\\":
            end += 2
            continue
        if char == quote:
            break
        if char != "\n":
            out[end] = " "
        end += 1
    return end + 1


def _blank_line_comment(text: str, out: list[str], index: int) -> int:
    """Blank to the line's end; the newline stays."""
    end = text.find("\n", index)
    end = len(text) if end < 0 else end
    _blank_range(out, index, end)
    return end


def _blank_block_comment(text: str, out: list[str], index: int) -> int:
    """Blank through the closer (or the end); the closer blanked too."""
    end = text.find("*/", index + 2)
    end = len(text) if end < 0 else end + 2
    _blank_range(out, index, end)
    return end


def _blank_range(out: list[str], start: int, end: int) -> None:
    """Spaces over [start, end), newlines kept so lines never move."""
    for position in range(start, min(end, len(out))):
        if out[position] != "\n":
            out[position] = " "
