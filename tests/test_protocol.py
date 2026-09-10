"""Protocol integration tests for Kalshi MCP server stdio JSON-RPC handshake."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO, Any


class StdioMCPClient:
    """Minimal stdio JSON-RPC test client for protocol integration tests."""

    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
        self.out_q: queue.Queue[str | None] = queue.Queue()
        self.err_lines: list[str] = []

        threading.Thread(
            target=self._reader, args=(self.proc.stdout, self.out_q), daemon=True
        ).start()
        threading.Thread(
            target=self._err_reader, args=(self.proc.stderr,), daemon=True
        ).start()

    def _reader(self, stream: IO[str], q: queue.Queue[str | None]) -> None:
        for line in stream:
            q.put(line)
        q.put(None)

    def _err_reader(self, stream: IO[str]) -> None:
        for line in stream:
            self.err_lines.append(line)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def await_response(self, want_id: int, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.out_q.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                raise RuntimeError("Server closed stdout before responding")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as err:
                raise AssertionError(
                    f"Protocol violation: non-JSON line received on stdout: {line!r}"
                ) from err
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        stderr = "".join(self.err_lines)
        raise TimeoutError(
            f"Timed out waiting for response id={want_id}. Stderr: {stderr}"
        )

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def test_stdio_jsonrpc_protocol_flow() -> None:
    """Verify full stdio protocol flow: initialize, initialized, tools/list, and tools/call."""
    env = {**os.environ, "KALSHI_ENV": "demo"}
    cmd = [sys.executable, "-m", "mcp_server_kalshi.server"]
    client = StdioMCPClient(cmd, env)

    try:
        # 1. Initialize Handshake
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "pytest-protocol-client",
                        "version": "1.0.0",
                    },
                },
            }
        )
        init_resp = client.await_response(1)
        assert "result" in init_resp, f"Handshake failed: {init_resp}"
        result = init_resp["result"]
        assert result["serverInfo"]["name"] == "kalshi-server"
        assert "capabilities" in result

        # 2. Initialized Notification
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. Dynamic Tools List
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_resp = client.await_response(2)
        assert "result" in list_resp
        tools = list_resp["result"].get("tools", [])
        assert len(tools) == 36, f"Expected 36 tools, got {len(tools)}"

        # 4. Wire Tool Call
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_environment", "arguments": {}},
            }
        )
        call_resp = client.await_response(3)
        assert "result" in call_resp
        content = call_resp["result"].get("content", [])
        assert len(content) > 0
        text = content[0].get("text", "")
        assert "DEMO" in text
        assert call_resp["result"].get("isError") is False
    finally:
        client.close()
