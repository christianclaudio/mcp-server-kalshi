"""OpenAPI v3.36.0 read tools: handlers and wire paths (offline mocks).

Spec: https://docs.kalshi.com/openapi.yaml (info.version 3.36.0).
Legacy GET /portfolio/orders (list_orders) is left unchanged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_server_kalshi import server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    mock.get_historical_cutoff.return_value = {"market_settled_ts": 1}
    mock.list_historical_markets.return_value = {"markets": []}
    mock.get_historical_market.return_value = {"market": {}}
    mock.get_historical_market_candlesticks.return_value = {"candlesticks": []}
    mock.get_historical_trades.return_value = {"trades": []}
    mock.get_historical_fills.return_value = {"fills": []}
    mock.list_historical_orders.return_value = {"orders": []}
    mock.get_historical_positions.return_value = {"market_positions": []}
    mock.batch_get_market_candlesticks.return_value = {"markets": []}
    mock.get_live_data_batch.return_value = {"live_data": []}
    mock.get_account_limits.return_value = {"tier": "basic"}
    mock.get_account_endpoint_costs.return_value = {"endpoints": []}
    mock.get_order_queue_positions.return_value = {"queue_positions": []}
    mock.get_order_queue_position.return_value = {"queue_position": 0}
    mock.list_deposits.return_value = {"deposits": []}
    mock.list_withdrawals.return_value = {"withdrawals": []}
    mock.get_subaccount_balances.return_value = {"balances": []}
    mock.list_subaccount_transfers.return_value = {"transfers": []}
    mock.get_subaccount_netting.return_value = {"netting": []}
    monkeypatch.setattr(server, "kalshi_client", mock)
    return mock


async def _call(name: str, args: dict[str, Any] | None = None) -> Any:
    handler = server.ToolRegistry.get_handler(name)
    return await handler(args or {})


@pytest.mark.asyncio
async def test_historical_and_batch_handlers_call_client(client: AsyncMock) -> None:
    await _call("get_historical_cutoff")
    client.get_historical_cutoff.assert_awaited_once()

    await _call("list_historical_markets", {"series_ticker": "KXTEST", "limit": 10})
    client.list_historical_markets.assert_awaited_once()
    assert (
        client.list_historical_markets.await_args.args[0]["series_ticker"] == "KXTEST"
    )

    await _call("get_historical_market", {"ticker": "MKT"})
    client.get_historical_market.assert_awaited_once_with("MKT")

    await _call(
        "get_historical_market_candlesticks",
        {"ticker": "MKT", "start_ts": 1, "end_ts": 2, "period_interval": 60},
    )
    client.get_historical_market_candlesticks.assert_awaited_once()

    await _call("get_historical_trades", {"ticker": "MKT"})
    client.get_historical_trades.assert_awaited_once()

    await _call("get_historical_fills", {"subaccount": 0})
    client.get_historical_fills.assert_awaited_once()

    await _call("list_historical_orders", {"ticker": "MKT"})
    client.list_historical_orders.assert_awaited_once()

    await _call("get_historical_positions", {"event_ticker": "EVT"})
    client.get_historical_positions.assert_awaited_once()

    await _call(
        "batch_get_market_candlesticks",
        {
            "market_tickers": "A,B",
            "start_ts": 1,
            "end_ts": 2,
            "period_interval": 60,
        },
    )
    client.batch_get_market_candlesticks.assert_awaited_once()

    await _call("get_live_data_batch", {"milestone_ids": ["m1", "m2"]})
    client.get_live_data_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_portfolio_read_handlers_call_client(client: AsyncMock) -> None:
    await _call("get_account_limits")
    client.get_account_limits.assert_awaited_once()
    await _call("get_account_endpoint_costs")
    client.get_account_endpoint_costs.assert_awaited_once()
    await _call("get_order_queue_positions", {"event_ticker": "EVT"})
    client.get_order_queue_positions.assert_awaited_once()
    await _call("get_order_queue_position", {"order_id": "oid-1"})
    client.get_order_queue_position.assert_awaited_once_with("oid-1")
    await _call("list_deposits", {"limit": 50})
    client.list_deposits.assert_awaited_once()
    await _call("list_withdrawals", {"cursor": "c1"})
    client.list_withdrawals.assert_awaited_once()
    await _call("get_subaccount_balances")
    client.get_subaccount_balances.assert_awaited_once()
    await _call("list_subaccount_transfers", {"limit": 20})
    client.list_subaccount_transfers.assert_awaited_once()
    await _call("get_subaccount_netting")
    client.get_subaccount_netting.assert_awaited_once()


@pytest.fixture
def wire(make_client, rsa_key_file, monkeypatch):
    client, requests = make_client(
        lambda req: httpx.Response(200, json={"ok": True}),
        api_key="key-id",
        private_key_path=rsa_key_file,
    )
    monkeypatch.setattr(server, "kalshi_client", client)
    return requests


@pytest.mark.asyncio
async def test_v336_read_paths_on_the_wire(wire) -> None:
    """Every new read hits the exact OpenAPI v3.36.0 path."""
    cases = [
        ("get_historical_cutoff", {}, "/trade-api/v2/historical/cutoff"),
        ("list_historical_markets", {}, "/trade-api/v2/historical/markets"),
        (
            "get_historical_market",
            {"ticker": "T"},
            "/trade-api/v2/historical/markets/T",
        ),
        (
            "get_historical_market_candlesticks",
            {"ticker": "T", "start_ts": 1, "end_ts": 2, "period_interval": 60},
            "/trade-api/v2/historical/markets/T/candlesticks",
        ),
        ("get_historical_trades", {}, "/trade-api/v2/historical/trades"),
        ("get_historical_fills", {}, "/trade-api/v2/historical/fills"),
        ("list_historical_orders", {}, "/trade-api/v2/historical/orders"),
        ("get_historical_positions", {}, "/trade-api/v2/historical/positions"),
        (
            "batch_get_market_candlesticks",
            {
                "market_tickers": "A,B",
                "start_ts": 1,
                "end_ts": 2,
                "period_interval": 60,
            },
            "/trade-api/v2/markets/candlesticks",
        ),
        (
            "get_live_data_batch",
            {"milestone_ids": ["m1"]},
            "/trade-api/v2/live_data/batch",
        ),
        ("get_account_limits", {}, "/trade-api/v2/account/limits"),
        ("get_account_endpoint_costs", {}, "/trade-api/v2/account/endpoint_costs"),
        (
            "get_order_queue_positions",
            {},
            "/trade-api/v2/portfolio/orders/queue_positions",
        ),
        (
            "get_order_queue_position",
            {"order_id": "oid"},
            "/trade-api/v2/portfolio/orders/oid/queue_position",
        ),
        ("list_deposits", {}, "/trade-api/v2/portfolio/deposits"),
        ("list_withdrawals", {}, "/trade-api/v2/portfolio/withdrawals"),
        (
            "get_subaccount_balances",
            {},
            "/trade-api/v2/portfolio/subaccounts/balances",
        ),
        (
            "list_subaccount_transfers",
            {},
            "/trade-api/v2/portfolio/subaccounts/transfers",
        ),
        (
            "get_subaccount_netting",
            {},
            "/trade-api/v2/portfolio/subaccounts/netting",
        ),
    ]
    for name, args, path in cases:
        before = len(wire)
        await _call(name, args)
        assert wire[-1].url.path == path, (name, wire[-1].url.path)
        assert wire[-1].method == "GET"
        assert len(wire) == before + 1


@pytest.mark.asyncio
async def test_legacy_portfolio_orders_read_untouched(wire) -> None:
    """Leave GET /portfolio/orders (list_orders) in place."""
    await _call("list_orders", {})
    assert wire[-1].url.path == "/trade-api/v2/portfolio/orders"
    assert wire[-1].method == "GET"


@pytest.mark.asyncio
async def test_endpoint_costs_needs_no_credentials(make_client, monkeypatch) -> None:
    """GET /account/endpoint_costs has no security block in the spec."""
    client, requests = make_client(
        lambda req: httpx.Response(200, json={"endpoints": []}),
    )
    monkeypatch.setattr(server, "kalshi_client", client)
    result = await _call("get_account_endpoint_costs")
    assert requests[-1].url.path == "/trade-api/v2/account/endpoint_costs"
    assert "KALSHI-ACCESS-KEY" not in requests[-1].headers
    assert result is not None
