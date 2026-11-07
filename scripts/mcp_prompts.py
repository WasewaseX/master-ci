"""MCP prompt templates and the prompt registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_tools import _resolve, _tier_list


def _prompt_fix_my_repo(args: dict[str, Any]) -> str:
    """The full fix loop as one paste-ready instruction."""
    root = _resolve(str(args.get("path", ".")))
    tiers_json = json.dumps(_tier_list(args))
    return (
        f"Improve the repository at {root} with the master-ci tools, in this order. "
        f'1. scan_slop {{"path": "{root}"}}: read the score, blocking_score and '
        "findings. 2. Fix prose-level findings by editing deliberately; meaning-level "
        "rewrites are never mechanical. 3. plan_fixes "
        f'{{"path": "{root}", "tiers": {tiers_json}}}: preview the mechanical '
        f'cleanup. 4. apply_fixes {{"path": "{root}", "tiers": {tiers_json}}}: '
        "execute it. 5. Re-run scan_slop and plan_fixes: the blocking score should drop "
        "and the plan should report zero files changed. 6. Show git diff --stat and "
        "summarize what changed and why."
    )


def _prompt_explain_findings(args: dict[str, Any]) -> str:
    """Reviewer-style explanation prompt for a scan."""
    root = _resolve(str(args.get("path", ".")))
    return (
        f"Explain the master-ci scan of {root} like a code reviewer. Call scan_slop "
        f'with {{"path": "{root}"}}, then: summarize what the score and grade mean '
        "for this repository; group the findings by check; say which are mechanical "
        "(fixable by apply_fixes) and which are meaning-level and need real edits; "
        "propose the exact apply_fixes call you would make; state what CI would do with "
        "the current blocking score."
    )


def _prompt_pre_flight(args: dict[str, Any]) -> str:
    """Pre-push check: which CI stacks fire and how the diff scores."""
    raw = args.get("files", [])
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines() if line.strip()]
    files = [str(item) for item in raw]
    if not files:
        raise ValueError("files must be a non-empty list of repo-relative paths")
    root_arg = args.get("path")
    root = _resolve(str(root_arg)) if root_arg else Path.cwd()
    files_json = json.dumps(files[:20])
    filter_call = f'filter_paths {{"files": {files_json}, "path": "{root}"}}'
    diff_call = f'scan_diff {{"path": "{root}"}}'
    return (
        f"Pre-flight check before pushing changes in {root}. Call {filter_call} to see "
        f"which CI stacks fire, then call {diff_call} to score the changed files. "
        "Report which checks CI will run, the blocking score, and what to fix before "
        "opening the PR."
    )


PROMPTS: list[dict[str, Any]] = [
    {
        "name": "fix-my-repo",
        "description": "Fix-ready walkthrough: scan, plan, apply, verify, report.",
        "arguments": [
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
            {
                "name": "tiers",
                "description": "fix tiers, safe (default) or safe and lint",
                "required": False,
            },
        ],
    },
    {
        "name": "explain-findings",
        "description": (
            "Reviewer-style explanation of a scan: what is mechanical, what needs real edits."
        ),
        "arguments": [
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
        ],
    },
    {
        "name": "pre-flight",
        "description": "Before pushing: which CI stacks fire and how the changed files score.",
        "arguments": [
            {
                "name": "files",
                "description": "repo-relative changed paths (list or newline text)",
                "required": True,
            },
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
        ],
    },
]
