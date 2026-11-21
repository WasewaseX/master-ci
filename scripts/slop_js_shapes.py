"""The js family's shape-oracle checks: no-new-array and set-state-in-effect.

scanaislop round-19 adoption: the two JS judgment calls the user-facing
scans kept surfacing that the engine had no oracle for - unicorn's
``no-new-array`` and React's ``set-state-in-effect``. Both advisory:
the shapes are judgment calls, and the failure direction is the quiet
one (a shape the scanner cannot see confidently stays out). Both scan
a length-preserving masked copy - string and comment contents blanked,
every offset intact - so a mention of the shape in prose is never a
finding and the reported line is the real one.
"""

from __future__ import annotations

import re
from pathlib import Path

from slop_model import Finding, _finding
from slop_span_mask import mask_span_forms

_NO_NEW_ARRAY = re.compile(r"\bnew\s+Array\s*\(([^()]*)\)")

_EFFECT_CALL = re.compile(r"\b(?:useEffect|useLayoutEffect)\s*\(")
_SET_STATE_CALL = re.compile(r"\bset[A-Z]\w*\s*\(")

# Bare names the setter shape matches that are not React state setters:
# the timer trio a synchronous-scheduling effect legitimately calls.
_NON_HOOK_SETTERS = frozenset({"setTimeout", "setInterval", "setImmediate"})


def check_no_new_array(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """``new Array(x)`` with one argument builds a hole-y array (advisory).

    The single-argument call is the trap: ``new Array(5)`` reserves a
    length, ``new Array('5')`` builds one element, and ``new Array(1,
    2)`` is the two-element array the writer almost always meant. The
    scan reads a length-preserving masked copy (string and comment
    contents blanked, every offset intact), so a message that mentions
    the shape is not a finding and the reported line is the real one;
    a single-argument call with a nested call the comma could belong
    to, and the zero- and two-argument calls, stay out.
    """
    if family != "js":
        return []
    findings: list[Finding] = []
    for match in _NO_NEW_ARRAY.finditer(mask_span_forms(text)):
        argument = match.group(1).strip()
        if not argument or "," in argument:
            continue
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "no-new-array",
                (
                    "new Array(singleArgument) is the length trap - build arrays "
                    "with Array.from({ length: n }), a literal or .fill()"
                ),
                1,
            )
        )
    return findings[:5]


_EFFECT_CALL = re.compile(r"\b(?:useEffect|useLayoutEffect)\s*\(")
_SET_STATE_CALL = re.compile(r"\bset[A-Z]\w*\s*\(")

# Bare names the setter shape matches that are not React state setters:
# the timer trio a synchronous-scheduling effect legitimately calls.
_NON_HOOK_SETTERS = frozenset({"setTimeout", "setInterval", "setImmediate"})


_NO_NEW_ARRAY = re.compile(r"\bnew\s+Array\s*\(([^()]*)\)")

_EFFECT_CALL = re.compile(r"\b(?:useEffect|useLayoutEffect)\s*\(")
_SET_STATE_CALL = re.compile(r"\bset[A-Z]\w*\s*\(")

# Bare names the setter shape matches that are not React state setters:
# the timer trio a synchronous-scheduling effect legitimately calls.
_NON_HOOK_SETTERS = frozenset({"setTimeout", "setInterval", "setImmediate"})


def check_set_state_in_effect(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """setState called synchronously inside an effect body (advisory).

    React's compiler rule: a synchronous setState in
    ``useEffect``/``useLayoutEffect`` renders, effects run, the set
    renders again - cascading renders the component did not need.
    Derived state belongs in render; async assignment belongs behind a
    callback. The scan stays at the shape it can see confidently: the
    effect call's first argument, read over the masked copy so a
    string or a comment is never a call, flagged at the body's own
    statement depth. Calls nested one function deeper - a ``then``, a
    listener, a timer - sit behind a paren and run after the render,
    which is the rule's whole point, so they stay out; a body the
    balance cannot close is skipped, never guessed at.
    """
    if family != "js":
        return []
    masked = mask_span_forms(text)
    findings: list[Finding] = []
    for start, end in _effect_first_arg_spans(masked):
        for offset in _flagged_set_state_offsets(masked[start:end], start):
            findings.append(
                _finding(
                    path,
                    text,
                    offset,
                    "set-state-in-effect",
                    (
                        "calling setState synchronously within an effect can "
                        "trigger cascading renders - derive it in render or "
                        "set it behind a callback"
                    ),
                    1,
                )
            )
    return findings[:5]


def _effect_first_arg_spans(masked: str) -> list[tuple[int, int]]:
    """(start, end) spans of each effect call's first argument.

    The masked copy carries no string or comment contents, so plain
    paren depth decides: the first argument ends at the first comma at
    the call's own depth (the deps array follows it) or at the closing
    paren. An effect the balance cannot close contributes nothing.
    """
    spans: list[tuple[int, int]] = []
    for match in _EFFECT_CALL.finditer(masked):
        end = _first_arg_end(masked, match.end() - 1)
        if end > match.end():
            spans.append((match.end(), end))
    return spans


def _first_arg_end(masked: str, open_paren: int) -> int:
    """The index where the call's first argument ends, -1 when unbalanced."""
    depth = 0
    index = open_paren
    while index < len(masked):
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        elif char == "," and depth == 1:
            return index
        index += 1
    return -1


# One depth counter per bracket kind; the walker reads only these.
_OPEN = {"(": "paren", "[": "bracket", "{": "brace"}
_CLOSE = {")": "paren", "]": "bracket", "}": "brace"}


# One depth counter per bracket kind; the walker reads only these.
_OPEN = {"(": "paren", "[": "bracket", "{": "brace"}
_CLOSE = {")": "paren", "]": "bracket", "}": "brace"}


def _flagged_set_state_offsets(arg: str, base: int) -> list[int]:
    """Offsets of setState calls made synchronously in the effect body.

    A block body (``() => { ... }``) flags calls at the body's own
    statement depth - inside its braces, outside any nested call or
    bracket; a concise body (``() => setX(1)``) flags calls at depth
    zero of the whole argument. A call behind a paren (``.then``, a
    timer), behind a bracket, deeper braces, past a nested ``=>`` (a
    listener body runs when the event fires, not during the render),
    or on a method receiver (``store.setState``, ``el.setAttribute`` -
    a hook setter is a bare identifier) stays out - the quiet
    direction the advisory prefers. The timer trio the setter shape
    also matches are named and exempt outright.
    """
    body_start, target_brace, block = _effect_body_span(arg)
    depths = {"paren": 0, "bracket": 0, "brace": 1 if block else 0}
    flagged: list[int] = []
    crossed_arrow = False
    index = body_start + 1 if block else body_start
    while index < len(arg):
        char = arg[index]
        kind = _OPEN.get(char) or _CLOSE.get(char)
        if kind is not None:
            depths[kind] += 1 if char in _OPEN else -1
        elif char == "=" and arg[index + 1 : index + 2] == ">":
            crossed_arrow = True
            index += 1
        elif char == "s":
            setter = _sync_setter(arg, index, depths, target_brace, crossed_arrow)
            if setter:
                flagged.append(base + index)
                # Re-enter at the call's own '(' so the depth walk stays
                # balanced - a second statement in the same body must
                # still see paren == 0.
                index += len(setter)
                continue
        index += 1
    return flagged


def _effect_body_span(arg: str) -> tuple[int, int, bool]:
    """(body start, target brace depth, block?) of the effect body."""
    arrow = arg.find("=>")
    body = arrow + 2 if arrow >= 0 else 0
    while body < len(arg) and arg[body] in " \t\r\n":
        body += 1
    block = body < len(arg) and arg[body] == "{"
    return body, 1 if block else 0, block


def _sync_setter(
    arg: str, index: int, depths: dict[str, int], target_brace: int, crossed_arrow: bool
) -> str | None:
    """The setter name when ``arg[index:]`` is a synchronous setState call.

    The gates, each the quiet direction: statement depth only (not
    behind a paren or bracket, at the body's own brace level), no
    nested arrow crossed, a bare identifier (not a method receiver),
    and not one of the timer trio the shape also matches.
    """
    if crossed_arrow or depths["brace"] != target_brace:
        return None
    if depths["paren"] or depths["bracket"]:
        return None
    if index and arg[index - 1] in "._$?":
        return None
    match = _SET_STATE_CALL.match(arg, index)
    if not match:
        return None
    name = match.group(0).split("(")[0].strip()
    return None if name in _NON_HOOK_SETTERS else name
