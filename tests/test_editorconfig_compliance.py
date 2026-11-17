"""The editorconfig parity gate: Python files pad to multiples of four.

Hosted CI's hygiene job runs editorconfig-checker (``[*.py] indent_size
= 4`` means every line's leading spaces must be a multiple of four) -
and that checker sees docstring and template prose that ``ruff format``
never touches, so round-11's aligned docstrings passed every sandbox
gate and still red'd the hosted ``hygiene`` job (11 errors across
hook_gate.py, hook_install.py, slop_catalog.py; the same class already
red'd once before, fixed in 96c6cb5). This test mirrors exactly the
hosted rule that bit twice, over the same tree the checker walks, so
the suite catches it before the push instead of the runner after.

Scope is deliberately the two rules that produce hosted reds undetectable
by ruff/mypy: Python left-padding multiples and the ``[*]`` finals -
trailing whitespace, final newline. Markdown files opt out of indent
checks in .editorconfig, so prose is not re-judged here.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Generated and vendored trees the hosted checker's default excludes
# keep out; the repo has no .gitignore-level exclusion for them.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "_ci"}


def _python_files() -> list[Path]:
    """Every .py file the hosted checker would walk under the repo root."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def test_python_left_padding_is_multiple_of_four() -> None:
    """Mirrors editorconfig-checker: ``[*.py] indent_size = 4``."""
    offenders: list[str] = []
    for path in _python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.lstrip(" ")
            pad = len(line) - len(stripped)
            if stripped and pad % 4 != 0:
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno} pads {pad}")
    assert offenders == [], (
        "leading spaces must be a multiple of 4 in .py files "
        "(hosted hygiene gate parity):\n" + "\n".join(offenders)
    )


def test_python_trailing_whitespace_and_final_newline() -> None:
    """Mirrors the ``[*]`` rules: trim trailing, keep one final newline."""
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            offenders.append(f"{path.relative_to(ROOT)}: missing final newline")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line != line.rstrip():
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno} trailing whitespace")
    assert offenders == [], "editorconfig [*] parity:\n" + "\n".join(offenders)
