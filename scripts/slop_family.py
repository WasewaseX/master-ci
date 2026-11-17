"""Cross-family regex checks: exceptions, debug residue, suppressions, placeholders."""

from __future__ import annotations

import re
from pathlib import Path

from slop_files import is_test_path
from slop_model import Finding, _finding
from slop_patterns import (
    BLANKET_SUPPRESS,
    CREDIT_PLACEHOLDER_VALUE,
    CREDIT_VALUE_PATTERN,
    EMPTY_CATCH,
    EMPTY_RESCUE,
    EVAL_USAGE,
    GO_BLANK_ERR,
    GO_DEBUG_PRINT,
    GO_IGNORED_ERROR,
    JS_CONSOLE_LOG,
    PLACEHOLDER_NAME_PATTERN,
    PRINT_STACKTRACE_ONLY,
    RUST_STUB,
    RUST_UNWRAP,
    TS_SUPPRESSION,
    _strip_strings,
)

# scanaislop round-5 (languages page, Java column): System.out leftovers,
# broad throws, and unused imports.
JVM_DEBUG_PRINT = re.compile(r"\bSystem\.(?:out|err)\.print(?:ln|f)?\s*\(")
JVM_BROAD_THROWS = re.compile(r"\bthrows\s+(?:[\w.]+\.)?(?:Exception|Throwable)\b")
JVM_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)

# scanaislop round-8 adoption: the Go panic column, the Rust per-finding
# unwrap (the density verdict keeps its own aggregate) and the Scala
# broad-catch clause.
GO_PANIC = re.compile(r"\bpanic\s*\(")
SCALA_BROAD_CATCH = re.compile(
    r"case\s+(?:_\s*:|\w+\s*:)\s*(?:[\w.]+\.)?(?:Exception|Throwable)\b"
)


def check_jvm_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """Java-column signals from scanaislop's languages page (advisory).

    The Java oracles run on ``.java``; the Scala broad-catch clause joins
    on ``.scala`` - same family, different suffix, one gate.
    """
    if family != "jvm" or is_test_path(rel):
        return []
    suffix = path.suffix.lower()
    if suffix not in (".java", ".scala"):
        return []
    findings: list[Finding] = []
    if suffix == ".java":
        prints = len(JVM_DEBUG_PRINT.findall(text))
        if prints:
            findings.append(Finding(
                path=str(path), line=1, check="debug-print",
                message=f"System.out debug output ({prints}x)",
                weight=1, advisory=True,
            ))
        for match in JVM_BROAD_THROWS.finditer(text):
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "java-broad-throws",
                    f"broad {match.group(0).split()[-1]} thrown - declare the specific type",
                    1,
                )
            )
        findings.extend(_jvm_phantom_imports(path, text))
    findings.extend(_scala_broad_catch(path, text))
    return findings


def _scala_broad_catch(path: Path, text: str) -> list[Finding]:
    """Scala catch clauses handling Exception/Throwable wholesale.

    Immediately-rethrowing clauses are exempt (recovery-neutral, the same
    exemption redundant-try-catch grants). Advisory: the oracle reads one
    clause line beyond the ``=>``, no more.
    """
    if not path.suffix.lower() == ".scala":
        return []
    findings: list[Finding] = []
    for match in SCALA_BROAD_CATCH.finditer(text):
        tail = text[match.end():match.end() + 80]
        clause = tail.split("=>", 1)[-1].split("\n")[0] if "=>" in tail else ""
        if "throw" not in clause:
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "scala-broad-catch",
                    "catch clause handles Exception/Throwable wholesale - "
                    "recover from the specific type it can handle",
                    1,
                )
            )
    return findings[:5]


def _jvm_phantom_imports(path: Path, text: str) -> list[Finding]:
    """Imported simple names that never appear outside their import lines."""
    imported: list[tuple[str, int]] = []
    for match in JVM_IMPORT.finditer(text):
        name = match.group(1).split(".")[-1]
        if name != "*":
            imported.append((name, match.start()))
    if not imported:
        return []
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("import "))
    findings: list[Finding] = []
    for name, pos in imported[:40]:
        if not re.search(rf"\b{re.escape(name)}\b", body):
            findings.append(
                _finding(
                    path,
                    text,
                    pos,
                    "phantom-import",
                    f"import {name!r} is never used",
                    1,
                )
            )
    return findings[:5]


def _catch_findings(path: Path, text: str, family: str) -> list[Finding]:
    """Empty catches across the brace families, stack-trace-only for the JVM."""
    findings: list[Finding] = []
    if family in ("js", "jvm", "csharp", "cpp", "php", "swift", "dart"):
        findings.extend(
            _finding(
                path,
                text,
                match.start(),
                "empty-catch",
                "empty catch block hides every error",
                2,
            )
            for match in EMPTY_CATCH.finditer(text)
        )
    if family == "jvm":
        findings.extend(
            _finding(
                path,
                text,
                match.start(),
                "print-stacktrace-only",
                "catch block only prints a stack trace",
                1,
            )
            for match in PRINT_STACKTRACE_ONLY.finditer(text)
        )
    return findings


def _empty_rescue_lines(text: str) -> list[int]:
    """1-based lines of `rescue` blocks with nothing but `end` after them."""
    lines = text.splitlines()
    hits: list[int] = []
    for idx, raw in enumerate(lines):
        if not EMPTY_RESCUE.match(raw):
            continue
        nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        if nxt == "end":
            hits.append(idx + 1)
    return hits


def check_exceptions_js_family(path: Path, text: str, family: str) -> list[Finding]:
    findings = _catch_findings(path, text, family)
    if family == "ruby":
        findings.extend(
            Finding(
                path=str(path),
                line=line,
                check="empty-rescue",
                message="rescue with no handling before end",
                weight=2,
            )
            for line in _empty_rescue_lines(text)
        )
    return findings


def check_go(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    if family != "go":
        return []
    findings: list[Finding] = []
    for match in GO_IGNORED_ERROR.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "go-ignored-error",
                "err checked but nil returned - failure hidden",
                2,
            )
        )
    for match in GO_BLANK_ERR.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "go-error-assigned-blank",
                "error assigned to blank identifier",
                1,
            )
        )
    is_test = rel.endswith("_test.go")
    if not is_test:
        debugs = len(GO_DEBUG_PRINT.findall(text))
        if debugs:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="debug-print",
                    message=f"fmt.Print* debug output ({debugs}x)",
                    weight=1,
                    advisory=True,
                )
            )
        findings.extend(_go_library_panic(path, text))
    return findings


def _go_library_panic(path: Path, text: str) -> list[Finding]:
    """scanaislop round-8: panic unwinds the caller's goroutine instead of
    returning an error; `package main` binaries may, libraries may not."""
    if "package main" in text[:200] or "package main" in text[-200:]:
        return []
    for match in GO_PANIC.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if text[line_start:match.start()].strip().startswith("//"):
            continue
        return [
            _finding(
                path,
                text,
                match.start(),
                "go-library-panic",
                "panic() in a library package - return an error the caller "
                "can handle instead of unwinding its goroutine",
                1,
            )
        ]
    return []


def check_rust(path: Path, text: str, family: str) -> list[Finding]:
    if family != "rust":
        return []
    findings: list[Finding] = []
    for match in RUST_STUB.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "rust-stub",
                f"{match.group(0)}!() panics at runtime",
                1,
            )
        )
    unwraps = len(RUST_UNWRAP.findall(text))
    if unwraps >= 10:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="unwrap-density",
                message=f"{unwraps} unwrap() calls; panics on None/Err",
                weight=1,
                advisory=True,
            )
        )
    findings.extend(_rust_unwraps(path, text))
    return findings


def _rust_unwraps(path: Path, text: str) -> list[Finding]:
    """Per-finding unwraps in production code (scanaislop round-8).

    Test-gated by both the path and the in-file ``#[cfg(test)]`` marker:
    only unwraps before the marker are flagged, so a mixed file's test
    half stays silent. Advisory - an unwrap behind a documented invariant
    is a judgment call the oracle cannot model.
    """
    cfg_test = text.find("#[cfg(test)]")
    findings: list[Finding] = []
    for match in RUST_UNWRAP.finditer(text):
        if cfg_test != -1 and match.start() > cfg_test:
            break
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "rust-unwrap",
                "unwrap() panics on None/Err - handle it or document the "
                "invariant that makes it safe",
                1,
            )
        )
    return findings[:5]


def check_debug_left(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    lowered_rel = rel.lower()
    if ".test." in lowered_rel or ".spec." in lowered_rel or "/test" in lowered_rel:
        return []
    findings: list[Finding] = []
    if family == "js" and "debugger;" in text:
        findings.append(
            _finding(
                path,
                text,
                text.find("debugger;"),
                "debugger-left",
                "debugger; statement committed",
                2,
            )
        )
    if family in ("ruby",) and re.search(r"binding\.pry|byebug\b", text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="debugger-left",
                message="pry/byebug breakpoint committed",
                weight=2,
            )
        )
    return findings


def check_js_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    if family != "js":
        return []
    findings: list[Finding] = []
    is_test = bool(re.search(r"(?:\.test\.|\.spec\.|/tests?/|/__tests__/)", rel.lower()))
    if not is_test:
        logs = len(JS_CONSOLE_LOG.findall(text))
        if logs:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="debug-print",
                    message=f"console.log debug output ({logs}x)",
                    weight=1,
                    advisory=True,
                )
            )
    for match in TS_SUPPRESSION.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "ts-suppression",
                f"type-system bypass {match.group(0)!r}",
                1,
            )
        )
    for match in EVAL_USAGE.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "eval-usage",
                f"dynamic evaluation {match.group(0)!r} - the checker cannot see what runs",
                1,
            )
        )
    return findings


def check_blanket_suppression(path: Path, text: str) -> list[Finding]:
    count = len(BLANKET_SUPPRESS.findall(text))
    if count >= 2:
        return [
            Finding(
                path=str(path),
                line=1,
                check="blanket-suppression",
                message=f"{count} blanket lint/type suppressions",
                weight=1,
                advisory=True,
            )
        ]
    return []


def check_placeholder_names(path: Path, text: str) -> list[Finding]:
    hits = len(PLACEHOLDER_NAME_PATTERN.findall(_strip_strings(text)))
    if hits >= 5:
        return [
            Finding(
                path=str(path),
                line=1,
                check="placeholder-naming",
                message=f"{hits} foo/bar/baz-style identifiers",
                weight=1,
                advisory=True,
            )
        ]
    return []


def check_placeholder_credential(path: Path, text: str) -> list[Finding]:
    for match in CREDIT_VALUE_PATTERN.finditer(text):
        value = match.group(2)
        if CREDIT_PLACEHOLDER_VALUE.search(value):
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-credential",
                    f"placeholder credential assigned to {match.group(1)!r}",
                    2,
                )
            ]
    return []
