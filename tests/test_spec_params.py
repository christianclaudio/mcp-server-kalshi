"""Optional query/body params added from the Kalshi OpenAPI spec (v3.34.0).

Source: https://docs.kalshi.com/openapi.yaml and https://docs.kalshi.com/changelog
(accessed 2026-10-21). Each test drives a tool handler through the real
``KalshiAPIClient`` with an ``httpx.MockTransport`` (no network) and checks the wire
request. Every param is optional; omitting it leaves the request unchanged.
"""

import json

import httpx
import pytest
from pydantic import ValidationError

from mcp_server_kalshi import server
from mcp_server_kalshi.kalshi_client.schemas import (
    BatchCancelOrdersRequest,
    CreateOrderRequest,
    GetEventRequest,
    GetPositionsRequest,
)


@pytest.fixture
def wire(make_client, rsa_key_file, monkeypatch):
    """Install an authenticated mock-transport client as ``server.kalshi_client``."""
    client, requests = make_client(
        lambda req: httpx.Response(200, json={"ok": True}),
        api_key="key-id",
        private_key_path=rsa_key_file,
    )
    monkeypatch.setattr(server, "kalshi_client", client)
    return requests


async def _call(name, args):
    return await server.ToolRegistry.get_handler(name)(args)


def _q(req) -> dict[str, str]:
    return dict(req.url.params)


def _body(req) -> dict:
    return json.loads(req.content)


# ---- Discovery -----------------------------------------------------------------
async def test_list_events_new_filters(wire):
    await _call(
        "list_events",
        {
            "tickers": "EV-1,EV-2",
            "min_close_ts": 100,
            "min_updated_ts": 200,
            "with_milestones": True,
        },
    )
    assert wire[-1].url.path == "/trade-api/v2/events"
    assert _q(wire[-1]) == {
        "tickers": "EV-1,EV-2",
        "min_close_ts": "100",
        "min_updated_ts": "200",
        "with_milestones": "true",
    }


async def test_get_event_exclude_markets(wire):
    await _call("get_event", {"event_ticker": "EV-1", "exclude_markets": True})
    assert wire[-1].url.path == "/trade-api/v2/events/EV-1"
    assert _q(wire[-1]) == {"exclude_markets": "true"}

    await _call("get_event", {"event_ticker": "EV-1"})
    assert _q(wire[-1]) == {}


def test_get_event_rejects_exclude_with_nested_markets():
    with pytest.raises(ValidationError, match="cannot be combined"):
        GetEventRequest(
            event_ticker="EV-1", exclude_markets=True, with_nested_markets=True
        )
    # Either flag alone, or exclude_markets=false with nested markets, is fine.
    GetEventRequest(
        event_ticker="EV-1", exclude_markets=False, with_nested_markets=True
    )


async def test_list_markets_ts_and_mve_filters(wire):
    args = {
        "min_created_ts": 1,
        "max_created_ts": 2,
        "min_updated_ts": 3,
        "max_updated_ts": 4,
        "min_settled_ts": 5,
        "max_settled_ts": 6,
        "mve_filter": "exclude",
    }
    await _call("list_markets", args)
    assert wire[-1].url.path == "/trade-api/v2/markets"
    assert _q(wire[-1]) == {k: str(v) for k, v in args.items()}


async def test_get_market_trades_is_block_trade(wire):
    await _call("get_market_trades", {"ticker": "X-1", "is_block_trade": False})
    assert _q(wire[-1]) == {"ticker": "X-1", "is_block_trade": "false"}


async def test_list_series_new_flags(wire):
    await _call(
        "list_series",
        {"include_volume": True, "include_product_metadata": True, "min_updated_ts": 9},
    )
    assert _q(wire[-1]) == {
        "include_volume": "true",
        "include_product_metadata": "true",
        "min_updated_ts": "9",
    }


async def test_get_series_include_volume(wire):
    await _call("get_series", {"series_ticker": "KXS", "include_volume": True})
    assert wire[-1].url.path == "/trade-api/v2/series/KXS"
    assert _q(wire[-1]) == {"include_volume": "true"}

    await _call("get_series", {"series_ticker": "KXS"})
    assert _q(wire[-1]) == {}


async def test_candlesticks_include_latest_before_start(wire):
    await _call(
        "get_market_candlesticks",
        {
            "ticker": "KXS-1",
            "start_ts": 10,
            "end_ts": 20,
            "include_latest_before_start": True,
        },
    )
    assert _q(wire[-1])["include_latest_before_start"] == "true"

    await _call(
        "get_market_candlesticks", {"ticker": "KXS-1", "start_ts": 10, "end_ts": 20}
    )
    assert "include_latest_before_start" not in _q(wire[-1])


async def test_get_milestones_new_filters(wire):
    await _call(
        "get_milestones",
        {
            "minimum_start_date": "2026-10-21T00:00:00Z",
            "source_id": "src-1",
            "min_updated_ts": 7,
        },
    )
    assert _q(wire[-1]) == {
        "limit": "20",
        "minimum_start_date": "2026-10-21T00:00:00Z",
        "source_id": "src-1",
        "min_updated_ts": "7",
    }


async def test_list_multivariate_collections_associated_event(wire):
    await _call("list_multivariate_collections", {"associated_event_ticker": "EV-1"})
    assert _q(wire[-1]) == {"limit": "20", "associated_event_ticker": "EV-1"}


async def test_get_event_live_data_range(wire):
    await _call("get_event_live_data", {"event_ticker": "EV-1", "range": "1h"})
    assert wire[-1].url.path == "/trade-api/v2/live_data/events/EV-1"
    assert _q(wire[-1]) == {"range": "1h"}

    await _call("get_event_live_data", {"event_ticker": "EV-1"})
    assert _q(wire[-1]) == {}


# ---- Portfolio -----------------------------------------------------------------
async def test_get_balance_subaccount_and_exchange_index(wire):
    await _call("get_balance", {"subaccount": 0, "exchange_index": 1})
    assert wire[-1].url.path == "/trade-api/v2/portfolio/balance"
    # subaccount=0 is a real value (primary), not "omitted".
    assert _q(wire[-1]) == {"subaccount": "0", "exchange_index": "1"}

    await _call("get_balance", {})
    assert _q(wire[-1]) == {}


async def test_get_positions_settlement_status_and_scope(wire):
    await _call(
        "get_positions",
        {"settlement_status": "settled", "subaccount": 2, "exchange_index": 0},
    )
    assert _q(wire[-1]) == {
        "settlement_status": "settled",
        "subaccount": "2",
        "exchange_index": "0",
    }


def test_get_positions_rejects_unknown_settlement_status():
    with pytest.raises(ValidationError):
        GetPositionsRequest(settlement_status="archived")
    with pytest.raises(ValidationError):
        GetPositionsRequest(subaccount=-1)


async def test_get_fills_subaccount_and_exchange_index(wire):
    await _call("get_fills", {"subaccount": 3, "exchange_index": 0})
    assert _q(wire[-1]) == {"subaccount": "3", "exchange_index": "0"}


async def test_list_orders_subaccount_and_exchange_index(wire):
    await _call("list_orders", {"subaccount": 1, "exchange_index": 2})
    assert wire[-1].url.path == "/trade-api/v2/portfolio/orders"
    assert _q(wire[-1]) == {"subaccount": "1", "exchange_index": "2"}


async def test_get_settlements_subaccount(wire):
    await _call("get_settlements", {"subaccount": 0})
    assert _q(wire[-1]) == {"subaccount": "0"}


async def test_list_order_groups_subaccount(wire):
    await _call("list_order_groups", {"subaccount": 4})
    assert _q(wire[-1]) == {"limit": "20", "subaccount": "4"}


# ---- Orders (writes) -----------------------------------------------------------
async def test_create_order_new_body_fields(wire):
    out = await _call(
        "create_order",
        {
            "ticker": "X-1",
            "action": "buy",
            "side": "yes",
            "count": 1,
            "limit_price": 40,
            "client_order_id": "cid",
            "order_group_id": "og-1",
            "cancel_order_on_pause": True,
            "subaccount": 0,
            "exchange_index": -1,
            "confirm": True,
        },
    )
    assert json.loads(out[0].text)["placed"] is True
    body = _body(wire[-1])
    assert body["order_group_id"] == "og-1"
    assert body["cancel_order_on_pause"] is True
    assert body["subaccount"] == 0
    assert body["exchange_index"] == -1


async def test_create_order_preview_shows_new_fields_and_sends_nothing(wire):
    out = await _call(
        "create_order",
        {
            "ticker": "X-1",
            "action": "buy",
            "side": "yes",
            "count": 1,
            "limit_price": 40,
            "cancel_order_on_pause": False,
        },
    )
    preview = json.loads(out[0].text)
    assert preview["preview"] is True
    assert preview["kalshi_v2_payload"]["cancel_order_on_pause"] is False
    assert "subaccount" not in preview["kalshi_v2_payload"]
    assert wire == []


def test_create_order_exchange_index_floor():
    base = {"ticker": "X", "action": "buy", "side": "yes", "count": 1, "limit_price": 5}
    with pytest.raises(ValidationError):
        CreateOrderRequest(**base, exchange_index=-2)


async def test_batch_create_orders_items_carry_new_fields(wire):
    await _call(
        "batch_create_orders",
        {
            "orders": [
                {
                    "ticker": "X-1",
                    "action": "buy",
                    "side": "no",
                    "count": 2,
                    "limit_price": 30,
                    "order_group_id": "og-1",
                    "subaccount": 5,
                    "exchange_index": 0,
                    "cancel_order_on_pause": True,
                },
                {
                    "ticker": "X-2",
                    "action": "sell",
                    "side": "yes",
                    "count": 1,
                    "limit_price": 60,
                },
            ],
            "confirm": True,
        },
    )
    first, second = _body(wire[-1])["orders"]
    assert first["order_group_id"] == "og-1"
    assert first["subaccount"] == 5
    assert first["exchange_index"] == 0
    assert first["cancel_order_on_pause"] is True
    for key in (
        "order_group_id",
        "subaccount",
        "exchange_index",
        "cancel_order_on_pause",
    ):
        assert key not in second


async def test_cancel_order_routing_query(wire):
    preview = json.loads(
        (
            await _call(
                "cancel_order",
                {"order_id": "o-1", "subaccount": 2, "market_ticker": "X-1"},
            )
        )[0].text
    )
    assert preview["query"] == {"subaccount": 2, "market_ticker": "X-1"}
    assert wire == []

    await _call(
        "cancel_order",
        {
            "order_id": "o-1",
            "subaccount": 2,
            "exchange_index": -1,
            "market_ticker": "X-1",
            "confirm": True,
        },
    )
    assert wire[-1].method == "DELETE"
    assert wire[-1].url.path == "/trade-api/v2/portfolio/events/orders/o-1"
    assert _q(wire[-1]) == {
        "subaccount": "2",
        "exchange_index": "-1",
        "market_ticker": "X-1",
    }

    await _call("cancel_order", {"order_id": "o-1", "confirm": True})
    assert _q(wire[-1]) == {}


async def test_amend_order_expiration_and_routing(wire):
    args = {
        "order_id": "o-1",
        "ticker": "X-1",
        "action": "buy",
        "side": "yes",
        "count": 3,
        "limit_price": 41,
        "client_order_id": "orig",
        "expiration_time": 0,
        "exchange_index": 0,
        "subaccount": 1,
    }
    preview = json.loads((await _call("amend_order", args))[0].text)
    assert preview["kalshi_v2_payload"]["expiration_time"] == 0
    assert preview["query"] == {"subaccount": 1}
    assert wire == []

    await _call("amend_order", {**args, "confirm": True})
    assert wire[-1].url.path == "/trade-api/v2/portfolio/events/orders/o-1/amend"
    assert _q(wire[-1]) == {"subaccount": "1"}
    body = _body(wire[-1])
    # expiration_time=0 clears the expiry, so 0 must be sent, not dropped.
    assert body["expiration_time"] == 0
    assert body["client_order_id"] == "orig"
    assert body["exchange_index"] == 0

    plain = {
        k: v
        for k, v in args.items()
        if k
        not in {"client_order_id", "expiration_time", "exchange_index", "subaccount"}
    }
    await _call("amend_order", {**plain, "confirm": True})
    assert _q(wire[-1]) == {}
    assert set(_body(wire[-1])) == {"ticker", "side", "price", "count"}


async def test_decrease_order_routing(wire):
    args = {
        "order_id": "o-1",
        "reduce_by": 1,
        "exchange_index": -1,
        "market_ticker": "X-1",
        "subaccount": 0,
    }
    preview = json.loads((await _call("decrease_order", args))[0].text)
    assert preview["query"] == {"subaccount": 0}
    assert wire == []

    await _call("decrease_order", {**args, "confirm": True})
    assert wire[-1].url.path == "/trade-api/v2/portfolio/events/orders/o-1/decrease"
    assert _q(wire[-1]) == {"subaccount": "0"}
    assert _body(wire[-1]) == {
        "reduce_by": "1",
        "exchange_index": -1,
        "market_ticker": "X-1",
    }

    await _call("decrease_order", {"order_id": "o-1", "reduce_to": 2, "confirm": True})
    assert _q(wire[-1]) == {}
    assert _body(wire[-1]) == {"reduce_to": "2"}


async def test_batch_cancel_orders_items(wire):
    orders = [
        {
            "order_id": "o-1",
            "subaccount": 1,
            "exchange_index": -1,
            "market_ticker": "X-1",
        },
        {"order_id": "o-2"},
    ]
    preview = json.loads(
        (await _call("batch_cancel_orders", {"orders": orders}))[0].text
    )
    assert preview["order_ids"] == ["o-1", "o-2"]
    assert preview["count"] == 2
    assert preview["orders"] == orders
    assert wire == []

    out = json.loads(
        (await _call("batch_cancel_orders", {"orders": orders, "confirm": True}))[
            0
        ].text
    )
    assert out["count"] == 2
    assert wire[-1].method == "DELETE"
    assert _body(wire[-1]) == {"orders": orders}

    # The original order_ids form is unchanged.
    await _call("batch_cancel_orders", {"order_ids": ["o-3"], "confirm": True})
    assert _body(wire[-1]) == {"orders": [{"order_id": "o-3"}]}


def test_batch_cancel_requires_exactly_one_form():
    with pytest.raises(ValidationError, match="exactly one"):
        BatchCancelOrdersRequest()
    with pytest.raises(ValidationError, match="exactly one"):
        BatchCancelOrdersRequest(order_ids=["o-1"], orders=[{"order_id": "o-2"}])


async def test_cancel_order_group_routing(wire):
    preview = json.loads(
        (
            await _call(
                "cancel_order_group", {"order_group_id": "og-1", "subaccount": 1}
            )
        )[0].text
    )
    assert preview["query"] == {"subaccount": 1}
    assert wire == []

    await _call(
        "cancel_order_group",
        {
            "order_group_id": "og-1",
            "subaccount": 1,
            "exchange_index": 0,
            "confirm": True,
        },
    )
    assert wire[-1].method == "DELETE"
    assert wire[-1].url.path == "/trade-api/v2/portfolio/order_groups/og-1"
    assert _q(wire[-1]) == {"subaccount": "1", "exchange_index": "0"}

    await _call("cancel_order_group", {"order_group_id": "og-1", "confirm": True})
    assert _q(wire[-1]) == {}


# ---- Schema surface ------------------------------------------------------------
NEW_PARAMS = {
    "list_events": {"tickers", "min_close_ts", "min_updated_ts", "with_milestones"},
    "get_event": {"exclude_markets"},
    "list_markets": {
        "min_created_ts",
        "max_created_ts",
        "min_updated_ts",
        "max_updated_ts",
        "min_settled_ts",
        "max_settled_ts",
        "mve_filter",
    },
    "get_market_trades": {"is_block_trade"},
    "list_series": {"include_volume", "include_product_metadata", "min_updated_ts"},
    "get_series": {"include_volume"},
    "get_market_candlesticks": {"include_latest_before_start"},
    "get_balance": {"subaccount", "exchange_index"},
    "get_positions": {"settlement_status", "subaccount", "exchange_index"},
    "get_fills": {"subaccount", "exchange_index"},
    "list_orders": {"subaccount", "exchange_index"},
    "get_settlements": {"subaccount"},
    "get_milestones": {"minimum_start_date", "source_id", "min_updated_ts"},
    "list_multivariate_collections": {"associated_event_ticker"},
    "get_event_live_data": {"range"},
    "create_order": {
        "order_group_id",
        "cancel_order_on_pause",
        "subaccount",
        "exchange_index",
    },
    "cancel_order": {"subaccount", "exchange_index", "market_ticker"},
    "amend_order": {
        "expiration_time",
        "client_order_id",
        "exchange_index",
        "subaccount",
    },
    "decrease_order": {"exchange_index", "market_ticker", "subaccount"},
    "batch_cancel_orders": {"orders"},
    "list_order_groups": {"subaccount"},
    "cancel_order_group": {"subaccount", "exchange_index"},
}


def test_new_params_are_optional_in_tool_schemas():
    tools = {t.name: t for t in server.ToolRegistry.get_tools()}
    for name, params in NEW_PARAMS.items():
        schema = tools[name].input_schema
        assert params <= set(schema["properties"]), name
        assert not params & set(schema.get("required", [])), name
