#!/usr/bin/env python3
"""mcp_server.py - Model Context Protocol server for Master CI.

Any MCP-capable AI assistant (Claude Desktop, Cursor, Cline, Windsurf,
z.ai and friends) can now drive the Master CI engines directly: scan for
AI slop, ask which checks a change set would trigger, dry-run the fix
policy, apply it, and read the check catalog. One engine, many surfaces -
the same philosophy vibecheck uses, applied to the whole template.

Transport is stdio with newline-delimited JSON-RPC 2.0, exactly as the
MCP spec defines it. The server is stdlib-only: no SDK, no pip install,
no network. Launch it with `python3 scripts/mcp_server.py` and point any
client's mcpServers config at it.

Tools are thin wrappers over the real engines (ai_slop_detector.py,
fix_engine.py, path_filters.py), so an MCP scan reports exactly what CI
would report and an MCP fix applies exactly what the autofix loop would
apply. `apply_fixes` is the only mutating tool; everything else is
read-only. If the MASTER_CI_MCP_ROOT environment variable is set, every
path argument must resolve inside that directory - a simple lock for
users who want the server pinned to one repository.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
MAX_FINDINGS_IN_RESPONSE = 200


def _load(name: str):
    """Import a sibling engine module by path (works from any cwd)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detector = _load("ai_slop_detector")
fix_engine = _load("fix_engine")
path_filters = _load("path_filters")


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    """Resolve a tool path argument and enforce the optional root lock."""
    root_lock = os.environ.get("MASTER_CI_MCP_ROOT")
    resolved = Path(path).expanduser().resolve()
    if root_lock:
        locked = Path(root_lock).expanduser().resolve()
        if resolved != locked and locked not in resolved.parents:
            raise ValueError(f"path {resolved} is outside MASTER_CI_MCP_ROOT ({locked})")
    if not resolved.exists():
        raise ValueError(f"path does not exist: {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# tool implementations - each returns a JSON-serializable payload
# ---------------------------------------------------------------------------


def tool_scan_slop(args: dict[str, Any]) -> dict[str, Any]:
    """Scan a path with the AI-integrity engine (read-only)."""
    root = _resolve(str(args.get("path", ".")))
    max_score = int(args.get("max_score", 8))
    provenance = bool(args.get("provenance", True))
    phrases = detector.load_phrases()
    findings = detector.scan(root, phrases, provenance=provenance)

    per_file: dict[str, list] = {}
    for finding in findings:
        per_file.setdefault(finding.path, []).append(finding)
    deficits = [detector.file_deficit(items) for items in per_file.values()]
    score, grade, verdict = detector.project_score(deficits)
    blocking = [f for f in findings if not f.advisory]
    advisory = [f for f in findings if f.advisory]

    serialized = [
        {
            "path": f.path,
            "line": f.line,
            "check": f.check,
            "message": f.message,
            "weight": f.weight,
            "advisory": f.advisory,
            "remedy": f.remedy,
        }
        for f in (blocking + advisory)[:MAX_FINDINGS_IN_RESPONSE]
    ]
    return {
        "path": str(root),
        "score": score,
        "grade": grade,
        "verdict": verdict,
        "blocking_score": detector.blocking_score(findings),
        "blocking_gate": {
            "max_score": max_score,
            "would_fail_ci": detector.blocking_score(findings) > max_score,
        },
        "advisory_score": detector.advisory_score(findings),
        "files_with_findings": len(per_file),
        "total_findings": len(findings),
        "truncated": len(findings) > MAX_FINDINGS_IN_RESPONSE,
        "findings": serialized,
        "note": "blocking findings gate CI above max_score; advisory never gate",
    }


def _changed_files(root: Path, base: str, include_uncommitted: bool) -> list[str]:
    """Changed paths for scan_diff: git diff against base, optionally vs HEAD."""
    paths: list[str] = []
    if base:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", base],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise ValueError(f"git diff failed: {out.stderr.strip()[:200]}")
        paths.extend(line for line in out.stdout.splitlines() if line)
    if include_uncommitted:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            paths.extend(line for line in out.stdout.splitlines() if line)
    return sorted(set(paths))


def tool_scan_diff(args: dict[str, Any]) -> dict[str, Any]:
    """Scan only the changed files of a repository (read-only)."""
    root = _resolve(str(args.get("path", ".")))
    base = str(args.get("base", "")).strip()
    include_uncommitted = bool(args.get("include_uncommitted", not base))
    if not (root / ".git").exists():
        raise ValueError(f"not a git repository: {root}")
    changed = _changed_files(root, base, include_uncommitted)
    phrases = detector.load_phrases()
    findings = detector.scan(root, phrases)
    changed_set = set(changed)
    hits = [f for f in findings if f.path in changed_set]
    filters, source = path_filters.filters_for_repo(root)
    fired = path_filters.stacks_for_paths(changed, filters)
    blocking = detector.blocking_score(hits)
    return {
        "path": str(root),
        "base": base or "HEAD (uncommitted)",
        "changed_files": changed,
        "changed_count": len(changed),
        "stacks_fired": sorted(fired),
        "filter_source": source,
        "blocking_score": blocking,
        "findings": [
            {
                "path": f.path,
                "line": f.line,
                "check": f.check,
                "message": f.message,
                "weight": f.weight,
                "advisory": f.advisory,
                "remedy": f.remedy,
            }
            for f in hits[:MAX_FINDINGS_IN_RESPONSE]
        ],
    }


def _tier_list(args: dict[str, Any]) -> list[str]:
    raw = args.get("tiers", ["safe"])
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    tiers = [tier for tier in raw if tier in ("safe", "lint")]
    return tiers or ["safe"]


def tool_plan_fixes(args: dict[str, Any]) -> dict[str, Any]:
    """Dry-run the fix policy: what WOULD be fixed, nothing touched."""
    root = _resolve(str(args.get("path", ".")))
    tiers = _tier_list(args)
    semgrep_config = str(args.get("semgrep_config", ""))
    plan = fix_engine.build_plan(root, set(tiers), semgrep_config)
    generic = fix_engine.scan_generic(root, dry_run=True)
    return {
        "path": str(root),
        "tiers": tiers,
        "planned_tools": [{"name": entry["name"], "tier": entry["tier"]} for entry in plan],
        "generic": generic,
        "note": "dry-run: no file was modified; use apply_fixes to execute",
    }


def tool_apply_fixes(args: dict[str, Any]) -> dict[str, Any]:
    """Run the fix policy for real (MUTATES files under path)."""
    root = _resolve(str(args.get("path", ".")))
    tiers = _tier_list(args)
    semgrep_config = str(args.get("semgrep_config", ""))
    plan = fix_engine.build_plan(root, set(tiers), semgrep_config)
    results: list[dict[str, Any]] = []
    generic: dict[str, Any] = {"files_scanned": 0, "files_changed": 0, "skipped": {}}
    with tempfile.TemporaryDirectory(prefix="master-ci-mcp-") as tmp:
        for entry in plan:
            name = entry["name"]
            if name == "generic":
                generic = fix_engine.scan_generic(root, dry_run=False)
                results.append(
                    {
                        "name": "generic",
                        "tier": "safe",
                        "status": "fixed",
                        "detail": (
                            f"{generic['files_changed']} of "
                            f"{generic['files_scanned']} files normalized"
                        ),
                    }
                )
            elif name == "semgrep":
                status, detail = fix_engine.tool_semgrep(root, semgrep_config, False)
                results.append({"name": name, "tier": "lint", "status": status, "detail": detail})
            else:
                status, detail = entry["runner"](root, dry_run=False)
                results.append(
                    {"name": name, "tier": entry["tier"], "status": status, "detail": detail}
                )
        fix_engine.write_reports(
            Path(tmp), root, sorted(set(tiers)), semgrep_config, results, generic, False
        )
        summary = (Path(tmp) / "summary.md").read_text(encoding="utf-8")
    changed_any = any(r["status"] == "fixed" for r in results)
    return {
        "path": str(root),
        "tiers": sorted(set(tiers)),
        "changed_any": changed_any,
        "tools": results,
        "generic": generic,
        "summary": summary,
        "note": "files were modified in place; review with git diff and commit what you want",
    }


def tool_list_checks(_args: dict[str, Any]) -> dict[str, Any]:
    """The check catalog: detector rules, remedies, fix policy, scoring."""
    phrases = detector.load_phrases()
    return {
        "detector": {
            "blocking_checks": sorted(detector.BLOCKING_CHECKS),
            "phrase_categories": {name: len(entries) for name, entries in phrases.items()},
            "phrases": phrases,
            "remedies": dict(detector.REMEDIES),
            "scoring": "project = min(100, 0.6*worst_file_deficit + 0.4*mean_file_deficit)",
            "advisory_note": "provenance and prose findings never gate CI",
        },
        "fix_policy": {
            "safe_tier": [
                "ruff format",
                "rustfmt",
                "gofmt",
                "biome",
                "shfmt",
                "markdownlint --fix",
                "generic whitespace/EOF normalizer",
            ],
            "lint_tier": [
                "ruff check --fix",
                "go mod tidy",
                "sqlfluff fix",
                "semgrep --autofix",
            ],
            "never_auto_fixed": [
                "secrets (rotate)",
                "dependency vulnerabilities (upgrade decisions)",
                "AI-integrity prose findings (meaning)",
            ],
        },
        "ci_catalog": "docs/checks.md in the master-ci repository (80+ checks)",
    }


def tool_filter_paths(args: dict[str, Any]) -> dict[str, Any]:
    """Which CI stacks would fire for a change set (uses the live table)."""
    raw = args.get("files", [])
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines() if line.strip()]
    files = [str(f) for f in raw]
    if not files:
        raise ValueError("files must be a non-empty list of repo-relative paths")
    root_arg = args.get("path")
    root = _resolve(str(root_arg)) if root_arg else Path.cwd()
    filters, source = path_filters.filters_for_repo(root)
    fired = path_filters.stacks_for_paths(files, filters)
    detail = {
        path: sorted(name for name, fs in filters.items() if fs.matches(path)) for path in files
    }
    return {
        "fired": sorted(fired),
        "idle": sorted(set(filters) - fired),
        "source": source,
        "detail": detail,
        "note": "source=live reads .github/workflows/master-ci.yml; fallback uses the committed snapshot",
    }


# ---------------------------------------------------------------------------
# tool metadata: names, schemas, descriptions (MCP tools/list)
# ---------------------------------------------------------------------------

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
]

TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "scan_slop": tool_scan_slop,
    "scan_diff": tool_scan_diff,
    "plan_fixes": tool_plan_fixes,
    "apply_fixes": tool_apply_fixes,
    "list_checks": tool_list_checks,
    "filter_paths": tool_filter_paths,
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (MCP stdio: one message per line)
# ---------------------------------------------------------------------------


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, payload: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _tool_result(req_id: Any, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2)
    _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _tool_error(req_id: Any, message: str) -> None:
    _result(req_id, {"content": [{"type": "text", "text": message}], "isError": True})


def handle_request(msg: dict[str, Any]) -> None:
    """Dispatch one JSON-RPC request or notification."""
    method = str(msg.get("method", ""))
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:  # notification: never responded to
        return

    if method == "initialize":
        requested = str(params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION))
        agreed = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        _result(
            req_id,
            {
                "protocolVersion": agreed,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "master-ci", "version": SERVER_VERSION},
            },
        )
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = str(params.get("name", ""))
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            _tool_error(req_id, f"unknown tool: {name}")
            return
        try:
            _tool_result(req_id, handler(dict(params.get("arguments") or {})))
        except Exception as exc:  # noqa: BLE001 - tool failures are results, not crashes
            _tool_error(req_id, f"{type(exc).__name__}: {exc}")
    elif method == "prompts/list" or method == "resources/list":
        _result(req_id, {"prompts": []} if method == "prompts/list" else {"resources": []})
    else:
        _error(req_id, -32601, f"method not found: {method}")


def serve(stdin_reader=sys.stdin) -> int:
    """Main loop: read JSON-RPC lines until EOF."""
    print(
        f"[master-ci-mcp] v{SERVER_VERSION} ready: {len(TOOLS)} tools, "
        "protocol " + ", ".join(PROTOCOL_VERSIONS),
        file=sys.stderr,
    )
    for line in stdin_reader:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error: line is not valid JSON")
            continue
        if not isinstance(msg, dict):
            _error(None, -32600, "invalid request: expected an object")
            continue
        try:
            handle_request(msg)
        except Exception as exc:  # noqa: BLE001 - the server must never die on a message
            req_id = msg.get("id")
            if req_id is not None:
                _error(req_id, -32603, f"internal error: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(serve())
