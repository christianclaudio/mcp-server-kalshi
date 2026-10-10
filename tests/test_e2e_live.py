"""End-to-end live testing across all dynamically discovered Kalshi MCP tools."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import pytest

from mcp_server_kalshi.config import get_settings
from mcp_server_kalshi.server import ToolRegistry, handle_call_tool

ACTIVE_MARKET_STATUSES = frozenset({"open", "active"})
MULTIVARIATE_TICKER_PREFIX = "KXMVE"


def is_usable_sample_market(market: dict[str, Any]) -> bool:
    """Return True for an open, single (non-multivariate) market with a ticker."""
    ticker = market.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        return False
    if market.get("status") not in ACTIVE_MARKET_STATUSES:
        return False
    if ticker.startswith(MULTIVARIATE_TICKER_PREFIX):
        return False
    if market.get("mve_collection_ticker") or market.get("mve_selected_legs"):
        return False
    return True


def pick_sample_market(markets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first usable sample market, or None when there is none."""
    for market in markets:
        if is_usable_sample_market(market):
            return market
    return None


def require_sample_market(markets: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first usable sample market, or skip the test with a reason."""
    market = pick_sample_market(markets)
    if market is None:
        pytest.skip(
            "No open, non-multivariate Kalshi market found to use as the e2e sample"
        )
    return market


LIST_EVENTS_ARGS: dict[str, Any] = {
    "status": "open",
    "with_nested_markets": True,
    "limit": 100,
}


def extract_event_markets(text: str) -> list[dict[str, Any]]:
    """Parse a list_events result into its nested markets.

    An error result or an unexpected shape fails the test; only a
    well-formed result can lead to a skip.
    """
    assert not text.startswith("Error in list_events:"), f"list_events failed: {text}"
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AssertionError(f"list_events returned non-JSON: {text!r}") from exc
    assert isinstance(data, dict), f"list_events returned a non-object: {data!r}"
    events = data.get("events")
    assert isinstance(events, list), f"list_events has no events list: {data!r}"
    markets: list[dict[str, Any]] = []
    for event in events:
        assert isinstance(event, dict), f"list_events event is not an object: {event!r}"
        nested = event.get("markets")
        if nested is None:
            nested = []
        assert isinstance(nested, list), f"event markets is not a list: {event!r}"
        markets.extend(m for m in nested if isinstance(m, dict))
    return markets


async def discover_sample_market() -> dict[str, Any]:
    """Find a live sample market. Lookup failures fail; an empty result skips."""
    content = await handle_call_tool("list_events", LIST_EVENTS_ARGS)
    assert content, "list_events returned no content"
    return require_sample_market(extract_event_markets(content[0].text))


def test_pick_sample_market_skips_multivariate_and_closed() -> None:
    markets: list[dict[str, Any]] = [
        {"ticker": "KXMVECROSSCATEGORY-S1-A", "status": "active"},
        {"ticker": "KXFOO-1", "status": "active", "mve_collection_ticker": "KXC"},
        {"ticker": "KXBAR-1", "status": "active", "mve_selected_legs": [{"x": 1}]},
        {"ticker": "KXCLOSED-1", "status": "closed"},
        {"ticker": "KXSETTLED-1", "status": "settled"},
        {"status": "active"},
        {"ticker": "KXGOOD-1", "status": "active", "event_ticker": "KXGOOD"},
        {"ticker": "KXGOOD-2", "status": "open"},
    ]
    assert pick_sample_market(markets) == markets[6]
    assert pick_sample_market([{"ticker": "KXOPEN-1", "status": "open"}]) == {
        "ticker": "KXOPEN-1",
        "status": "open",
    }


def test_require_sample_market_skips_when_none_usable() -> None:
    assert pick_sample_market([]) is None
    with pytest.raises(pytest.skip.Exception, match="non-multivariate"):
        require_sample_market(
            [
                {"ticker": "KXMVEX-1", "status": "active"},
                {"ticker": "KXDONE-1", "status": "finalized"},
            ]
        )
    good = {"ticker": "KXGOOD-1", "status": "active"}
    assert require_sample_market([good]) is good


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

    # The first page of list_markets is mostly multivariate (KXMVE...) markets,
    # which can 404 on per-market endpoints, so walk open events instead.
    market = await discover_sample_market()
    sample_ticker = market["ticker"]
    sample_event = market.get("event_ticker") or sample_event

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
        "decrease_order": {
            "order_id": "00000000-0000-0000-0000-000000000000",
            "reduce_by": 1,
            "confirm": False,
        },
        "cancel_order": {
            "order_id": "00000000-0000-0000-0000-000000000000",
            "confirm": False,
        },
        "batch_cancel_orders": {
            "order_ids": ["00000000-0000-0000-0000-000000000000"],
            "confirm": False,
        },
        "cancel_order_group": {
            "order_group_id": "00000000-0000-0000-0000-000000000000",
            "confirm": False,
        },
    }

    auth_required_tools: dict[str, dict[str, Any]] = {
        "get_balance": {},
        "get_positions": {},
        "get_fills": {},
        "get_settlements": {},
        "list_orders": {},
        "get_order": {"order_id": "00000000-0000-0000-0000-000000000000"},
        "list_order_groups": {},
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
                err_msg = f"Tool {name} returned error: {text}"
                assert not text.startswith(f"Error in {name}:"), err_msg
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


@pytest.mark.parametrize(
    "text",
    [
        "Error in list_events: 401 Unauthorized",
        "not json",
        "[]",
        '{"cursor": ""}',
        '{"events": {}}',
        '{"events": ["KXFOO"]}',
        '{"events": [{"markets": {}}]}',
    ],
)
def test_extract_event_markets_fails_on_bad_result(text: str) -> None:
    with pytest.raises(AssertionError):
        extract_event_markets(text)


def test_extract_event_markets_flattens_nested_markets() -> None:
    text = json.dumps(
        {
            "events": [
                {"markets": [{"ticker": "KXA-1"}, "junk"]},
                {"markets": None},
                {},
                {"markets": [{"ticker": "KXB-1"}]},
            ]
        }
    )
    assert extract_event_markets(text) == [{"ticker": "KXA-1"}, {"ticker": "KXB-1"}]


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.mark.asyncio
async def test_discover_sample_market_fails_on_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(name: str, args: dict[str, Any]) -> list[_FakeContent]:
        raise RuntimeError("network down")

    async def error_result(name: str, args: dict[str, Any]) -> list[_FakeContent]:
        return [_FakeContent("Error in list_events: 401 Unauthorized")]

    async def empty_content(name: str, args: dict[str, Any]) -> list[_FakeContent]:
        return []

    async def bad_shape(name: str, args: dict[str, Any]) -> list[_FakeContent]:
        return [_FakeContent('{"unexpected": true}')]

    cases = (
        (boom, RuntimeError),
        (error_result, AssertionError),
        (empty_content, AssertionError),
        (bad_shape, AssertionError),
    )
    for fake, expected in cases:
        monkeypatch.setattr(sys.modules[__name__], "handle_call_tool", fake)
        try:
            with pytest.raises(expected):
                await discover_sample_market()
        except pytest.skip.Exception:
            pytest.fail(f"{fake.__name__}: lookup failure skipped instead of failing")


@pytest.mark.asyncio
async def test_discover_sample_market_skips_or_picks_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    payload: dict[str, Any] = {
        "events": [{"markets": [{"ticker": "KXMVEX-1", "status": "active"}]}]
    }

    async def fake(name: str, args: dict[str, Any]) -> list[_FakeContent]:
        calls.append((name, args))
        return [_FakeContent(json.dumps(payload))]

    monkeypatch.setattr(sys.modules[__name__], "handle_call_tool", fake)
    with pytest.raises(pytest.skip.Exception, match="non-multivariate"):
        await discover_sample_market()
    assert calls == [("list_events", LIST_EVENTS_ARGS)]

    good = {"ticker": "KXGOOD-1", "status": "active", "event_ticker": "KXGOOD"}
    payload["events"].append({"markets": [good]})
    assert await discover_sample_market() == good
