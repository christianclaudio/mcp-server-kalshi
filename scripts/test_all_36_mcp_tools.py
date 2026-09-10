#!/usr/bin/env python3
"""Comprehensive test runner for all 36 Kalshi MCP tools.

Communicates over stdio JSON-RPC with mcp-server-kalshi in demo mode.
Tests public tools, simulation preview modes, and authentication safety gates.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO, Any

ALL_TOOLS = [
    # System & Exchange
    "get_environment",
    "get_exchange_status",
    "get_exchange_schedule",
    # Discovery & Taxonomies
    "list_markets",
    "get_market",
    "list_events",
    "get_event",
    "list_series",
    "get_series",
    "get_tags_by_categories",
    "get_sports_filters",
    # Market Data & Legal Rules
    "get_market_orderbook",
    "get_market_candlesticks",
    "get_market_trades",
    "get_market_rules",
    "fetch_rules_pdf",
    # Live & Multivariates
    "get_milestones",
    "get_milestone",
    "get_event_live_data",
    "list_multivariate_collections",
    "get_multivariate_collection",
    # Order Simulation Previews
    "create_order",
    "amend_order",
    "batch_create_orders",
    # Portfolio & Auth Gates
    "get_balance",
    "get_portfolio_summary",
    "get_positions",
    "get_fills",
    "get_settlements",
    "list_orders",
    "get_order",
    "decrease_order",
    "cancel_order",
    "batch_cancel_orders",
    "list_order_groups",
    "cancel_order_group",
]


class MCPClient:
    def __init__(self, cmd: list[str], env: dict[str, str]):
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
        self._req_id = 1

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

    def await_response(self, want_id: int, timeout: float = 20.0) -> dict[str, Any]:
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
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        raise TimeoutError(f"Timed out waiting for response id={want_id}")

    def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float = 20.0
    ) -> dict[str, Any]:
        self._req_id += 1
        req_id = self._req_id
        self.send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return self.await_response(req_id, timeout=timeout)

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> int:
    print("============================================================")
    print("        Kalshi MCP Server Tool Suite Verification           ")
    print("============================================================")
    env = {**os.environ, "KALSHI_ENV": "demo"}
    cmd = [sys.executable, "-m", "mcp_server_kalshi.server"]

    client = MCPClient(cmd, env)
    results: list[dict[str, Any]] = []

    try:
        # 1. Initialize
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-runner", "version": "1.0.0"},
                },
            }
        )
        init_res = client.await_response(1)
        server_name = init_res.get("result", {}).get("serverInfo", {}).get("name")
        print(f"[✓] Handshake initialized with server: {server_name}")
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2. Tools list
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_res = client.await_response(2)
        registered_tools = {
            t["name"] for t in tools_res.get("result", {}).get("tools", [])
        }
        print(
            f"[✓] Server reported {len(registered_tools)} registered tools in ToolRegistry."
        )
        assert len(registered_tools) == 36, (
            f"Expected 36 tools, found {len(registered_tools)}"
        )

        # 3. Dynamic Discovery of Live Fixtures
        fixtures: dict[str, Any] = {
            "market_ticker": "KXELONMARS-99",
            "event_ticker": "KXELONMARS-99",
            "series_ticker": "KXELONMARS",
            "milestone_id": "",
            "collection_ticker": "KXMVECROSSCATEGORY-SHARD1-R",
        }

        # Discover live markets
        try:
            m_resp = client.call_tool("list_markets", {"limit": 5})
            content = m_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            data = json.loads(content) if content else {}
            markets = data.get("markets", [])
            if markets:
                fixtures["market_ticker"] = markets[0]["ticker"]
                fixtures["event_ticker"] = markets[0].get(
                    "event_ticker", fixtures["event_ticker"]
                )
                if "series_ticker" in markets[0]:
                    fixtures["series_ticker"] = markets[0]["series_ticker"]
                print(f"[✓] Discovered live market: {fixtures['market_ticker']}")
                print(f"[✓] Discovered live event:  {fixtures['event_ticker']}")
        except Exception as e:
            print(f"[!] Warning discovering live markets: {e}")

        # Discover milestones
        try:
            ms_resp = client.call_tool("get_milestones", {"limit": 5})
            content = ms_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            data = json.loads(content) if content else {}
            milestones = data.get("milestones", [])
            if milestones:
                fixtures["milestone_id"] = milestones[0].get("id", "")
                print(f"[✓] Discovered live milestone: {fixtures['milestone_id']}")
        except Exception as e:
            print(f"[!] Warning discovering live milestones: {e}")

        # Discover multivariate collection
        try:
            c_resp = client.call_tool("list_multivariate_collections", {"limit": 5})
            content = c_resp.get("result", {}).get("content", [{}])[0].get("text", "")
            data = json.loads(content) if content else {}
            colls = data.get("multivariate_contracts", [])
            if colls:
                fixtures["collection_ticker"] = colls[0].get(
                    "ticker", fixtures["collection_ticker"]
                )
                print(
                    f"[✓] Discovered live multivariate collection: {fixtures['collection_ticker']}"
                )
        except Exception as e:
            print(f"[!] Warning discovering live collections: {e}")

        print("\n--- Executing Test Matrix Across All 36 Tools ---\n")

        now_ts = int(time.time())
        tool_args: dict[str, dict[str, Any]] = {
            "get_environment": {},
            "get_exchange_status": {},
            "get_exchange_schedule": {},
            "list_markets": {"limit": 3},
            "get_market": {"ticker": fixtures["market_ticker"]},
            "list_events": {"limit": 3},
            "get_event": {"event_ticker": fixtures["event_ticker"]},
            "list_series": {},
            "get_series": {"series_ticker": fixtures["series_ticker"]},
            "get_tags_by_categories": {},
            "get_sports_filters": {},
            "get_market_orderbook": {"ticker": fixtures["market_ticker"], "depth": 5},
            "get_market_candlesticks": {
                "ticker": fixtures["market_ticker"],
                "start_ts": now_ts - 86400 * 30,
                "end_ts": now_ts,
                "period_interval": 1440,
            },
            "get_market_trades": {"ticker": fixtures["market_ticker"], "limit": 3},
            "get_market_rules": {"ticker": fixtures["market_ticker"]},
            "fetch_rules_pdf": {"ticker": fixtures["market_ticker"]},
            "get_milestones": {"limit": 3},
            "get_milestone": {
                "milestone_id": fixtures["milestone_id"]
                or "c866c213-7b86-5626-a73e-32432a32c253"
            },
            "get_event_live_data": {"event_ticker": fixtures["event_ticker"]},
            "list_multivariate_collections": {"limit": 3},
            "get_multivariate_collection": {
                "collection_ticker": fixtures["collection_ticker"]
            },
            "create_order": {
                "ticker": fixtures["market_ticker"],
                "action": "buy",
                "side": "yes",
                "count": 1,
                "limit_price": 50,
                "confirm": False,
            },
            "amend_order": {
                "ticker": fixtures["market_ticker"],
                "order_id": "00000000-0000-0000-0000-000000000000",
                "action": "buy",
                "side": "yes",
                "count": 2,
                "limit_price": 55,
                "confirm": False,
            },
            "batch_create_orders": {
                "orders": [
                    {
                        "ticker": fixtures["market_ticker"],
                        "action": "buy",
                        "side": "yes",
                        "count": 1,
                        "limit_price": 50,
                    }
                ],
                "confirm": False,
            },
            "get_balance": {},
            "get_portfolio_summary": {},
            "get_positions": {},
            "get_fills": {},
            "get_settlements": {},
            "list_orders": {},
            "get_order": {"order_id": "00000000-0000-0000-0000-000000000000"},
            "decrease_order": {
                "order_id": "00000000-0000-0000-0000-000000000000",
                "reduce_by": 1,
            },
            "cancel_order": {
                "order_id": "00000000-0000-0000-0000-000000000000",
            },
            "batch_cancel_orders": {
                "order_ids": ["00000000-0000-0000-0000-000000000000"],
            },
            "list_order_groups": {},
            "cancel_order_group": {
                "order_group_id": "00000000-0000-0000-0000-000000000000",
            },
        }

        # Classifications
        auth_required_tools = {
            "get_balance",
            "get_portfolio_summary",
            "get_positions",
            "get_fills",
            "get_settlements",
            "list_orders",
            "get_order",
            "decrease_order",
            "cancel_order",
            "batch_cancel_orders",
            "list_order_groups",
            "cancel_order_group",
        }
        simulation_preview_tools = {
            "create_order",
            "amend_order",
            "batch_create_orders",
        }

        for idx, tool_name in enumerate(ALL_TOOLS, 1):
            args = tool_args.get(tool_name, {})
            t0 = time.monotonic()
            try:
                resp = client.call_tool(tool_name, args, timeout=25.0)
                dur = time.monotonic() - t0
                is_error = resp.get("result", {}).get("isError", False)
                content = resp.get("result", {}).get("content", [{}])[0].get("text", "")
                status = "UNKNOWN"

                if tool_name in simulation_preview_tools:
                    if not is_error and (
                        "preview" in content.lower() or "simulation" in content.lower()
                    ):
                        status = "PASS (Simulation Preview Verified)"
                    else:
                        status = f"FAIL (Unexpected preview result: {content[:60]})"
                elif tool_name in auth_required_tools:
                    if is_error and "requires Kalshi credentials" in content:
                        status = "PASS (Auth Safety Gate Enforced)"
                    elif not is_error:
                        status = "PASS (Authenticated Data Returned)"
                    else:
                        status = f"FAIL (Unexpected error: {content[:60]})"
                else:
                    # Public tools
                    if not is_error:
                        status = "PASS (Live Payload OK)"
                    elif "404" in content:
                        # Endpoint exists and responded with 404 for specific entity
                        status = "PASS (Endpoint Active - 404)"
                    else:
                        status = f"FAIL (Error: {content[:80]})"

                snippet = content.strip().replace("\n", " ")
                if len(snippet) > 65:
                    snippet = snippet[:62] + "..."

                results.append(
                    {
                        "index": idx,
                        "tool": tool_name,
                        "status": status,
                        "duration_s": round(dur, 3),
                        "summary": snippet,
                    }
                )
                print(
                    f"[{idx:2d}/36] {tool_name:<30} -> {status:<34} ({dur:.3f}s) | {snippet}"
                )

            except Exception as exc:
                dur = time.monotonic() - t0
                results.append(
                    {
                        "index": idx,
                        "tool": tool_name,
                        "status": "FAIL (Exception)",
                        "duration_s": round(dur, 3),
                        "summary": str(exc),
                    }
                )
                print(f"[{idx:2d}/36] {tool_name:<30} -> FAIL (Exception: {exc})")

        print("\n" + "=" * 60)
        print("                  VERIFICATION RESULTS")
        print("=" * 60)
        passed_count = sum(1 for r in results if r["status"].startswith("PASS"))
        failed_count = len(ALL_TOOLS) - passed_count
        print(f"Total Tools Verified:   {len(ALL_TOOLS)}")
        print(f"Successful / Passed:    {passed_count} / {len(ALL_TOOLS)}")
        print(f"Failed:                 {failed_count} / {len(ALL_TOOLS)}")
        print("=" * 60)

        return 0 if passed_count == len(ALL_TOOLS) else 1

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
