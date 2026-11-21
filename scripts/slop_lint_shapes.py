"""The js family's lint-shape gates (round-19 capability adoption).

User-facing scans kept surfacing the classes a real linter catches and
the engine had no oracle for: a bare ``fs`` nothing imports (a
``ReferenceError`` on first run), an assignment to itself, an empty
``export {}`` in a file that is already a module, an impure call at a
component's render depth, an anchored regex a string method states
directly, and an escape the backslash cannot justify. All advisory,
and every check reads a length-preserving masked copy - string,
comment and regex literal contents blanked, every offset intact - so
a mention of the shape in prose is never a finding and the reported
line is the real one. The quiet direction throughout: binding
evidence silences ``bare-node-builtin``; ``impure-render-call`` reads
only the statement depth of a JSX-returning PascalCase component;
``prefer-starts-ends-with`` asks for the test/match use and a plain
body; ``useless-empty-export`` stays quiet when the empty export is
the one module mark; templates stay out of ``useless-escape`` whole.
"""

from __future__ import annotations

import re
from pathlib import Path

from slop_js_shapes import (
    _CLOSE,
    _OPEN,
    _arrow_scope,
    _ended_arrows,
    _live_arrows,
    _start_arrow_bodies,
)
from slop_model import Finding, _finding
from slop_span_mask import _regex_end, mask_span_forms, string_spans

# Node's module builtins a bare name can only reach through an import.
# Globals (`console`, `process`, `Buffer`, `Math`, `Date`) are not here:
# they need no import, so a bare use of them is not a defect.
_NODE_MODULES = frozenset(
    "assert async_hooks buffer child_process cluster crypto dgram dns domain "
    "events fs http http2 https inspector module net os path perf_hooks "
    "punycode querystring readline repl stream string_decoder tls "
    "trace_events tty url util v8 vm worker_threads zlib".split()
)


_BARE_USE_RE = re.compile(
    r"(?<![\w$.])(" + "|".join(sorted(_NODE_MODULES)) + r")(?=\s*\.\s*[A-Za-z_$])"
)

_SELF_ASSIGN_RE = re.compile(
    r"(?<![\w$.])(?P<lhs>[\w$]+(?:\.[\w$]+)*)\s*=(?![=>])\s*"
    r"(?P=lhs)(?![\w$.])\s*(?=[;\n,)}]|$)"
)

_EMPTY_EXPORT_RE = re.compile(r"\bexport\s*\{\s*\}")
_MODULE_MARK_RE = re.compile(r"\b(?:import|export)\b")

_IMPURE_CALL_RE = re.compile(
    r"\b(?:Math\.random\s*\(|Date\.now\s*\(|new\s+Date\s*\(|"
    r"crypto\.randomUUID\s*\(|crypto\.getRandomValues\s*\()"
)
_FUNCTION_COMPONENT_RE = re.compile(r"\bfunction\s+[A-Z][\w$]*\s*\([^)]*\)\s*\{")
_ARROW_COMPONENT_RE = re.compile(
    r"\b(?:const|let|var)\s+[A-Z][\w$]*\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{"
)
_JSX_RETURN_RE = re.compile(r"\breturn\s*\(?\s*<\s*[A-Za-z]")

_ANCHORED_BODY_RE = re.compile(r"[A-Za-z0-9 ._-]+")
_RECEIVER_USE_RE = re.compile(r"\.\s*(?:test|match|exec|search)\s*\(\s*$")
_METHOD_ARG_RE = re.compile(r"\s*\.\s*(?:test|match|exec|search)\s*\(")
_FLAG_RE = re.compile(r"[a-z]*")

_ESCAPE_RE = re.compile(r"\\(.)")


def _binding_pattern(name: str) -> re.Pattern[str]:
    """The shapes that bind ``name`` locally - any one silences the check."""
    return re.compile(
        rf"\b(?:const|let|var|function|class|interface|type|enum)\s+{name}\b"
        rf"|\bas\s+{name}\b"
        rf"|\b{name}\s*=(?![=>])"
        rf"|[({{,]\s*{name}\s*[:,),}}]"
    )


def _import_pattern(name: str) -> re.Pattern[str]:
    """The specifier shapes that import ``name`` (or its ``node:`` spelling)."""
    return re.compile(
        rf"(?:from\s*|require\s*\(\s*|import\s*\(\s*|import\s+)['\"](?:node:)?{name}(?:['\"]|/)"
    )


def check_bare_node_builtin(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """A node builtin module used as a bare name no import binds (advisory).

    ``fs.readFile`` with no ``import`` of ``fs`` is a ``ReferenceError``
    the first time the file runs - the shape behind scripts that grew
    by pasting. Uses are read over the masked copy, specifiers over
    the string-kept copy, and any evidence of a local binding - the
    module imported, a ``const``/``let``/``function``/``class`` of the
    same name, an ``as fs`` rename, a parameter or a key of the same
    name - silences the file: a shadowed builtin is not a defect the
    scanner can see confidently, and the quiet direction prefers a
    miss.
    """
    if family != "js":
        return []
    masked = mask_span_forms(text)
    first: dict[str, int] = {}
    for match in _BARE_USE_RE.finditer(masked):
        first.setdefault(match.group(1), match.start())
    if not first:
        return []
    kept = mask_span_forms(text, quotes="\"'`", keep_strings=True)
    findings: list[Finding] = []
    for name, offset in sorted(first.items()):
        if _binding_pattern(name).search(masked) or _import_pattern(name).search(kept):
            continue
        findings.append(
            _finding(
                path,
                text,
                offset,
                "bare-node-builtin",
                (
                    f"bare {name} is never imported - node builtin modules need an "
                    f"import (import ... from 'node:{name}')"
                ),
                2,
            )
        )
    return findings[:5]


def check_self_assign(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """``x = x`` - an assignment that changes nothing (advisory).

    A self-assignment is dead code at best and a bug at worst: the
    writer meant a different source or none at all. The masked copy
    keeps strings and comments from proposing the shape. ``==``, ``=>``,
    compound assignment (``+=``) and a right-hand side that continues
    (``x = x + 1``) are not self-assignments and stay out;
    ``const x = x`` is the same defect with a declaration attached.
    """
    if family != "js":
        return []
    masked = mask_span_forms(text)
    findings = [
        _finding(
            path,
            text,
            match.start(),
            "self-assign",
            f"{match.group('lhs')} is assigned to itself - the assignment changes nothing",
            1,
        )
        for match in _SELF_ASSIGN_RE.finditer(masked)
    ]
    return findings[:5]


def check_useless_empty_export(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """``export {}`` in a file that is already a module (advisory).

    An empty export does one thing - it turns a file with no other
    import or export into a module, which TypeScript's isolated
    transpilation sometimes needs. A file that already imports or
    exports carries that mark, so the empty export is a line of
    nothing: delete it.
    """
    if family != "js":
        return []
    masked = mask_span_forms(text)
    findings: list[Finding] = []
    for match in _EMPTY_EXPORT_RE.finditer(masked):
        rest = masked[: match.start()] + masked[match.end() :]
        if not _MODULE_MARK_RE.search(rest):
            continue
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "useless-empty-export",
                "export {} does nothing in a file that is already a module - delete it",
                1,
            )
        )
    return findings[:5]


def check_impure_render_call(
    path: Path, text: str, family: str, rel: str, ext: str
) -> list[Finding]:
    """An impure call at a component's render depth (advisory).

    ``Math.random()``, ``Date.now()`` and ``new Date()`` read a clock
    or a die: at a component body's own statement depth they run on
    every render and make the output differ from the input - React's
    purity rule. The scan reads only PascalCase functions whose body
    returns JSX, and only the statement depth: a call inside a
    callback, a listener, a timer or a memo runs when its owner runs,
    not during render, so it stays out.
    """
    if family != "js" or ext not in (".tsx", ".jsx"):
        return []
    masked = mask_span_forms(text)
    findings: list[Finding] = []
    for opener in _component_openers(masked):
        end = _bracket_span(masked, opener)
        if end < 0:
            continue
        body = masked[opener + 1 : end - 1]
        if not _JSX_RETURN_RE.search(body):
            continue
        for offset in _render_call_offsets(body):
            findings.append(
                _finding(
                    path,
                    text,
                    opener + 1 + offset,
                    "impure-render-call",
                    (
                        "an impure call during render makes every render differ - "
                        "compute it outside the component or in an event/effect"
                    ),
                    1,
                )
            )
    return findings[:5]


def _component_openers(masked: str) -> list[int]:
    """The ``{`` positions of function and arrow components (PascalCase)."""
    openers: list[int] = []
    for pattern in (_FUNCTION_COMPONENT_RE, _ARROW_COMPONENT_RE):
        openers.extend(match.end() - 1 for match in pattern.finditer(masked))
    return sorted(set(openers))


def _bracket_span(masked: str, open_index: int) -> int:
    """The index just past the ``}`` matching the ``{`` at open_index, -1."""
    depth = 0
    index = open_index
    while index < len(masked):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _render_call_offsets(body: str) -> list[int]:
    """Offsets of impure calls at the render depth, arrows excluded.

    The walk reuses the effect walker's arrow scopes: a call inside a
    nested ``=>`` body (a listener, a timer, a memo) runs when its
    owner runs, not during render, so it stays out - and the arrow's
    exclusion ends where its body ends, exactly as the effect walker's
    does.
    """
    wanted = {match.start() for match in _IMPURE_CALL_RE.finditer(body)}
    offsets: list[int] = []
    depths = {"paren": 0, "bracket": 0, "brace": 1}
    arrows: list[list] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char not in " \t\r\n":
            _start_arrow_bodies(arrows)
        nxt, hit = _impure_step(body, index, char, depths, arrows, wanted)
        if hit >= 0:
            offsets.append(hit)
        index = nxt
    return offsets


def _impure_step(
    body: str,
    index: int,
    char: str,
    depths: dict[str, int],
    arrows: list[list],
    wanted: set[int],
) -> tuple[int, int]:
    """One character's walk step; (next index, flagged offset or -1)."""
    kind = _OPEN.get(char)
    if kind is not None:
        depths[kind] += 1
        return index + 1, -1
    if char in _CLOSE:
        depths[_CLOSE[char]] -= 1
        arrows[:] = _live_arrows(arrows, depths, char)
        return index + 1, -1
    if char == "=" and body[index + 1 : index + 2] == ">":
        arrows.append(_arrow_scope(body, index, depths))
        return index + 2, -1
    if char in (";", "\n"):
        arrows[:] = _ended_arrows(arrows, depths, char)
        return index + 1, -1
    if _impure_here(index, depths, arrows, wanted):
        return index + 1, index
    return index + 1, -1


def _impure_here(index: int, depths: dict[str, int], arrows: list[list], wanted: set[int]) -> bool:
    """The render-depth gate: statement level, no callback enclosing."""
    return (
        index in wanted
        and not arrows
        and depths["paren"] == 0
        and depths["bracket"] == 0
        and depths["brace"] == 1
    )


def check_prefer_starts_ends_with(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """An anchored regex a string method states directly (advisory).

    ``/^GET/.test(method)`` and ``name.match(/app$/)`` test a prefix
    or a suffix; ``String#startsWith``/``String#endsWith`` say so
    without a regex to read. The scan asks for the use (``test``,
    ``match``, ``exec``, ``search``), for a body that is plain text -
    no metacharacter, no class, no quantifier - and for flags that
    keep the anchor's meaning (``i`` and ``m`` change it), so a regex
    the string methods cannot express stays out.
    """
    if family != "js":
        return []
    masked = mask_span_forms(text)
    findings: list[Finding] = []
    for slash in (match.start() for match in re.finditer("/", text)):
        if masked[slash] != "/":
            continue
        end = _regex_end(text, slash)
        if end is None:
            continue
        suggestion = _anchored_suggestion(text, slash, end)
        if suggestion is None:
            continue
        if not _used_for_testing(text, slash, end):
            continue
        findings.append(_finding(path, text, slash, "prefer-starts-ends-with", suggestion, 1))
    return findings[:5]


def _anchored_suggestion(text: str, slash: int, end: int) -> str | None:
    """The startsWith/endsWith suggestion for one plain anchored literal."""
    pattern = text[slash + 1 : end - 1]
    flags_match = _FLAG_RE.match(text, end)
    if flags_match is None:
        return None
    flags = flags_match.group(0)
    if "i" in flags or "m" in flags or "y" in flags:
        return None
    if pattern.startswith("^") and not pattern.endswith("$"):
        body = pattern[1:]
        if body and _ANCHORED_BODY_RE.fullmatch(body):
            return f"prefer startsWith({body!r}) over a regex with a caret"
        return None
    if pattern.endswith("$") and not pattern.startswith("^"):
        body = pattern[:-1]
        if body and _ANCHORED_BODY_RE.fullmatch(body):
            return f"prefer endsWith({body!r}) over a regex with a dollar"
    return None


def _used_for_testing(text: str, slash: int, end: int) -> bool:
    """True when the literal is the tested/matched expression itself."""
    if _METHOD_ARG_RE.match(text, end):
        return True
    return bool(_RECEIVER_USE_RE.search(text, 0, slash))


def check_useless_escape(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """An escape the backslash cannot justify (advisory).

    Inside a single-quoted string a ``\\"`` changes nothing, and the
    same holds for ``\\'`` in double quotes, an escaped backtick in
    either, and ``\\/`` anywhere - the escape is one character of
    noise a reader must undo. The scan reads the string-kept masked
    copy, so only real literals are read (a regex's escapes are its
    own grammar and stay out), and templates stay out whole.
    """
    if family != "js":
        return []
    kept = mask_span_forms(text, quotes="\"'`", keep_strings=True)
    findings: list[Finding] = []
    for start, end in string_spans(kept):
        quote = kept[start]
        if quote == "`":
            continue
        for match in _ESCAPE_RE.finditer(kept, start + 1, end - 1):
            escaped = match.group(1)
            if escaped == "\\\\" or not _escape_is_useless(escaped, quote):
                continue
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "useless-escape",
                    f"unnecessary escape \\{escaped} - the backslash changes nothing here",
                    1,
                )
            )
    return findings[:5]


def _escape_is_useless(escaped: str, quote: str) -> bool:
    """The four escape shapes that never change the string's meaning."""
    if escaped == "/":
        return True
    if escaped == '"':
        return quote == "'"
    if escaped == "'":
        return quote == '"'
    return escaped == "`" and quote != "`"
