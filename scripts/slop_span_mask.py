"""The length-preserving span mask shared by the shape-oracle scans.

Scans that must map a regex match back to a real line cannot read over
``_strip_strings`` (its single-space replacement moves every offset
after the match) and cannot read raw text (a string or comment
mentioning the shape is not code). The mask blanks string and comment
contents in place - quotes and newlines kept - so the masked copy has
the same length, and therefore the same offsets and the same lines, as
the original. ``quotes`` selects the string forms (all three for
JS/TS, only the double quote for JSONC); ``keep_strings`` flips the
string role for a JSONC reader, which needs the literals intact - they
are the data - while the walk still passes through each one, so a
``//`` inside a string value never starts a comment.
"""

from __future__ import annotations


def mask_span_forms(text: str, quotes: str = "\"'`", keep_strings: bool = False) -> str:
    """String and comment contents blanked, every offset preserved."""
    out = list(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char in quotes:
            if keep_strings:
                index = _skip_quoted(text, index)
            else:
                index = _blank_quoted(text, out, index, quotes)
        elif text[index : index + 2] == "//":
            index = _blank_line_comment(text, out, index)
        elif text[index : index + 2] == "/*":
            index = _blank_block_comment(text, out, index)
        else:
            index += 1
    return "".join(out)


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
