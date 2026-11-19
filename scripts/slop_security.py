"""The security column adopted from scanaislop (round-16 parity).

The placeholder-credential check catches placeholder credentials by
name; this module catches the shape-level defects the naming oracle
cannot see:

- innerhtml / dangerously-set-innerhtml: DOM injection sinks that
    need sanitization (JS family)
- sql-injection: interpolation or concatenation inside an SQL string
    (Python f-strings, JS template literals, C# interpolated strings)
- shell-injection: a non-literal command handed to a shell executor
    (subprocess shell=True, child_process.exec, C system/popen)
- unsafe-deserialization: pickle/marshal/yaml.load without a safe
    loader and the .NET BinaryFormatter/SoapFormatter sinks
- unsafe-c-call: the memory-unsafe C string functions (gets, strcpy,
    strcat, sprintf)
- hardcoded-secret: format-specific credentials the naming oracle
    misses (AWS access keys, JWTs, PEM private keys, database URLs
    with embedded passwords)

Every oracle is lexical by design and skips test paths and comment
lines: the column refuses to decide taint across function
boundaries, so it flags only the sink patterns a reviewer can
confirm on the same line.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from slop_files import is_test_path
from slop_model import _finding
from slop_patterns import _strip_strings

if TYPE_CHECKING:
    from slop_model import Finding

# Lines the column treats as non-code: pure comments in the four syntaxes
# it scans. Interpolations inside real code never start with these.
_COMMENT_LINE = re.compile(r"^\s*(#|//|/\*|\*|--)")


def _offset(text: str, lineno: int) -> int:
    """Character offset of the start of a 1-based line."""
    return sum(len(line) + 1 for line in text.splitlines()[: lineno - 1])


def _code_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line, line) pairs, skipping blank and comment-only lines."""
    return [
        (n, line)
        for n, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not _COMMENT_LINE.match(line)
    ]


def _cap(findings: list[Finding]) -> list[Finding]:
    """Five findings per check keeps one hot file from owning the report."""
    return findings[:5]


def _skip(rel: str) -> bool:
    """Test paths carry deliberate fixtures, not production sinks."""
    return is_test_path(rel)


# -- 1. innerHTML -----------------------------------------------------------

_INNERHTML_ASSIGN = re.compile(r"\.innerHTML\s*=(?!=)")
_DANGEROUS_PROP = re.compile(r"dangerouslySetInnerHTML\s*[:=]")


def check_innerhtml(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """Direct .innerHTML assignment: unsanitized HTML reaches the DOM."""
    if family != "js" or _skip(rel):
        return []
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if _INNERHTML_ASSIGN.search(line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "innerhtml",
                    "direct .innerHTML assignment - untrusted HTML becomes "
                    "markup; sanitize or build DOM nodes instead",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return _cap(out)


def check_dangerously_set_innerhtml(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """React dangerouslySetInnerHTML: the JSX escape hatch for raw HTML."""
    if family != "js" or _skip(rel):
        return []
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if _DANGEROUS_PROP.search(line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "dangerously-set-innerhtml",
                    "dangerouslySetInnerHTML bypasses React's escaping - "
                    "sanitize the HTML or render components instead",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return _cap(out)


# -- 2. SQL injection -------------------------------------------------------

_SQL_KEYWORD = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE)\b",
    re.IGNORECASE,
)
_PY_FSTRING_SQL = re.compile(r"""\bf["'][^"']*\{""", re.IGNORECASE)
_INTERP = re.compile(r"\{|\+\s*\w|\$\{")


def check_sql_injection(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """SQL written as an interpolated string: the query and the data mix."""
    if family not in ("py", "js", "csharp") or _skip(rel):
        return []
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if not _SQL_KEYWORD.search(line) or not _INTERP.search(line):
            continue
        interpolated = (
            bool(_PY_FSTRING_SQL.search(line))
            if family == "py"
            else bool(re.search(r"\$\{|\+\s*\w|\$\"", line))
        )
        if not interpolated:
            continue
        out.append(
            _finding(
                path,
                text,
                _offset(text, n),
                "sql-injection",
                "SQL query built by string interpolation - use parameterized "
                "queries or bound placeholders instead",
                5,
            )
        )
        if len(out) >= 5:
            break
    return _cap(out)


# -- 3. Shell injection -----------------------------------------------------

_SHELL_TRUE = re.compile(r"shell\s*=\s*True")
# The command slot holds a non-literal: a bare name, an f-string, a
# concatenation, .format or % interpolation. A constant string is exempt.
_PY_DYNAMIC_CMD = re.compile(
    r"""\(\s*(?!["'])[A-Za-z_]\w*(?:\.\w+)*\s*[,)]|f["'][^"']*\{|%\s*\(|\.format\(|["']\s*\+"""
)
_JS_EXEC = re.compile(r"\b(?:child_process\.)?exec(?:Sync)?\s*\(")
# After _strip_strings a string literal is blanked to spaces, so a
# whitespace character in the command slot means "a (blanked) literal" -
# only a visible identifier counts as a non-literal command.
_C_SYSTEM = re.compile(r"\b(?:system|popen)\s*\(\s*[^\"\s)]")


def _py_shell_findings(path: Path, text: str) -> list[Finding]:
    """shell=True whose command slot holds anything but a constant."""
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if _SHELL_TRUE.search(line) and _PY_DYNAMIC_CMD.search(line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "shell-injection",
                    "shell=True with a dynamic command - pass an argv "
                    "list without shell=True instead",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return out


def _js_shell_findings(path: Path, text: str) -> list[Finding]:
    """exec()/execSync() with an interpolated command string."""
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if _JS_EXEC.search(line) and re.search(r"\$\{|\+\s*\w", line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "shell-injection",
                    "exec() with an interpolated command - use execFile with an argv array instead",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return out


def _c_shell_findings(path: Path, text: str) -> list[Finding]:
    """system()/popen() whose argument reaches /bin/sh unevaluated."""
    code = _strip_strings(text)
    out: list[Finding] = []
    for n, line in _code_lines(code):
        if _C_SYSTEM.search(line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(code, n),
                    "shell-injection",
                    "system()/popen() with a non-literal command - the "
                    "argument reaches /bin/sh; use execvp/fork instead",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return out


_SHELL_ORACLES = {"py": _py_shell_findings, "js": _js_shell_findings, "cpp": _c_shell_findings}


def check_shell_injection(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """A dynamic command handed to a shell: the shell parses the input."""
    if _skip(rel) or family not in _SHELL_ORACLES:
        return []
    return _cap(_SHELL_ORACLES[family](path, text))


# -- 4. Unsafe deserialization ----------------------------------------------

_PY_DESER = re.compile(r"\b(?:pickle|marshal|dill)\.(loads?|Unpickler)\s*\(")
_YAML_UNSAFE = re.compile(r"\byaml\.load\s*\(")
_NET_DESER = re.compile(r"\b(?:BinaryFormatter|SoapFormatter|NetDataContractSerializer)")
_FMT_DESERIALIZE = re.compile(r"\.Deserialize\s*\(")


def _py_deserialization_findings(path: Path, text: str) -> list[Finding]:
    """pickle/marshal loads and yaml.load without a safe loader."""
    out: list[Finding] = []
    for n, line in _code_lines(text):
        if _PY_DESER.search(line) or (_YAML_UNSAFE.search(line) and "Loader=" not in line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "unsafe-deserialization",
                    "pickle/marshal/yaml.load reconstructs arbitrary "
                    "objects - parse untrusted input with a safe loader",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return out


def _csharp_deserialization_findings(path: Path, text: str) -> list[Finding]:
    """The legacy .NET formatters paired with a Deserialize call."""
    out: list[Finding] = []
    if not _FMT_DESERIALIZE.search(text):
        return out
    for n, line in _code_lines(text):
        if _NET_DESER.search(line):
            out.append(
                _finding(
                    path,
                    text,
                    _offset(text, n),
                    "unsafe-deserialization",
                    "BinaryFormatter/SoapFormatter deserialization - the "
                    "legacy formatters execute attacker-chosen types; "
                    "migrate to a data-only format",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return out


def check_unsafe_deserialization(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """Formats that instantiate attacker-chosen types on load."""
    if _skip(rel):
        return []
    if family == "py":
        return _cap(_py_deserialization_findings(path, text))
    if family == "csharp":
        return _cap(_csharp_deserialization_findings(path, text))
    return []


# -- 5. Memory-unsafe C calls ------------------------------------------------

_UNSAFE_C = re.compile(r"\b(gets|strcpy|strcat|sprintf)\s*\(")


def check_unsafe_c_call(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """gets/strcpy/strcat/sprintf: no bounds checking, buffer overflows."""
    if family != "cpp" or _skip(rel):
        return []
    code = _strip_strings(text)
    out: list[Finding] = []
    for n, line in _code_lines(code):
        m = _UNSAFE_C.search(line)
        if m:
            out.append(
                _finding(
                    path,
                    text,
                    _offset(code, n),
                    "unsafe-c-call",
                    f"memory-unsafe C string function ({m.group(1)}) - use "
                    "the bounds-checked form (fgets/strncpy/snprintf) or "
                    "RAII containers",
                    5,
                )
            )
            if len(out) >= 5:
                break
    return _cap(out)


# -- 6. Format-specific hardcoded secrets ------------------------------------

SECRET_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "JWT token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    ("PEM private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "database URL with embedded password",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)"
            r"://[^/\s:@]+:[^@\s]{6,}@"
        ),
    ),
)


def check_hardcoded_secret(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """Credential formats the naming-based placeholder oracle cannot see."""
    if family == "" or _skip(rel) or path.suffix.lower() in {".md", ".json"}:
        return []
    out: list[Finding] = []
    for n, line in _code_lines(text):
        for label, pattern in SECRET_FORMATS:
            if pattern.search(line):
                out.append(
                    _finding(
                        path,
                        text,
                        _offset(text, n),
                        "hardcoded-secret",
                        f"hardcoded credential format detected: {label} - "
                        "move it to a secret store and rotate the value",
                        5,
                    )
                )
                if len(out) >= 5:
                    return out
                break
    return out


def check_security_column(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """The per-file security column, dispatched from the scan pipeline."""
    findings: list[Finding] = []
    findings.extend(check_innerhtml(path, text, family, rel))
    findings.extend(check_dangerously_set_innerhtml(path, text, family, rel))
    findings.extend(check_sql_injection(path, text, family, rel))
    findings.extend(check_shell_injection(path, text, family, rel))
    findings.extend(check_unsafe_deserialization(path, text, family, rel))
    findings.extend(check_unsafe_c_call(path, text, family, rel))
    findings.extend(check_hardcoded_secret(path, text, family, rel))
    return findings
