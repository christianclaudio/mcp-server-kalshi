#!/usr/bin/env python3
"""Stdio Handshake Smoke Test for mcp-server-kalshi.

Spawns the local server over stdio and executes JSON-RPC initialize, tools/list, and tools/call.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO

EXPECTED_SERVER_NAME = "kalshi-server"
TIMEOUT_SECONDS = 15


def _reader(stream: IO[str], q: queue.Queue[str | None]) -> None:
    for line in stream:
        q.put(line)
    q.put(None)


def _send(proc: subprocess.Popen[str], message: dict[str, object]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _await_response(
    q: queue.Queue[str | None], want_id: int, deadline: float
) -> dict[str, object]:
    while time.monotonic() < deadline:
        try:
            line = q.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        if line is None:
            raise SystemExit("server closed stdout before responding")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg
    raise SystemExit(f"timed out waiting for response id={want_id}")


def main() -> int:
    print("[*] Starting stdio smoke test for mcp-server-kalshi...")
    env = {**os.environ, "KALSHI_ENV": "demo"}

    cmd = [sys.executable, "-m", "mcp_server_kalshi.server"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )

    out_q: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, out_q), daemon=True).start()

    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        # 1. Initialize
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "1.0.0"},
                },
            },
        )
        init = _await_response(out_q, 1, deadline)
        result = init.get("result", {})
        if not isinstance(result, dict):
            raise SystemExit(f"Invalid initialize result: {init}")

        name = result.get("serverInfo", {}).get("name")
        if name != EXPECTED_SERVER_NAME:
            raise SystemExit(f"Unexpected serverInfo.name: {name!r}")
        print("[✓] JSON-RPC initialize handshake successful.")

        # 2. Initialized notification
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. List tools
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_resp = _await_response(out_q, 2, deadline)
        tools_result = tools_resp.get("result", {})
        tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
        print(f"[✓] tools/list returned {len(tools)} tools.")
        if len(tools) != 24:
            raise SystemExit(f"Expected 24 tools, got {len(tools)}")

        # 4. Call get_environment
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_environment", "arguments": {}},
            },
        )
        call_resp = _await_response(out_q, 3, deadline)
        call_result = call_resp.get("result", {})
        content = (
            call_result.get("content", []) if isinstance(call_result, dict) else []
        )
        if not content or "DEMO" not in content[0].get("text", ""):
            raise SystemExit(f"Unexpected tools/call result: {call_resp}")
        print("[✓] tools/call get_environment verified successfully.")

        print("[✓] Stdio smoke test completed with 100% success.")
        return 0
    except SystemExit as exc:
        stderr = proc.stderr.read() if proc.stderr else ""
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        if stderr.strip():
            print("---- server stderr ----", file=sys.stderr)
            print(stderr, file=sys.stderr)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
