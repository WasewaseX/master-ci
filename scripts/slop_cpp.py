"""C/C++ family checks: the scanaislop C++ column, adopted as advisory rules.

The oracle is line/regex over the preprocessed-invisible surface - the same
zero-dependency law as the rest of the engine. C++-only rules (casts,
nullptr, #define constants) deliberately skip plain `.h` files: a `.h`
header may be consumed from C, where the C++ replacement does not exist,
so flagging it would be a false positive by construction. Header-scope
leaks and stub throws run on every C family file including `.h`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from slop_model import _finding

if TYPE_CHECKING:
    from slop_model import Finding
from slop_patterns import _strip_strings

# C++-only translation units and C++ headers - plain `.h` stays out because
# it may be compiled as C, where nullptr/constexpr/casts rules do not apply.
CPP_ONLY_EXTS = {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}
HEADER_EXTS = {".h", ".hpp", ".hh", ".hxx"}
CPP_STDLIB_CAST = re.compile(
    r"\(\s*(?:const\s+|volatile\s+)*(?:unsigned\s+|signed\s+)?"
    r"(?:void|int|long|short|char|float|double|bool|size_t|ssize_t|ptrdiff_t)"
    r"\s*\*?\s*\)\s*[&*\w]"
)
CPP_DEFINE_CONSTANT = re.compile(
    r"^\s*#define\s+([A-Za-z_]\w*)\s+(?!\"|\s*$)(\S.*)$", re.MULTILINE
)
CPP_ENDL = re.compile(r"<<\s*std::endl\b")
CPP_IOSTREAM = re.compile(r"\bstd::c(?:out|err)\b")
CPP_MAIN = re.compile(r"\bint\s+main\s*\(")
CPP_DELETE = re.compile(r"\bdelete\s*(?:\[\s*\])?\s*[&*A-Za-z_]")
CPP_NOT_IMPLEMENTED = re.compile(
    r"throw\s+std::(?:logic_error|runtime_error)\s*\(\s*\"[^\"]*not\s+implemented"
    r"|assert\s*\(\s*(?:false|!\s*)[^)]*\"[^\"]*not\s+implemented"
)
CPP_NULL = re.compile(r"\bNULL\b")
CPP_USING_NAMESPACE_STD = re.compile(r"^\s*using\s+namespace\s+std\s*;", re.MULTILINE)

MAX_FINDINGS_PER_CHECK = 5


def _cpp_only(path: Path) -> bool:
    return path.suffix.lower() in CPP_ONLY_EXTS


def check_cpp_slop(path: Path, text: str, family: str) -> list[Finding]:
    """The C++ column: one finder per rule, every finding advisory."""
    if family != "cpp":
        return []
    code = _strip_strings(text)
    findings: list[Finding] = []
    if _cpp_only(path):
        findings.extend(_c_style_cast(path, code))
        findings.extend(_define_constant(path, code))
        findings.extend(_null_literal(path, code))
    findings.extend(_endl_in_stream(path, code))
    findings.extend(_iostream_leftover(path, code))
    findings.extend(_manual_delete(path, code))
    findings.extend(_not_implemented(path, text))
    if path.suffix.lower() in HEADER_EXTS:
        findings.extend(_using_namespace_std(path, code))
    return findings


def _cap(findings: list[Finding]) -> list[Finding]:
    return findings[:MAX_FINDINGS_PER_CHECK]


def _c_style_cast(path: Path, code: str) -> list[Finding]:
    return _cap([
        _finding(path, code, m.start(), "cpp-c-style-cast",
                 "C-style cast - static, const and reinterpret conversions "
                 "share one syntax; name the conversion you mean", 1)
        for m in CPP_STDLIB_CAST.finditer(code)
    ])


def _define_constant(path: Path, code: str) -> list[Finding]:
    return _cap([
        _finding(path, code, m.start(), "cpp-define-constant",
                 f"#define constant {m.group(1)!r} - no type, no scope, "
                 "invisible to the debugger; use constexpr or const", 1)
        for m in CPP_DEFINE_CONSTANT.finditer(code)
        if "(" not in m.group(0).split(m.group(1), 1)[1][:1]
    ])


def _endl_in_stream(path: Path, code: str) -> list[Finding]:
    return _cap([
        _finding(path, code, m.start(), "cpp-endl-in-stream",
                 "<< std::endl flushes the stream on every use - use '\\n' "
                 "and let the buffer work", 1)
        for m in CPP_ENDL.finditer(code)
    ])


def _iostream_leftover(path: Path, code: str) -> list[Finding]:
    if CPP_MAIN.search(code):
        return []
    return _cap([
        _finding(path, code, m.start(), "cpp-iostream-leftover",
                 "std::cout/std::cerr in a file with no main - usually debug "
                 "output that shipped", 1)
        for m in CPP_IOSTREAM.finditer(code)
    ])


def _manual_delete(path: Path, code: str) -> list[Finding]:
    return _cap([
        _finding(path, code, m.start(), "cpp-manual-delete",
                 "manual delete - an early return or throw between new and "
                 "delete leaks; use unique_ptr or RAII", 1)
        for m in CPP_DELETE.finditer(code)
    ])


def _not_implemented(path: Path, text: str) -> list[Finding]:
    """Stub throws keep their string literals: the "not implemented" text
    IS the signal, so the original text runs unstripped here."""
    return _cap([
        _finding(path, text, m.start(), "cpp-not-implemented",
                 "not-implemented stub - compiles and fails at runtime", 1)
        for m in CPP_NOT_IMPLEMENTED.finditer(text)
    ])


def _null_literal(path: Path, code: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in CPP_NULL.finditer(code):
        start = code.rfind("\n", 0, m.start()) + 1
        line = code[start:m.start()]
        if "#define" in line or "#include" in line:
            continue
        findings.append(_finding(
            path, code, m.start(), "cpp-null-literal",
            "NULL in C++ - an integer constant in disguise that can select "
            "the wrong overload; use nullptr", 1,
        ))
    return _cap(findings)


def _using_namespace_std(path: Path, code: str) -> list[Finding]:
    return _cap([
        _finding(path, code, m.start(), "cpp-using-namespace-std-in-header",
                 "using namespace std in a header - every includer inherits "
                 "the whole std namespace and its clashes", 1)
        for m in CPP_USING_NAMESPACE_STD.finditer(code)
    ])
