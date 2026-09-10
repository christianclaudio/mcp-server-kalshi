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


async def test_stateless_streamable_http_standalone_post() -> None:
    """Verify Streamable HTTP in stateless mode accepts standalone requests without session ID."""
    import httpx

    from mcp_server_kalshi.server import server

    app = server.streamable_http_app(stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http_client:
            # Standalone initialize without session ID
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "test-stateless", "version": "1.0"},
                },
            }
            res = await http_client.post(
                "/mcp",
                json=init_payload,
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 200
            assert "Mcp-Session-Id" not in res.headers
            data = res.json()
            assert data["result"]["serverInfo"]["name"] == "kalshi-server"

            # Standalone tools/list without prior session handshake
            list_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
            res_list = await http_client.post(
                "/mcp",
                json=list_payload,
                headers={"Content-Type": "application/json"},
            )
            assert res_list.status_code == 200
            assert "Mcp-Session-Id" not in res_list.headers
            list_data = res_list.json()
            assert "result" in list_data
            assert "tools" in list_data["result"]
            assert len(list_data["result"]["tools"]) == 36

            # Standalone tool call without session affinity
            tool_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_environment", "arguments": {}},
            }
            res_tool = await http_client.post(
                "/mcp",
                json=tool_payload,
                headers={"Content-Type": "application/json"},
            )
            assert res_tool.status_code == 200
            assert "Mcp-Session-Id" not in res_tool.headers
            tool_data = res_tool.json()
            assert "result" in tool_data
            assert "content" in tool_data["result"]
            assert "DEMO" in tool_data["result"]["content"][0]["text"]
