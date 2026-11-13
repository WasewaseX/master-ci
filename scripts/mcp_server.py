#!/usr/bin/env python3
"""mcp_server.py - Model Context Protocol server for Master CI.

Any MCP-capable AI assistant (Claude Desktop, Cursor, Cline, Windsurf,
z.ai and friends) can now drive the Master CI engines directly: scan for
AI slop, ask which checks a change set would trigger, dry-run the fix
policy, apply it, and read the check catalog. One engine, many surfaces -
the same philosophy vibecheck uses, applied to the whole template. Every
tool result embeds a fix-ready `prompt` field: the next instruction the
assistant should execute, so data never arrives without a plan. Ready
made walkthroughs are also served through the MCP prompts primitive.

Two meta tools keep an install trustworthy. `diagnose` reports the
server version, the loaded engines, and which external formatters are on
PATH. `check_updates` compares the installed version against the newest
tag on GitHub and returns the exact command to update - the server never
updates anything by itself, and it never touches the network except when
this tool is called.

Transport is stdio with newline-delimited JSON-RPC 2.0, exactly as the
MCP spec defines it. The server is stdlib-only: no SDK, no pip install,
no required network. Launch it with `python3 scripts/mcp_server.py` and
point any client's mcpServers config at it; `scripts/mcp_ping.py` is a
standalone client that proves an install works end to end.

Tools are thin wrappers over the real engines (ai_slop_detector.py,
fix_engine.py, path_filters.py), so an MCP scan reports exactly what CI
would report and an MCP fix applies exactly what the autofix loop would
apply. `apply_fixes` is the only mutating tool; everything else is
read-only. Mutating tools REQUIRE the MASTER_CI_MCP_ROOT environment
variable: without it apply_fixes refuses to run, and with it every path
argument must resolve inside that directory. The lock is enforced, not
recommended - the server can never be tricked into editing files outside
the one repository it was configured for.

Protocol support spans the full MCP timeline: the legacy handshake era
(2024-11-05 through 2025-11-25, where clients call `initialize` first)
and the modern stateless era (2026-07-28, where every request carries
`_meta["io.modelcontextprotocol/protocolVersion"]` and `server/discover`
replaces the handshake). Unknown per-request versions are rejected with
the standard -32022 UnsupportedProtocolVersionError, which lists the
versions this server does support.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# Sibling modules: anchor this file's directory so importlib-by-path loads
# (the contract tests, mcp_ping) resolve them from any cwd.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp_meta import (  # noqa: E402,F401
    tool_bench,
    tool_check_updates,
    tool_diagnose,
    tool_filter_paths,
    tool_list_checks,
)
from mcp_prompts import (  # noqa: E402,F401
    PROMPTS,
    _prompt_explain_findings,
    _prompt_fix_my_repo,
    _prompt_pre_flight,
)
from mcp_registry import TOOLS  # noqa: E402,F401
from mcp_tools import (  # noqa: E402,F401
    DEFAULT_PROTOCOL_VERSION,
    MAX_FINDINGS_IN_RESPONSE,
    META_PROTOCOL_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSIONS,
    SCRIPTS,
    SERVER_VERSION,
    UNSUPPORTED_PROTOCOL_VERSION_ERROR,
    _require_write_root,
    _resolve,
    detector,
    fix_engine,
    path_filters,
    tool_apply_fixes,
    tool_plan_fixes,
    tool_scan_diff,
    tool_scan_slop,
)

# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "scan_slop": tool_scan_slop,
    "scan_diff": tool_scan_diff,
    "plan_fixes": tool_plan_fixes,
    "apply_fixes": tool_apply_fixes,
    "list_checks": tool_list_checks,
    "filter_paths": tool_filter_paths,
    "diagnose": tool_diagnose,
    "check_updates": tool_check_updates,
}


# ---------------------------------------------------------------------------
# MCP prompts: ready-made walkthroughs (prompts/list, prompts/get)
# ---------------------------------------------------------------------------


PROMPT_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "fix-my-repo": _prompt_fix_my_repo,
    "explain-findings": _prompt_explain_findings,
    "pre-flight": _prompt_pre_flight,
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (MCP stdio: one message per line)
# ---------------------------------------------------------------------------


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, payload: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _write({"jsonrpc": "2.0", "id": req_id, "error": err})


def _tool_result(req_id: Any, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2)
    _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _tool_error(req_id: Any, message: str) -> None:
    _result(req_id, {"content": [{"type": "text", "text": message}], "isError": True})


def _respond_discover(req_id: Any, params: dict[str, Any]) -> None:
    """server/discover - the stateless capability query of the 2026-07-28 era."""
    meta = params.get("_meta") if isinstance(params, dict) else None
    client_info = meta.get("io.modelcontextprotocol/clientInfo") if isinstance(meta, dict) else None
    _result(
        req_id,
        {
            "resultType": "complete",
            "supportedVersions": list(PROTOCOL_VERSIONS),
            "capabilities": {"tools": {}, "prompts": {}},
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "master-ci",
                    "version": SERVER_VERSION,
                }
            },
            "instructions": (
                "Call tools/list to discover the engines. Every tool result embeds "
                "a fix-ready prompt field with the next step. apply_fixes mutates "
                "files and therefore requires MASTER_CI_MCP_ROOT; everything else "
                "is read-only."
            ),
            "client_echo": client_info if isinstance(client_info, dict) else None,
        },
    )


def _respond_initialize(req_id: Any, params: dict[str, Any]) -> None:
    requested = str(params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION))
    agreed = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    _result(
        req_id,
        {
            "protocolVersion": agreed,
            "capabilities": {"tools": {}, "prompts": {}},
            "serverInfo": {"name": "master-ci", "version": SERVER_VERSION},
            "instructions": (
                "Call tools/list to discover the engines. Every tool result embeds "
                "a fix-ready prompt field with the next step; prompts/list serves "
                "ready made walkthroughs (fix-my-repo, explain-findings, "
                "pre-flight). apply_fixes requires MASTER_CI_MCP_ROOT in the env "
                "block - it refuses to mutate anything without it."
            ),
        },
    )


def _respond_tools_call(req_id: Any, params: dict[str, Any]) -> None:
    name = str(params.get("name", ""))
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        _error(req_id, -32602, f"unknown tool: {name}")
        return
    try:
        _tool_result(req_id, handler(dict(params.get("arguments") or {})))
    except Exception as exc:  # noqa: BLE001 - tool failures are results, not crashes
        _tool_error(req_id, f"{type(exc).__name__}: {exc}")


def _respond_prompts_get(req_id: Any, params: dict[str, Any]) -> None:
    prompt_name = str(params.get("name", ""))
    builder = PROMPT_BUILDERS.get(prompt_name)
    if builder is None:
        _error(req_id, -32602, f"unknown prompt: {prompt_name}")
        return
    try:
        text = builder(dict(params.get("arguments") or {}))
    except Exception as exc:  # noqa: BLE001 - bad prompt args are client errors
        _error(req_id, -32602, f"invalid prompt arguments: {type(exc).__name__}: {exc}")
        return
    description = next(p["description"] for p in PROMPTS if p["name"] == prompt_name)
    _result(
        req_id,
        {
            "description": description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        },
    )


_SIMPLE_RESPONSES: dict[str, Callable[[], dict[str, Any]]] = {
    "ping": lambda: {},
    "tools/list": lambda: {"tools": TOOLS},
    "prompts/list": lambda: {"prompts": PROMPTS},
    "resources/list": lambda: {"resources": []},
}

_PARAM_HANDLERS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "initialize": _respond_initialize,
    "tools/call": _respond_tools_call,
    "prompts/get": _respond_prompts_get,
    "server/discover": _respond_discover,
}


def _meta_protocol_version(params: dict[str, Any]) -> str | None:
    """Per-request protocol version from _meta (the 2026-07-28 negotiation)."""
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    version = meta.get(META_PROTOCOL_KEY)
    return str(version) if version else None


def handle_request(msg: dict[str, Any]) -> None:
    """Dispatch one JSON-RPC request or notification."""
    method = str(msg.get("method", ""))
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if req_id is None:  # notification: never responded to
        return
    # 2026-07-28 era: every request carries its protocol version in _meta.
    # Unknown versions get the spec'd rejection with the supported list;
    # requests without _meta fall through to the legacy initialize flow.
    requested_version = _meta_protocol_version(params)
    if requested_version is not None and requested_version not in PROTOCOL_VERSIONS:
        _error(
            req_id,
            UNSUPPORTED_PROTOCOL_VERSION_ERROR,
            "Unsupported protocol version",
            {"supported": list(PROTOCOL_VERSIONS), "requested": requested_version},
        )
        return
    if method in _SIMPLE_RESPONSES:
        _result(req_id, _SIMPLE_RESPONSES[method]())
    elif method in _PARAM_HANDLERS:
        _PARAM_HANDLERS[method](req_id, params)
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
