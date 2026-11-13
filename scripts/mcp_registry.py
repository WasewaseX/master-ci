"""The MCP tool registry: schemas for every tool the server exposes."""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "scan_slop",
        "description": (
            "Scan a file or directory with the Master CI AI-integrity engine. "
            "Returns the project slop score (0-100 with grade and verdict), the "
            "blocking score that CI gates on, and every finding with its "
            "machine-applicable remedy. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file or directory to scan"},
                "max_score": {"type": "integer", "description": "CI gate threshold", "default": 8},
                "provenance": {
                    "type": "boolean",
                    "description": "include AI-provenance findings",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "scan_diff",
        "description": (
            "Scan only the changed files of a git repository: uncommitted "
            "changes by default, or a diff against a base ref. Also reports "
            "which CI stacks the change set activates. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "base": {
                    "type": "string",
                    "description": "git ref to diff against (default: uncommitted vs HEAD)",
                },
                "include_uncommitted": {"type": "boolean", "default": True},
            },
            "required": [],
        },
    },
    {
        "name": "plan_fixes",
        "description": (
            "Dry-run of the Master CI fix policy for a repository: which "
            "tools would run (safe and lint tiers) and how many files the "
            "whitespace normalizer would touch. Nothing is modified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "tiers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["safe", "lint"]},
                    "default": ["safe"],
                },
                "semgrep_config": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "apply_fixes",
        "description": (
            "MUTATES FILES. Runs the Master CI fix policy in place: safe tier "
            "(formatters, whitespace normalizer) and optionally the lint tier "
            "(tool-verified transforms). Review the result with git diff. The "
            "summary mirrors exactly what the autofix/self-heal loop would do."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "tiers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["safe", "lint"]},
                    "default": ["safe"],
                },
                "semgrep_config": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "list_checks",
        "description": (
            "The Master CI check catalog from the server's engines: blocking "
            "vs advisory detector rules, the phrase database, per-finding "
            "remedies, the scoring formula, and the fix policy tiers."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "filter_paths",
        "description": (
            "Ask which CI stacks a change set would activate before you write "
            "the code: uses the live dorny/paths-filter table from "
            ".github/workflows/master-ci.yml (or the committed snapshot). "
            "Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "repo-relative changed paths",
                },
                "path": {"type": "string", "description": "repository root (default: server cwd)"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "diagnose",
        "description": (
            "Environment report for this install: server and protocol versions, "
            "the loaded engines, which external formatters are on PATH and which "
            "are missing, the active path lock, and the one-command self test. "
            "Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "bench",
        "description": (
            "Run the performance benchmark of the Master CI engines (AI-integrity "
            "detector, fix engine, path filters) against a deterministic synthetic "
            "corpus and get comparable numbers for this machine. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scale": {
                    "type": "string",
                    "enum": ["small", "full"],
                    "description": "corpus size (default: small for quick checks)",
                },
                "repeat": {
                    "type": "integer",
                    "description": "best-of-N timing runs (default 1)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_updates",
        "description": (
            "Compare the installed master-ci version against the newest tag on "
            "GitHub and return the exact update command. The server never "
            "updates itself; network is only touched by this tool and offline "
            "is reported gracefully."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]
