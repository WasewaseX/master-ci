#!/usr/bin/env python3
"""hook_gate.py - the fast per-edit gate behind the agent hooks.

The scanaislop product's sharpest loop is not CI, it is the per-edit
hook: the coding agent runs the scanner on every file it touches, so
slop is fixed while the file is still open. Master CI had the engines
but no per-edit entry point - the full scan answers "is the repo
clean", never "is this edit clean". Round 11 adds the missing loop
half.

The gate runs the same per-file engines the full scan runs (vocabulary,
AST checks, family slop oracles, residue) over ONLY the files the agent
touched, then decides by the same registry: a blocking-grade finding
exits 1, advisory findings print and exit 0. The repo-level views
(duplication, import cycles, coupling, coverage) are deliberately out
of scope here - they need the whole tree and belong to the CI scan;
a hook that scanned everything would be too slow to stay installed.

Invocation:
- file list:      python3 scripts/hook_gate.py src/a.py src/b.ts
- Claude Code:      python3 scripts/hook_gate.py --stdin-claude
                    (PostToolUse hook: reads the tool payload on stdin,
                    gates the file the agent just edited)
- pre-commit:       python3 scripts/hook_gate.py $(git diff --cached
                    --name-only --diff-filter=ACMR)

Exit codes: 0 clean or advisory-only; 1 at least one blocking finding;
2 the gate itself was misconfigured (no files, an unusable root).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from slop_files import load_phrases
from slop_imports import collect_js_dependencies, collect_py_dependencies
from slop_scan import ScanContext, _scan_one_file


def _claude_stdin_files(payload: str) -> list[str]:
    """File paths out of a Claude Code PostToolUse payload."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    return [path] if path else []


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-edit master-ci gate")
    parser.add_argument("files", nargs="*", help="Files to gate")
    parser.add_argument(
        "--stdin-claude",
        action="store_true",
        help="Read a Claude Code PostToolUse JSON payload from stdin",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root for dependency context (default: cwd)",
    )
    args = parser.parse_args(argv)
    if args.stdin_claude:
        payload = sys.stdin.read()
        paths = _claude_stdin_files(payload)
    else:
        paths = list(args.files)
    if not paths:
        print("hook-gate: no files to gate", file=sys.stderr)
        return 2
    base = (args.root or Path.cwd()).resolve()
    ctx = ScanContext(
        load_phrases(),
        collect_py_dependencies(base),
        collect_js_dependencies(base),
        provenance=False,
    )
    blocking: list = []
    advisory: list = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue  # deleted between the edit and the gate: nothing to gate
        rel = _rel(path, base)
        findings = _scan_one_file(path, path.suffix.lower(), rel, ctx)
        for finding in findings:
            (blocking if not finding.advisory else advisory).append(finding)
    for finding in blocking:
        print(
            f"BLOCKING {finding.path}:{finding.line} [{finding.check}] "
            f"{finding.message} | fix: {finding.remedy}"
        )
    for finding in advisory:
        print(
            f"advisory {finding.path}:{finding.line} [{finding.check}] "
            f"{finding.message} | fix: {finding.remedy}"
        )
    if blocking:
        print(
            f"hook-gate: {len(blocking)} blocking finding(s) in "
            f"{len(paths)} file(s) - fix before this lands"
        )
        return 1
    if advisory:
        print(f"hook-gate: clean for the gate, {len(advisory)} advisory note(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
