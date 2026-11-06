"""Contract tests for the Master CI MCP server.

Covers the documented contract in scripts/mcp_server.py: MCP stdio
handshake (initialize, ping, tools/list), tool dispatch with proper
isError semantics, read-only vs mutating tools, the MASTER_CI_MCP_ROOT
path lock, and end-to-end newline-delimited JSON-RPC over a real
subprocess - the same wire behavior any MCP client (Claude, Cursor,
Cline, z.ai) will exercise.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MCP_PATH = ROOT / "scripts" / "mcp_server.py"

spec = importlib.util.spec_from_file_location("mcp_server", MCP_PATH)
assert spec is not None and spec.loader is not None
mcp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mcp
spec.loader.exec_module(mcp)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# handler-level tests (in process)
# ---------------------------------------------------------------------------


def test_scan_slop_reports_score_and_findings(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"slop.py": "# TODO: implement this\n# done\n"})
    result = mcp.tool_scan_slop({"path": str(root)})
    assert set(result) >= {"score", "grade", "verdict", "blocking_score", "findings"}
    assert result["blocking_gate"]["would_fail_ci"] in (True, False)
    assert isinstance(result["findings"], list)


def test_scan_slop_missing_path_is_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        mcp.tool_scan_slop({"path": str(tmp_path / "nope")})
    assert "does not exist" in str(exc.value)


def test_scan_diff_requires_git_repo(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"a.py": "x = 1\n"})
    with pytest.raises(ValueError) as exc:
        mcp.tool_scan_diff({"path": str(root)})
    assert "not a git repository" in str(exc.value)


def test_scan_diff_reports_stacks_and_findings(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"app.py": "# TODO: fill in\n"})
    git = ["git", "-c", "user.email=t@t.local", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=root, check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=root, check=True)
    (root / "app.py").write_text("# TODO: fill in\n# more\n", encoding="utf-8")
    result = mcp.tool_scan_diff({"path": str(root)})
    assert "app.py" in result["changed_files"]
    assert "python" in result["stacks_fired"]
    assert result["filter_source"] in ("live", "fallback")


def test_plan_fixes_never_mutates(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"a.py": "x = 1\n", "dirty.txt": "trailing   \n"})
    before = (root / "dirty.txt").read_text()
    result = mcp.tool_plan_fixes({"path": str(root), "tiers": ["safe"]})
    assert any(t["name"] == "generic" for t in result["planned_tools"])
    assert result["generic"]["files_changed"] >= 1
    assert (root / "dirty.txt").read_text() == before


def test_apply_fixes_normalizes_and_reports(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"dirty.txt": "trailing   \n\n\n"})
    result = mcp.tool_apply_fixes({"path": str(root), "tiers": ["safe"]})
    assert result["changed_any"] is True
    assert (root / "dirty.txt").read_text() == "trailing\n"
    assert "| tool |" in result["summary"]


def test_list_checks_catalog(tmp_path: Path) -> None:
    result = mcp.tool_list_checks({})
    assert result["detector"]["blocking_checks"]
    assert result["detector"]["remedies"]
    assert "ruff format" in result["fix_policy"]["safe_tier"]
    assert "secrets (rotate)" in result["fix_policy"]["never_auto_fixed"]


def test_filter_paths_uses_live_table() -> None:
    result = mcp.tool_filter_paths({"files": ["src/mod.py"], "path": str(ROOT)})
    assert result["source"] == "live"
    assert {"code", "python"} <= set(result["fired"])


def test_filter_paths_empty_files_is_error() -> None:
    with pytest.raises(ValueError) as exc:
        mcp.tool_filter_paths({"files": []})
    assert "non-empty" in str(exc.value)


# ---------------------------------------------------------------------------
# path lock
# ---------------------------------------------------------------------------


def test_master_ci_mcp_root_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MASTER_CI_MCP_ROOT", str(tmp_path))
    outside = Path(os.sep + "tmp")
    with pytest.raises(ValueError, match="MASTER_CI_MCP_ROOT"):
        mcp.tool_scan_slop({"path": str(outside)})
    inside = _make_repo(tmp_path, {"ok.py": "x = 1\n"})
    assert mcp.tool_scan_slop({"path": str(inside)})["score"] >= 0


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (in process)
# ---------------------------------------------------------------------------


def _dispatch(msg: dict, captured: list) -> None:
    """Capture mode for _write: collect messages instead of printing."""
    captured.append(msg)


def test_initialize_echoes_supported_protocol(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(mcp, "_write", lambda m: captured.append(m))
    mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert captured[0]["result"]["protocolVersion"] == "2025-06-18"
    assert captured[0]["result"]["serverInfo"]["name"] == "master-ci"


def test_initialize_falls_back_on_unknown_protocol(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(mcp, "_write", lambda m: captured.append(m))
    mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        }
    )
    assert captured[0]["result"]["protocolVersion"] == mcp.DEFAULT_PROTOCOL_VERSION


def test_unknown_method_is_32601(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(mcp, "_write", lambda m: captured.append(m))
    mcp.handle_request({"jsonrpc": "2.0", "id": 7, "method": "x/y/z"})
    assert captured[0]["error"]["code"] == -32601


def test_notifications_get_no_response(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(mcp, "_write", lambda m: captured.append(m))
    mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/cancelled"})
    assert captured == []


def test_tool_call_error_is_isError_payload(monkeypatch, tmp_path: Path) -> None:
    captured: list = []
    monkeypatch.setattr(mcp, "_write", lambda m: captured.append(m))
    mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "scan_slop", "arguments": {"path": str(tmp_path / "missing")}},
        }
    )
    result = captured[0]["result"]
    assert result["isError"] is True
    assert "does not exist" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# end-to-end over real stdio (what actual MCP clients do)
# ---------------------------------------------------------------------------


class StdioClient:
    def __init__(self, env_root: str | None = None) -> None:
        env = dict(os.environ)
        env.pop("MASTER_CI_MCP_ROOT", None)
        if env_root:
            env["MASTER_CI_MCP_ROOT"] = env_root
        self.proc = subprocess.Popen(
            [sys.executable, str(MCP_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout: float = 60.0) -> dict:
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            raise AssertionError("mcp server did not respond in time")
        line = self.proc.stdout.readline()
        return json.loads(line)

    def request(self, msg: dict) -> dict:
        self.send(msg)
        response = self.read()
        assert response.get("id") == msg.get("id"), f"id mismatch: {response}"
        return response

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)
        if self.proc.stderr:
            self.proc.stderr.close()


def test_stdio_full_session(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, {"dirty.txt": "trailing   \n\n", "README.md": "# hi\n"})
    client = StdioClient()
    try:
        init = client.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        assert init["result"]["protocolVersion"] == "2024-11-05"
        # notification carries no id and must not consume the next response slot
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        ping = client.request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert ping["result"] == {}
        listing = client.request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = [t["name"] for t in listing["result"]["tools"]]
        assert names == [
            "scan_slop",
            "scan_diff",
            "plan_fixes",
            "apply_fixes",
            "list_checks",
            "filter_paths",
        ]
        assert all(t["inputSchema"]["type"] == "object" for t in listing["result"]["tools"])
        call = client.request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "apply_fixes",
                    "arguments": {"path": str(root), "tiers": ["safe"]},
                },
            }
        )
        payload = json.loads(call["result"]["content"][0]["text"])
        assert payload["changed_any"] is True
        assert (root / "dirty.txt").read_text() == "trailing\n"
        bad = client.request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "no_such_tool", "arguments": {}},
            }
        )
        assert bad["result"]["isError"] is True
    finally:
        client.close()


def test_stdio_parse_error_and_unknown_method(tmp_path: Path) -> None:
    client = StdioClient(env_root=str(tmp_path))
    try:
        client.send({"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {}})
        assert client.read()["result"]["serverInfo"]["name"] == "master-ci"
        client.proc.stdin.write("this is not json\n")
        client.proc.stdin.flush()
        parse_err = client.read()
        assert parse_err["error"]["code"] == -32700
        assert parse_err["id"] is None
        client.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 11, "method": "nope"}) + "\n")
        client.proc.stdin.flush()
        nf = client.read()
        assert nf["error"]["code"] == -32601
        # the path lock: tmp_path itself is allowed, outside is rejected
        outside_call = client.request(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "scan_slop", "arguments": {"path": str(Path(os.sep + "etc"))}},
            }
        )
        assert outside_call["result"]["isError"] is True
        assert "MASTER_CI_MCP_ROOT" in outside_call["result"]["content"][0]["text"]
    finally:
        client.close()
