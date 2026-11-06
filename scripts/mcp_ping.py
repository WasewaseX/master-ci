#!/usr/bin/env python3
"""mcp_ping.py - prove a Master CI MCP install actually works.

Spawns scripts/mcp_server.py as a real subprocess and drives it over real
stdio with newline-delimited JSON-RPC 2.0: the initialize handshake, the
initialized notification, ping, tools/list, a tools/call of diagnose, and
the fix-my-repo prompt. Every MCP client (Claude Desktop, Cursor, Cline,
z.ai) produces the same wire traffic, so a PASS here means the client
wiring will work.

Each step prints a PASS or FAIL line with one piece of evidence; the exit
code is 0 only when every step passed. With --online the check_updates
tool is also called, which needs network access to GitHub.

Usage:
    python3 scripts/mcp_ping.py
    python3 scripts/mcp_ping.py --server /path/to/mcp_server.py --timeout 20
    python3 scripts/mcp_ping.py --online
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parent / "mcp_server.py"


class Client:
    """Minimal newline-delimited JSON-RPC client with timeout reads."""

    def __init__(self, server: Path, timeout: float) -> None:
        self.timeout = timeout
        self.proc = subprocess.Popen(
            [sys.executable, str(server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        assert self.proc.stdout is not None
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()

    def _pump(self, stream) -> None:
        for line in stream:
            self.lines.put(line)

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self.lines.get(timeout=self.timeout)
        return json.loads(line)

    def request(self, msg: dict) -> dict:
        self.send(msg)
        response = self.read()
        assert response.get("id") == msg["id"], f"id mismatch: {response}"
        return response

    def close(self) -> str:
        """Shut the server down and return a short stderr tail."""
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        tail = ""
        if self.proc.stderr:
            try:
                tail = self.proc.stderr.read()[-400:]
            except Exception:
                tail = ""
        return tail


def ok(step: str, evidence: str) -> None:
    print(f"[PASS] {step}: {evidence}")


def fail(step: str, evidence: str) -> None:
    print(f"[FAIL] {step}: {evidence}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a master-ci MCP install end to end.")
    parser.add_argument("--server", default=str(SERVER_PATH), help="path to mcp_server.py")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-read timeout seconds")
    parser.add_argument("--online", action="store_true", help="also call check_updates (network)")
    args = parser.parse_args()

    failures = 0
    client: Client | None = None
    try:
        client = Client(Path(args.server), args.timeout)
        init = client.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp_ping", "version": "1.0.0"},
                },
            }
        )
        result = init["result"]
        ok(
            "initialize",
            f"protocol {result['protocolVersion']}, server v{result['serverInfo']['version']}",
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        client.request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        ok("ping", "server answered")
        listing = client.request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = [tool["name"] for tool in listing["result"]["tools"]]
        if "diagnose" not in names or "apply_fixes" not in names:
            raise AssertionError(f"expected tools missing from tools/list: {names}")
        ok("tools/list", f"{len(names)} tools: {', '.join(names)}")
        call = client.request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "diagnose", "arguments": {}},
            }
        )
        if call["result"].get("isError"):
            raise AssertionError(f"diagnose returned an error: {call['result']['content']}")
        payload = json.loads(call["result"]["content"][0]["text"])
        ok("tools/call diagnose", f"engines loaded, python {payload['python']}")
        prompts = client.request({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
        prompt_names = [p["name"] for p in prompts["result"]["prompts"]]
        if "fix-my-repo" not in prompt_names:
            raise AssertionError(f"fix-my-repo prompt missing from prompts/list: {prompt_names}")
        ok("prompts/list", f"{len(prompt_names)} prompts: {', '.join(prompt_names)}")
        if args.online:
            update = client.request(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "check_updates", "arguments": {}},
                }
            )
            state = json.loads(update["result"]["content"][0]["text"])
            if state.get("error"):
                ok("check_updates", f"graceful offline report: {state['error'][:60]}")
            elif state.get("up_to_date"):
                ok("check_updates", f"install current with {state.get('latest_tag')}")
            else:
                ok("check_updates", f"update available: {state.get('latest_tag')}")
    except Exception as exc:  # noqa: BLE001 - any failure is a FAIL line, not a traceback
        failures += 1
        fail("session", f"{type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            stderr_tail = client.close()
            if failures:
                if stderr_tail.strip():
                    print(f"       server stderr tail: {stderr_tail.strip()}")
                print("       If your client config uses this server, check the command path:")
                print('       {"mcpServers": {"master-ci": {"command": "python3",')
                print(f'         "args": ["{SERVER_PATH}"]}}}}')

    print(f"result: {'FAIL' if failures else 'PASS'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
