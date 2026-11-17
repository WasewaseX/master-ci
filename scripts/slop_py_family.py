"""The Python family column, round-9 of the scanaislop adoption.

Two oracles, both zero-dependency and AST-backed:

- broad-except: a typed handler naming Exception/BaseException catches
    every error the call site can grow, including the ones nobody has
    invented yet. The same failure-path grade its C# sibling
    (``csharp-broad-catch``) got in round 8. Exempted: pass-only
    handlers (``swallowed-exception`` already owns them), a lone
    re-raise (a translation point, not a swallow - SonarQube's
    broad-catch rule exempts it the same way) and a declared
    ``# noqa: BLE001`` intent, the directive the shipped linter reads.
- debug-print (Python): print() debug output in production modules,
    the same advisory file-level grade as the JVM/Go/JS oracles.
    Reversed from the scanaislop python column's scoping: test paths,
    script directories, entrypoint basenames and main-guard files
    print on purpose - a file that runs as a tool is its own consumer.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath

from slop_files import is_test_path
from slop_model import Finding

BROAD_EXCEPT_NAMES = frozenset({"Exception", "BaseException"})

# Entrypoint basenames whose print() output is the product, not residue.
ENTRYPOINT_BASENAMES = frozenset(
    {"__main__.py", "manage.py", "setup.py", "cli.py", "main.py", "conftest.py"}
)

# Directories where printing is the deliverable: tools, docs, examples.
SCRIPT_DIR_NAMES = frozenset({"scripts", "bin", "examples", "example", "docs", "docs_src"})

MAIN_GUARD_RE = re.compile(r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", re.MULTILINE)

# A bare noqa directive (no code) would read as a blanket suppression;
# the declared form names the code it waives.
DECLARED_BROAD_RE = re.compile(r"#\s*noqa\s*:\s*[A-Z]+[0-9]*")


def except_type_names(handler: ast.ExceptHandler) -> list[str]:
    """The leaf names a handler's type clause names (Name/Attribute/Tuple)."""
    clause = handler.type
    if isinstance(clause, ast.Name):
        return [clause.id]
    if isinstance(clause, ast.Attribute):
        return [clause.attr]
    if isinstance(clause, ast.Tuple):
        names: list[str] = []
        for elt in clause.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Attribute):
                names.append(elt.attr)
        return names
    return []


def _bare_reraise(body: list[ast.stmt]) -> bool:
    """A lone ``raise`` re-throws the caught error - a translation point."""
    return len(body) == 1 and isinstance(body[0], ast.Raise) and body[0].exc is None


def _declared_broad(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """A coded ``# noqa: BLE001`` on the except line declares the intent."""
    if not 1 <= handler.lineno <= len(lines):
        return False
    return bool(DECLARED_BROAD_RE.search(lines[handler.lineno - 1]))


def check_py_broad_except_handler(
    path: Path, handler: ast.ExceptHandler, lines: list[str]
) -> list[Finding]:
    """Round-9: one finding per broad catch the exemptions do not own."""
    names = except_type_names(handler)
    if not any(name in BROAD_EXCEPT_NAMES for name in names):
        return []
    if all(isinstance(stmt, ast.Pass) for stmt in handler.body):
        return []  # swallowed-exception owns the pass-only handler
    if _bare_reraise(handler.body) or _declared_broad(handler, lines):
        return []
    named = next(name for name in names if name in BROAD_EXCEPT_NAMES)
    return [
        Finding(
            path=str(path),
            line=handler.lineno,
            check="broad-except",
            message=(
                f"except {named} catches every error the call site can grow "
                "- catch the specific type, re-raise, or declare the "
                "broad catch with # noqa: BLE001"
            ),
            weight=1,
            advisory=True,
        )
    ]


def check_py_print_debug(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    """print() debug output in production Python, advisory and counted.

    AST-backed, so comments and string interiors cannot false-positive.
    """
    if family != "py" or is_test_path(rel):
        return []
    norm = PurePosixPath(rel.replace("\\", "/"))
    if norm.name in ENTRYPOINT_BASENAMES or any(
        part in SCRIPT_DIR_NAMES for part in norm.parts[:-1]
    ):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    if MAIN_GUARD_RE.search(text):
        return []
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    if not calls:
        return []
    return [
        Finding(
            path=str(path),
            line=calls[0],
            check="debug-print",
            message=f"print() debug output ({len(calls)}x)",
            weight=1,
            advisory=True,
        )
    ]
