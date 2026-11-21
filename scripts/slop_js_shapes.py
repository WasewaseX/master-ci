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

# One depth counter per bracket kind; the walkers read only these.
_OPEN = {"(": "paren", "[": "bracket", "{": "brace"}
_CLOSE = {")": "paren", "]": "bracket", "}": "brace"}


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
    """The index where the call's first argument ends, -1 when unbalanced.

    A comma ends the first argument only at the call's own depth -
    paren one, no bracket or brace open: the comma inside a deps
    array, a body's array literal (``[1, 2]``) or an object sits
    behind one of those pairs and is not a separator. An argument the
    balance cannot close contributes nothing.
    """
    depths = {"paren": 1, "bracket": 0, "brace": 0}
    index = open_paren + 1
    while index < len(masked):
        char = masked[index]
        kind = _OPEN.get(char)
        if kind is not None:
            depths[kind] += 1
        elif char in _CLOSE:
            kind = _CLOSE[char]
            depths[kind] -= 1
            if kind == "paren" and depths["paren"] == 0:
                return index
        elif char == "," and depths["paren"] == 1 and not depths["bracket"] and not depths["brace"]:
            return index
        index += 1
    return -1


def _flagged_set_state_offsets(arg: str, base: int) -> list[int]:
    """Offsets of setState calls made synchronously in the effect body.

    A block body (``() => { ... }``) flags calls at the body's own
    statement depth - inside its braces, outside any nested call or
    bracket; a concise body (``() => setX(1)``) flags calls at depth
    zero of the whole argument. A call behind a paren (``.then``, a
    timer), behind a bracket, deeper braces, inside a nested arrow's
    body (a listener body runs when the event fires, not during the
    render), or on a method receiver (``store.setState``,
    ``el.setAttribute`` - a hook setter is a bare identifier) stays
    out - the quiet direction the advisory prefers. The timer trio the
    setter shape also matches are named and exempt outright. The
    arrow's exclusion ends where the arrow's body ends - a concise
    body at the statement's ``;`` or line end, a block body at its
    closing brace - so a later synchronous update is still the
    finding, not a casualty of an earlier callback.
    """
    body_start, target_brace, block = _effect_body_span(arg)
    depths = {"paren": 0, "bracket": 0, "brace": 1 if block else 0}
    flagged: list[int] = []
    arrows: list[list] = []
    index = body_start + 1 if block else body_start
    while index < len(arg):
        char = arg[index]
        if char not in " \t\r\n":
            _start_arrow_bodies(arrows)
        nxt, hit = _set_state_step(arg, index, char, depths, arrows, target_brace)
        if hit >= 0:
            flagged.append(base + hit)
        index = nxt
    return flagged


def _start_arrow_bodies(arrows: list[list]) -> None:
    """The first character since each arrow - its body has begun."""
    for scope in arrows:
        scope[4] = True


def _set_state_step(
    arg: str,
    index: int,
    char: str,
    depths: dict[str, int],
    arrows: list[list],
    target_brace: int,
) -> tuple[int, int]:
    """One character's walk step; (next index, flagged start or -1).

    Brackets count their depth, a ``=>`` opens an exclusion scope, a
    statement boundary ends the scopes it can, and a ``set`` may name
    a synchronous setter - the walk re-enters past the call so the
    balance holds for the body's next statement.
    """
    kind = _OPEN.get(char)
    if kind is not None:
        depths[kind] += 1
        return index + 1, -1
    if char in _CLOSE:
        depths[_CLOSE[char]] -= 1
        arrows[:] = _live_arrows(arrows, depths, char)
        return index + 1, -1
    if char == "=" and arg[index + 1 : index + 2] == ">":
        arrows.append(_arrow_scope(arg, index, depths))
        return index + 2, -1
    if char in (";", "\n"):
        arrows[:] = _ended_arrows(arrows, depths, char)
        return index + 1, -1
    setter = _sync_setter(arg, index, depths, target_brace, bool(arrows))
    if setter:
        return index + len(setter), index
    return index + 1, -1


def _arrow_scope(arg: str, arrow_index: int, depths: dict[str, int]) -> list:
    """One nested arrow's exclusion scope.

    ``[paren, bracket, brace, block?, started?]`` at the ``=>``:
    ``block`` is the ``() => {`` shape - its body ends at its own
    closing brace, so the closer ends the scope; a concise body ends
    at a statement boundary. ``started`` records that the body's first
    character appeared - an arrow the newline directly follows has not
    begun, and its body continues on the next line.
    """
    peek = arrow_index + 2
    while peek < len(arg) and arg[peek] in " \t\r\n":
        peek += 1
    return [
        depths["paren"],
        depths["bracket"],
        depths["brace"],
        peek < len(arg) and arg[peek] == "{",
        False,
    ]


def _live_arrows(arrows: list[list], depths: dict[str, int], closer: str) -> list[list]:
    """The scopes still alive after one closer.

    A depth dropped below a scope's own leaves its body - that scope
    ends. A ``}`` back at a block scope's own depth closes that
    scope's body, so the exclusion ends with it.
    """
    return [
        scope
        for scope in arrows
        if depths["paren"] >= scope[0]
        and depths["bracket"] >= scope[1]
        and depths["brace"] >= scope[2]
        and not (
            closer == "}"
            and scope[3]
            and depths["paren"] == scope[0]
            and depths["bracket"] == scope[1]
            and depths["brace"] == scope[2]
        )
    ]


def _ended_arrows(arrows: list[list], depths: dict[str, int], char: str) -> list[list]:
    """The scopes a statement boundary ends, the others kept.

    A ``;`` at a scope's own depth ends its expression; a newline ends
    a concise body whose expression has started. A block body keeps
    spanning lines, and an arrow the newline directly follows has not
    begun yet - its body continues on the next line.
    """
    live: list[list] = []
    for scope in arrows:
        at_depth = (
            depths["paren"] == scope[0]
            and depths["bracket"] == scope[1]
            and depths["brace"] == scope[2]
        )
        if at_depth and (char == ";" or (not scope[3] and scope[4])):
            continue
        live.append(scope)
    return live


def _effect_body_span(arg: str) -> tuple[int, int, bool]:
    """(body start, target brace depth, block?) of the effect body."""
    arrow = arg.find("=>")
    body = arrow + 2 if arrow >= 0 else 0
    while body < len(arg) and arg[body] in " \t\r\n":
        body += 1
    block = body < len(arg) and arg[body] == "{"
    return body, 1 if block else 0, block


def _sync_setter(
    arg: str, index: int, depths: dict[str, int], target_brace: int, in_arrow: bool
) -> str | None:
    """The setter name when ``arg[index:]`` is a synchronous setState call.

    The gates, each the quiet direction: statement depth only (not
    behind a paren or bracket, at the body's own brace level), no
    nested arrow body enclosing (a listener body runs when the event
    fires), a bare identifier (not a method receiver), and not one of
    the timer trio the shape also matches.
    """
    if in_arrow or depths["brace"] != target_brace:
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
