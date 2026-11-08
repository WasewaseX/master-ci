"""Cross-family regex checks: exceptions, debug residue, suppressions, placeholders."""

from __future__ import annotations

import re
from pathlib import Path

from slop_model import Finding, _finding
from slop_patterns import (
    BLANKET_SUPPRESS,
    CREDIT_PLACEHOLDER_VALUE,
    CREDIT_VALUE_PATTERN,
    EMPTY_CATCH,
    EMPTY_RESCUE,
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


def _is_test_rel(rel: str) -> bool:
    lowered = rel.lower()
    return ".test." in lowered or ".spec." in lowered or "/test" in lowered


def check_jvm_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """Java-column signals from scanaislop's languages page (advisory)."""
    if family != "jvm" or not path.suffix.lower() == ".java" or _is_test_rel(rel):
        return []
    findings: list[Finding] = []
    prints = len(JVM_DEBUG_PRINT.findall(text))
    if prints:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="debug-print",
                message=f"System.out debug output ({prints}x)",
                weight=1,
                advisory=True,
            )
        )
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
    return findings


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


def check_exceptions_js_family(path: Path, text: str, family: str) -> list[Finding]:
    findings: list[Finding] = []
    if family in ("js", "jvm", "csharp", "cpp", "php", "swift", "dart"):
        for match in EMPTY_CATCH.finditer(text):
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "empty-catch",
                    "empty catch block hides every error",
                    2,
                )
            )
    if family == "jvm":
        for match in PRINT_STACKTRACE_ONLY.finditer(text):
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "print-stacktrace-only",
                    "catch block only prints a stack trace",
                    1,
                )
            )
    if family == "ruby":
        lines = text.splitlines()
        for idx, raw in enumerate(lines):
            if EMPTY_RESCUE.match(raw):
                nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                if nxt == "end":
                    findings.append(
                        Finding(
                            path=str(path),
                            line=idx + 1,
                            check="empty-rescue",
                            message="rescue with no handling before end",
                            weight=2,
                        )
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
    return findings


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
    return findings


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
