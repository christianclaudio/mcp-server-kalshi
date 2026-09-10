"""End-to-end live testing across all dynamically discovered Kalshi MCP tools."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from mcp_server_kalshi.config import get_settings
from mcp_server_kalshi.server import ToolRegistry, handle_call_tool


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_kalshi_tools_live() -> None:
    """Dynamically discover and exercise 100% of registered Kalshi tools against sandbox or safety gates."""
    tools = ToolRegistry.get_tools()
    assert len(tools) > 0, "No tools registered in ToolRegistry"

    # Discover live sample entities for parameterized tools
    sample_ticker = "KXTEST"
    sample_event = "KXTEST"
    sample_series = "KXTEST"
    sample_milestone = "KXTEST"
    sample_collection = "KXTEST"

    try:
        m_res = await handle_call_tool("list_markets", {"limit": 1})
        m_data = json.loads(m_res[0].text)
        markets = m_data.get("markets", [])
        if markets:
            sample_ticker = markets[0].get("ticker", sample_ticker)
            sample_event = markets[0].get("event_ticker", sample_event)
    except Exception:
        pass

    try:
        s_res = await handle_call_tool("list_series", {})
        s_data = json.loads(s_res[0].text)
        series_list = s_data.get("series", [])
        for s in series_list:
            if s.get("contract_terms_url") and s.get("ticker"):
                sample_series = s["ticker"]
                break
    except Exception:
        pass

    try:
        ms_res = await handle_call_tool("get_milestones", {"limit": 1})
        ms_data = json.loads(ms_res[0].text)
        milestones = ms_data.get("milestones", [])
        if milestones and milestones[0].get("id"):
            sample_milestone = milestones[0]["id"]
    except Exception:
        pass

    try:
        c_res = await handle_call_tool("list_multivariate_collections", {"limit": 1})
        c_data = json.loads(c_res[0].text)
        contracts = c_data.get("multivariate_contracts", [])
        if contracts and contracts[0].get("collection_ticker"):
            sample_collection = contracts[0]["collection_ticker"]
    except Exception:
        pass

    simulation_preview_tools: dict[str, dict[str, Any]] = {
        "create_order": {
            "ticker": sample_ticker,
            "action": "buy",
            "side": "yes",
            "count": 1,
            "limit_price": 50,
            "confirm": False,
        },
        "amend_order": {
            "order_id": "00000000-0000-0000-0000-000000000000",
            "ticker": sample_ticker,
            "action": "buy",
            "side": "yes",
            "count": 1,
            "limit_price": 50,
            "confirm": False,
        },
        "batch_create_orders": {
            "orders": [
                {
                    "ticker": sample_ticker,
                    "action": "buy",
                    "side": "yes",
                    "count": 1,
                    "limit_price": 50,
                }
            ],
            "confirm": False,
        },
    }

    auth_required_tools: dict[str, dict[str, Any]] = {
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
        "cancel_order": {"order_id": "00000000-0000-0000-0000-000000000000"},
        "batch_cancel_orders": {"order_ids": ["00000000-0000-0000-0000-000000000000"]},
        "list_order_groups": {},
        "cancel_order_group": {
            "order_group_id": "00000000-0000-0000-0000-000000000000"
        },
    }

    public_param_tools: dict[str, dict[str, Any]] = {
        "get_market": {"ticker": sample_ticker},
        "get_event": {"event_ticker": sample_event},
        "get_series": {"series_ticker": sample_series},
        "get_market_orderbook": {"ticker": sample_ticker},
        "get_market_candlesticks": {"ticker": sample_ticker},
        "get_market_trades": {"ticker": sample_ticker},
        "get_market_rules": {"ticker": sample_ticker},
        "fetch_rules_pdf": {"series_ticker": sample_series},
        "get_milestone": {"milestone_id": sample_milestone},
        "get_event_live_data": {"event_ticker": sample_event},
        "get_multivariate_collection": {"collection_ticker": sample_collection},
    }

    results: list[dict[str, Any]] = []
    has_credentials = get_settings().has_credentials

    for tool in tools:
        t0 = time.perf_counter()
        name = tool.name

        if name in simulation_preview_tools:
            args = simulation_preview_tools[name]
        elif name in auth_required_tools:
            args = auth_required_tools[name]
        elif name in public_param_tools:
            args = public_param_tools[name]
        else:
            args = {}

        try:
            content = await handle_call_tool(name, args)
            dur = (time.perf_counter() - t0) * 1000
            text = content[0].text if content else ""

            if name in simulation_preview_tools:
                assert "preview" in text.lower() or "simulation" in text.lower()
                status = "PASS (Simulation Preview Verified)"
            elif name in auth_required_tools:
                if has_credentials:
                    assert (
                        not text.startswith(f"Error in {name}:")
                        or "400" in text
                        or "404" in text
                    )
                    status = "PASS (Authenticated Payload Verified)"
                else:
                    assert "requires Kalshi credentials" in text
                    status = "PASS (Auth Safety Gate Enforced)"
            elif name == "get_event_live_data":
                # Live scoreboard is ephemeral; sandbox returns 404 when no active game is in-progress
                assert not text.startswith(f"Error in {name}:") or "404" in text
                status = "PASS (Live Scoreboard Endpoint Checked)"
            else:
                assert not text.startswith(
                    f"Error in {name}:"
                ), f"Tool {name} returned error: {text}"
                status = "PASS (Live Payload OK)"

            results.append({"tool": name, "status": status, "latency_ms": dur})
        except Exception as exc:
            dur = (time.perf_counter() - t0) * 1000
            results.append(
                {"tool": name, "status": "FAIL", "latency_ms": dur, "error": str(exc)}
            )

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"Live tool verification failed for: {failed}"
    assert len(results) == len(tools)

    # Separate verification for expected not-found handling with placeholder identifier
    nf_content = await handle_call_tool(
        "get_market", {"ticker": "NON_EXISTENT_PLACEHOLDER_MARKET_99999"}
    )
    assert len(nf_content) > 0
    assert nf_content[0].text.startswith("Error in get_market:")
    assert "404" in nf_content[0].text or "not found" in nf_content[0].text.lower()
