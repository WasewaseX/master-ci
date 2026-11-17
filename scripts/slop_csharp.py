"""C# family checks: the scanaislop C# column, adopted as advisory rules.

Every rule is a judgment call a reviewer confirms, so all findings land in
ADVISORY_ONLY_CHECKS (registry data in slop_model, resolved by
Finding.__post_init__). Line/regex oracles, no Roslyn dependency - the
same zero-dependency law as the rest of the engine. Language limits are
stated per rule; none of these oracles parse the Roslyn syntax tree, so
nested-string and generated-code edge cases stay exempt through the
string-stripping helper rather than claimed as AST-grade truth.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from slop_files import is_test_path
from slop_model import _finding

if TYPE_CHECKING:
    from slop_model import Finding
from slop_patterns import _strip_strings

CS_ASYNC_VOID = re.compile(r"\basync\s+void\s+(\w+)\s*\(")
CS_BROAD_CATCH = re.compile(
    r"\bcatch\s*\(\s*(?:[\w.]+\.)?(?:Exception|System\.Exception)\s+(\w+)\s*\)"
)
CS_CATCH_RETHROW_ONLY = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{[^{}]*?\bthrow\s*;[^{}]*?\}", re.DOTALL
)
CS_EMPTY_CATCH_BODY = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
CS_CONSOLE = re.compile(
    r"\b(?:(?:Console\.(?:Write(?:Line)?|Error\.Write(?:Line)?|Out\.Write(?:Line)?))"
    r"|(?:Debug\.(?:Write(?:Line)?|Print))"
    r"|(?:Trace\.(?:Write(?:Line)?)))\s*\("
)
CS_LINQ_COUNT = re.compile(r"\.Count\(\)\s*(?:>0|> 0|<1|< 1|== 0|!= 0)")
CS_INDEX_LOOP = re.compile(
    r"\bfor\s*\(\s*(?:int|var|long)\s+(\w+)\s*=\s*0\s*;\s*\1\s*<\s*"
    r"[\w.\[\]]+\.(?:Length|Count)\b"
)
CS_IF_COMPARE = re.compile(r"\b(?:if|else\s+if)\s*\(\s*(\w+)\s*==\s*[^)]+\)")
CS_NOT_IMPLEMENTED = re.compile(r"\bthrow\s+new\s+NotImplementedException\s*\(")
CS_NULL_FORGIVING = re.compile(r"(?<=[\w\)])!(?=[.\[\)\],;\s])")
CS_SYNC_OVER_ASYNC = re.compile(r"\.(?:Result\b|Wait\(\)|GetAwaiter\(\)\.GetResult\(\))")
CS_PRAGMA_DISABLE = re.compile(r"^\s*#pragma\s+warning\s+disable\b(.*)$", re.MULTILINE)
CS_SUPPRESS_MESSAGE = re.compile(r"\[SuppressMessage\(")
CS_DOC_SUMMARY = re.compile(
    r"///\s*<summary>\s*((?:Gets|Sets|Gets or sets)\s+(?:or\s+sets\s+)?the\s+"
    r"[\w<>\[\]]+\.?)\s*</summary>",
    re.IGNORECASE,
)
CS_MEMBER_DECL = re.compile(r"\b([A-Za-z_]\w*)\s*(?:\{|=>|;|=|\()")

MAX_FINDINGS_PER_CHECK = 5

DOC_RESTATE_PHRASES = ("gets or sets the", "gets the", "sets the")


def _code_lines(text: str) -> list[tuple[int, str]]:
    """(1-based lineno, stripped) with comment-only and blank lines marked."""
    out: list[tuple[int, str]] = []
    in_block = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if in_block:
            out.append((lineno, "" if "*/" not in line else line.split("*/")[-1]))
            if "*/" in line:
                in_block = False
            continue
        if line.startswith("/*"):
            in_block = "*/" not in line
            out.append((lineno, ""))
            continue
        out.append((lineno, "" if line.startswith(("//", "#")) else line))
    return out


def _catch_body(text: str, open_brace: int) -> str:
    """The catch block's body up to its matching close brace."""
    depth = 0
    for idx in range(open_brace, min(open_brace + 2000, len(text))):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : idx]
    return ""


def check_csharp_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """The C# column: one finder per rule, every finding advisory."""
    if family != "csharp":
        return []
    is_test = is_test_path(rel)
    code = _strip_strings(text)
    findings: list[Finding] = []
    findings.extend(_async_void(path, text))
    findings.extend(_broad_catch(path, text))
    findings.extend(_catch_rethrow_only(path, code))
    findings.extend(_console_leftover(path, text, is_test))
    findings.extend(_linq_count(path, code))
    findings.extend(_index_loop(path, code))
    findings.extend(_if_ladder(path, code))
    findings.extend(_concat_in_loop(path, text))
    findings.extend(_not_implemented(path, text))
    findings.extend(_null_forgiving(path, code))
    findings.extend(_sync_over_async(path, text))
    findings.extend(_suppressed_warning(path, text))
    findings.extend(_redundant_doc_comment(path, text))
    return findings


def _cap(findings: list[Finding]) -> list[Finding]:
    return findings[:MAX_FINDINGS_PER_CHECK]


def _async_void(path: Path, text: str) -> list[Finding]:
    hits = [m for m in CS_ASYNC_VOID.finditer(text) if not m.group(1).startswith("On")]
    return _cap(
        [
            _finding(
                path,
                text,
                m.start(),
                "csharp-async-void",
                f"async void {m.group(1)!r} - callers cannot await it and its "
                "exceptions crash the process",
                1,
            )
            for m in hits
        ]
    )


def _broad_catch(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in CS_BROAD_CATCH.finditer(text):
        open_brace = text.find("{", m.end() - 1)
        body = _catch_body(text, open_brace) if open_brace != -1 else ""
        stripped = _strip_strings(body)
        caught = m.group(1)
        uses = caught and re.search(rf"\b{re.escape(caught)}\b", stripped)
        bare_rethrow = re.fullmatch(r"\s*(?:throw\s*;?\s*)", stripped or " ")
        if uses or bare_rethrow or not stripped.strip():
            continue
        findings.append(
            _finding(
                path,
                text,
                m.start(),
                "csharp-broad-catch",
                "catch (Exception) handles every failure and never reads the "
                "caught exception - handle the specific type it can recover from",
                1,
            )
        )
    return _cap(findings)


def _catch_rethrow_only(path: Path, code: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                code,
                m.start(),
                "csharp-empty-catch-rethrow",
                "catch block only rethrows - the handler changes nothing and adds nesting",
                1,
            )
            for m in CS_CATCH_RETHROW_ONLY.finditer(code)
        ]
    )


def _console_leftover(path: Path, text: str, is_test: bool) -> list[Finding]:
    if is_test:
        return []
    hits = CS_CONSOLE.findall(text)
    first = CS_CONSOLE.search(text)
    if first is None:
        return []
    return [
        _finding(
            path,
            text,
            first.start(),
            "csharp-console-leftover",
            f"Console/Debug/Trace output ({len(hits)}x) in library code",
            1,
        )
    ]


def _linq_count(path: Path, code: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                code,
                m.start(),
                "csharp-linq-count",
                ".Count() walks the whole sequence to answer a yes/no "
                "question - use .Any() or the Count property",
                1,
            )
            for m in CS_LINQ_COUNT.finditer(code)
        ]
    )


def _index_loop(path: Path, code: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                code,
                m.start(),
                "csharp-index-loop",
                f"index for-loop over .Length/.Count ({m.group(1)}) - a "
                "foreach reads more clearly when the index is bookkeeping",
                1,
            )
            for m in CS_INDEX_LOOP.finditer(code)
        ]
    )


def _if_ladder(path: Path, code: str) -> list[Finding]:
    chains: dict[str, int] = {}
    for m in CS_IF_COMPARE.finditer(code):
        chains[m.group(1)] = chains.get(m.group(1), 0) + 1
    return _cap(
        [
            _finding(
                path,
                code,
                code.find(f"{subject} =="),
                "csharp-if-ladder",
                f"{count} if/else-if branches compare {subject!r} against "
                "constants - a switch or dispatch table written longhand",
                1,
            )
            for subject, count in sorted(chains.items(), key=lambda kv: -kv[1])
            if count >= 4
        ]
    )


def _drop_comments(code: str) -> str:
    """Comment text blanked, newlines kept - line numbers stay true."""
    return re.sub(
        r"//[^\n]*",
        lambda m: " " * len(m.group(0)),
        re.sub(
            r"/\*[\s\S]*?\*/",
            lambda m: re.sub(r"[^\n]", " ", m.group(0)),
            code,
        ),
    )


CONCAT_TOKEN = re.compile(r"\{|\}|\b(?:for|foreach|while)\s*\(|\b(\w+)\s*\+=\s*(?:\$?)\"")


def _step_scope(token: str, depth: int, loop_bodies: list[int]) -> tuple[int, list[int]]:
    """One token's effect on the brace depth and the open loop bodies."""
    if token == "{":
        return depth + 1, loop_bodies
    if token == "}":
        depth -= 1
        return depth, [d for d in loop_bodies if d <= depth]
    if token.endswith("("):
        return depth, loop_bodies + [depth + 1]
    return depth, loop_bodies


def _concat_in_loop(path: Path, text: str) -> list[Finding]:
    """`+= "..."` inside a loop body, via brace depth over comment-free text.

    String literals stay INTACT here - the literal quote is the signal -
    so braces inside a string can nudge the depth counter; the worst case
    is one advisory finding at a shifted line, never a crash.
    """
    clean = _drop_comments(text)
    findings: list[Finding] = []
    depth = 0
    loop_bodies: list[int] = []
    for m in CONCAT_TOKEN.finditer(clean):
        depth, loop_bodies = _step_scope(m.group(0), depth, loop_bodies)
        if m.group(1) and loop_bodies and loop_bodies[-1] <= depth:
            findings.append(
                _finding(
                    path,
                    clean,
                    m.start(),
                    "csharp-string-concat-in-loop",
                    f"string built with += inside a loop ({m.group(1)!r}) - "
                    "quadratic reallocation; use StringBuilder",
                    1,
                )
            )
    return _cap(findings)


def _not_implemented(path: Path, text: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                text,
                m.start(),
                "csharp-not-implemented",
                "NotImplementedException stub - compiles, fails at runtime",
                1,
            )
            for m in CS_NOT_IMPLEMENTED.finditer(text)
        ]
    )


def _null_forgiving(path: Path, code: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                code,
                m.start(),
                "csharp-null-forgiving",
                "null-forgiving ! silences the nullable warning without checking anything",
                1,
            )
            for m in CS_NULL_FORGIVING.finditer(code)
        ]
    )


def _sync_over_async(path: Path, text: str) -> list[Finding]:
    return _cap(
        [
            _finding(
                path,
                text,
                m.start(),
                "csharp-sync-over-async",
                "blocking on a Task - ties up the thread and can deadlock "
                "under a synchronization context",
                1,
            )
            for m in CS_SYNC_OVER_ASYNC.finditer(text)
        ]
    )


def _suppressed_warning(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in CS_PRAGMA_DISABLE.finditer(text):
        if not re.search(r"//\s*\S", m.group(1)):
            findings.append(
                _finding(
                    path,
                    text,
                    m.start(),
                    "csharp-suppressed-warning",
                    "warning suppression without a reason comment - the finding "
                    "is hidden and nobody knows why",
                    1,
                )
            )
    for m in CS_SUPPRESS_MESSAGE.finditer(text):
        close = text.find(")]", m.start())
        scope = text[m.start() : close if close != -1 else m.start() + 200]
        if "Justification" not in scope:
            findings.append(
                _finding(
                    path,
                    text,
                    m.start(),
                    "csharp-suppressed-warning",
                    "[SuppressMessage] without a Justification - the analyzer finding is hidden",
                    1,
                )
            )
    return _cap(findings)


def _redundant_doc_comment(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in CS_DOC_SUMMARY.finditer(text):
        phrase = m.group(1).lower()
        if not phrase.startswith(DOC_RESTATE_PHRASES):
            continue
        tail = text[m.end() : m.end() + 400]
        decl = CS_MEMBER_DECL.search(tail)
        if decl:
            findings.append(
                _finding(
                    path,
                    text,
                    m.start(),
                    "csharp-redundant-doc-comment",
                    f"XML summary restates the member ({m.group(1)!r}) - it "
                    "documents nothing the signature does not already say",
                    1,
                )
            )
    return _cap(findings)
