"""Every Kalshi OpenAPI v3.36.0 GET has a read-only tool.

Spec fixture: tests/fixtures/kalshi-openapi-3.36.0.yaml, a copy of
https://docs.kalshi.com/openapi.yaml (info.version 3.36.0). Loaded from disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from conftest import handler_result
from pydantic import ValidationError

from mcp_server_kalshi import server
from mcp_server_kalshi.kalshi_client.schemas import (
    GetEventCandlesticksRequest,
    GetMarketOrderbooksRequest,
    GetWeatherIndexRequest,
    ListMultivariateEventsRequest,
    SearchConcertsRequest,
)
from scripts.check_openapi_drift import extract_client_calls, normalize_path

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi-openapi-3.36.0.yaml"
CLIENT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "mcp_server_kalshi"
    / "kalshi_client"
    / "client.py"
)

# Reads that are not themselves a Trade API GET.
NON_GET_READS = frozenset({"get_environment", "get_market_rules", "fetch_rules_pdf"})

# Spec path -> tool. One tool per GET.
GET_TOOLS: dict[str, str] = {
    "/exchange/status": "get_exchange_status",
    "/exchange/schedule": "get_exchange_schedule",
    "/exchange/user_data_timestamp": "get_exchange_user_data_timestamp",
    "/series/fee_changes": "get_series_fee_changes",
    "/series/{series_ticker}/markets/{ticker}/candlesticks": "get_market_candlesticks",
    "/series/{series_ticker}/events/{ticker}/candlesticks": "get_event_candlesticks",
    "/series/{series_ticker}/events/{ticker}/forecast_percentile_history": (
        "get_event_forecast_percentile_history"
    ),
    "/series/{series_ticker}": "get_series",
    "/series": "list_series",
    "/markets/trades": "get_market_trades",
    "/markets/{ticker}/orderbook": "get_market_orderbook",
    "/markets/orderbooks": "get_market_orderbooks",
    "/markets": "list_markets",
    "/markets/{ticker}": "get_market",
    "/markets/candlesticks": "batch_get_market_candlesticks",
    "/events": "list_events",
    "/events/multivariate": "list_multivariate_events",
    "/events/fee_changes": "get_event_fee_changes",
    "/events/{event_ticker}": "get_event",
    "/events/{event_ticker}/metadata": "get_event_metadata",
    "/portfolio/orders": "list_orders",
    "/portfolio/orders/{order_id}": "get_order",
    "/portfolio/orders/queue_positions": "get_order_queue_positions",
    "/portfolio/orders/{order_id}/queue_position": "get_order_queue_position",
    "/portfolio/order_groups": "list_order_groups",
    "/portfolio/order_groups/{order_group_id}": "get_order_group",
    "/portfolio/balance": "get_balance",
    "/portfolio/intra_exchange_instance_transfers": (
        "list_intra_exchange_instance_transfers"
    ),
    "/portfolio/intra_exchange_instance_transfers/{transfer_id}": (
        "get_intra_exchange_instance_transfer"
    ),
    "/portfolio/subaccounts/balances": "get_subaccount_balances",
    "/portfolio/subaccounts/transfers": "list_subaccount_transfers",
    "/portfolio/subaccounts/netting": "get_subaccount_netting",
    "/portfolio/positions": "get_positions",
    "/portfolio/positions/{ticker}/exit_trigger": "get_event_contract_exit_triggers",
    "/portfolio/settlements": "get_settlements",
    "/portfolio/credit_history": "get_credit_history",
    "/portfolio/deposits": "list_deposits",
    "/portfolio/withdrawals": "list_withdrawals",
    "/portfolio/summary/total_resting_order_value": "get_total_resting_order_value",
    "/portfolio/fills": "get_fills",
    "/portfolio/target_balance_allocation": "get_target_balance_allocation",
    "/communications/id": "get_communications_id",
    "/communications/block-trade-proposals": "list_block_trade_proposals",
    "/communications/rfqs": "list_rfqs",
    "/communications/rfqs/{rfq_id}": "get_rfq",
    "/communications/rfqs/{rfq_id}/quotes/{quote_id}": "get_rfq_quote",
    "/communications/rfq_batches": "list_rfq_batches",
    "/communications/rfq_batches/{rfq_batch_id}": "get_rfq_batch",
    "/communications/rfq_batches/{rfq_batch_id}/best_quotes": "get_rfq_batch_best_quotes",
    "/communications/rfq_batches/{rfq_batch_id}/quotes": "get_rfq_batch_quotes",
    "/communications/quotes": "list_quotes",
    "/communications/quotes/{quote_id}": "get_quote",
    "/api_keys": "list_api_keys",
    "/account/limits": "get_account_limits",
    "/account/api_usage_level/volume_progress": (
        "get_account_api_usage_level_volume_progress"
    ),
    "/account/endpoint_costs": "get_account_endpoint_costs",
    "/search/tags_by_categories": "get_tags_by_categories",
    "/search/filters_by_sport": "get_sports_filters",
    "/live_data/milestone/{milestone_id}": "get_live_data_by_milestone",
    "/live_data/{type}/milestone/{milestone_id}": "get_live_data",
    "/live_data/batch": "get_live_data_batch",
    "/live_data/milestone/{milestone_id}/game_stats": "get_game_stats",
    "/live_data/events/{event_ticker}": "get_event_live_data",
    "/live_data/weather/{city}": "get_weather_index",
    "/live_data/weather/{city}/calibrations": "get_weather_index_calibrations",
    "/structured_targets": "list_structured_targets",
    "/structured_targets/{structured_target_id}": "get_structured_target",
    "/milestones/{milestone_id}": "get_milestone",
    "/milestones": "get_milestones",
    "/weather_point_markets/availability": "get_weather_point_availability",
    "/concert_cancellation_markets/availability": "get_concert_cancellation_availability",
    "/concert_cancellation_markets/concerts": "list_concerts",
    "/concert_cancellation_markets/concerts/search": "search_concerts",
    "/multivariate_event_collections/{collection_ticker}": "get_multivariate_collection",
    "/multivariate_event_collections": "list_multivariate_collections",
    "/incentive_programs": "list_incentive_programs",
    "/incentive_schedules": "get_incentive_schedules",
    "/fcm/fills": "get_fcm_fills",
    "/fcm/orders": "get_fcm_orders",
    "/fcm/subtraders": "list_fcm_subtraders",
    "/fcm/subtraders/event_contract_daily_cap": "get_fcm_event_contract_daily_cap",
    "/fcm/subtraders/blocked_categories": "get_fcm_subtrader_blocked_categories",
    "/fcm/positions": "get_fcm_positions",
    "/historical/cutoff": "get_historical_cutoff",
    "/historical/markets/{ticker}/candlesticks": "get_historical_market_candlesticks",
    "/historical/fills": "get_historical_fills",
    "/historical/orders": "list_historical_orders",
    "/historical/positions": "get_historical_positions",
    "/historical/trades": "get_historical_trades",
    "/historical/markets": "list_historical_markets",
    "/historical/markets/{ticker}": "get_historical_market",
}

# name, auth, args, scalar query (decoded), repeated query values.
NEW_CASES: list[
    tuple[str, bool, dict[str, Any], dict[str, str], dict[str, list[str]]]
] = [
    (
        "get_event_contract_exit_triggers",
        True,
        {"ticker": "TICK", "subaccount": 1, "kind": "bracket"},
        {"subaccount": "1", "kind": "bracket"},
        {},
    ),
    (
        "get_series_fee_changes",
        False,
        {"series_ticker": "KX", "show_historical": True},
        {"series_ticker": "KX", "show_historical": "true"},
        {},
    ),
    ("get_exchange_user_data_timestamp", False, {}, {}, {}),
    (
        "get_market_orderbooks",
        False,
        {"tickers": ["A", "BB"]},
        {},
        {"tickers": ["A", "BB"]},
    ),
    (
        "get_event_candlesticks",
        False,
        {
            "series_ticker": "SER",
            "ticker": "EVT",
            "start_ts": 1,
            "end_ts": 2,
            "period_interval": 60,
        },
        {"start_ts": "1", "end_ts": "2", "period_interval": "60"},
        {},
    ),
    (
        "list_multivariate_events",
        False,
        {
            "limit": 10,
            "cursor": "c",
            "series_ticker": "SER",
            "tickers": "E1,E2",
            "with_nested_markets": True,
        },
        {
            "limit": "10",
            "cursor": "c",
            "series_ticker": "SER",
            "tickers": "E1,E2",
            "with_nested_markets": "true",
        },
        {},
    ),
    (
        "get_event_fee_changes",
        False,
        {"event_ticker": "EV", "limit": 50, "cursor": "c"},
        {"event_ticker": "EV", "limit": "50", "cursor": "c"},
        {},
    ),
    ("get_event_metadata", False, {"event_ticker": "EV"}, {}, {}),
    (
        "get_event_forecast_percentile_history",
        True,
        {
            "series_ticker": "SER",
            "ticker": "EVT",
            "percentiles": [0, 5000],
            "start_ts": 1,
            "end_ts": 2,
            "period_interval": 0,
        },
        {"start_ts": "1", "end_ts": "2", "period_interval": "0"},
        {"percentiles": ["0", "5000"]},
    ),
    (
        "get_order_group",
        True,
        {"order_group_id": "OG", "subaccount": 0},
        {"subaccount": "0"},
        {},
    ),
    (
        "list_intra_exchange_instance_transfers",
        True,
        {"limit": 5, "cursor": "c"},
        {"limit": "5", "cursor": "c"},
        {},
    ),
    (
        "get_intra_exchange_instance_transfer",
        True,
        {"transfer_id": "TID"},
        {},
        {},
    ),
    (
        "get_credit_history",
        True,
        {"limit": 10, "cursor": "c", "min_ts": 1, "max_ts": 2},
        {"limit": "10", "cursor": "c", "min_ts": "1", "max_ts": "2"},
        {},
    ),
    ("get_communications_id", True, {}, {}, {}),
    (
        "list_block_trade_proposals",
        True,
        {"cursor": "c", "market_ticker": "MKT", "limit": 10, "status": "open"},
        {"cursor": "c", "market_ticker": "MKT", "limit": "10", "status": "open"},
        {},
    ),
    (
        "list_rfqs",
        True,
        {
            "cursor": "c",
            "event_ticker": "EV",
            "market_ticker": "MKT",
            "subaccount": 2,
            "limit": 10,
            "status": "open",
            "creator_user_id": "user-1",
            "user_filter": "self",
        },
        {
            "cursor": "c",
            "event_ticker": "EV",
            "market_ticker": "MKT",
            "subaccount": "2",
            "limit": "10",
            "status": "open",
            "creator_user_id": "user-1",
            "user_filter": "self",
        },
        {},
    ),
    ("get_rfq", True, {"rfq_id": "RFQ"}, {}, {}),
    (
        "get_rfq_quote",
        True,
        {"rfq_id": "RFQ", "quote_id": "QTE"},
        {},
        {},
    ),
    (
        "list_quotes",
        True,
        {
            "cursor": "c",
            "min_ts": 1,
            "max_ts": 2,
            "limit": 25,
            "status": "open",
            "quote_creator_user_id": "maker",
            "user_filter": "self",
            "rfq_user_filter": "self",
            "rfq_creator_user_id": "creator",
            "rfq_creator_subtrader_id": "sub",
            "rfq_id": "RFQ",
        },
        {
            "cursor": "c",
            "min_ts": "1",
            "max_ts": "2",
            "limit": "25",
            "status": "open",
            "quote_creator_user_id": "maker",
            "user_filter": "self",
            "rfq_user_filter": "self",
            "rfq_creator_user_id": "creator",
            "rfq_creator_subtrader_id": "sub",
            "rfq_id": "RFQ",
        },
        {},
    ),
    ("get_quote", True, {"quote_id": "QTE"}, {}, {}),
    (
        "list_api_keys",
        True,
        {"fcm_subtrader_id": "user_sub"},
        {"fcm_subtrader_id": "user_sub"},
        {},
    ),
    ("get_account_api_usage_level_volume_progress", True, {}, {}, {}),
    (
        "get_live_data_by_milestone",
        False,
        {"milestone_id": "M1", "include_player_stats": True},
        {"include_player_stats": "true"},
        {},
    ),
    (
        "get_live_data",
        False,
        {"type": "football", "milestone_id": "M1", "include_player_stats": False},
        {"include_player_stats": "false"},
        {},
    ),
    ("get_game_stats", False, {"milestone_id": "M1"}, {}, {}),
    (
        "get_weather_index",
        False,
        {"city": "miami", "from": 1, "to": 2, "detailed": True},
        {"from": "1", "to": "2", "detailed": "true"},
        {},
    ),
    ("get_weather_index_calibrations", False, {"city": "miami"}, {}, {}),
    (
        "list_structured_targets",
        False,
        {
            "ids": ["u1", "u2"],
            "type": "player",
            "competition": "nfl",
            "page_size": 50,
            "cursor": "c",
        },
        {"type": "player", "competition": "nfl", "page_size": "50", "cursor": "c"},
        {"ids": ["u1", "u2"]},
    ),
    ("get_structured_target", False, {"structured_target_id": "ST"}, {}, {}),
    ("get_weather_point_availability", False, {}, {}, {}),
    (
        "get_concert_cancellation_availability",
        False,
        {"date_from": "2026-01-01", "date_to": "2026-02-01"},
        {"date_from": "2026-01-01", "date_to": "2026-02-01"},
        {},
    ),
    (
        "list_concerts",
        True,
        {
            "metro_id": "jambase:1",
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
            "page": 2,
        },
        {
            "metro_id": "jambase:1",
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
            "page": "2",
        },
        {},
    ),
    ("search_concerts", True, {"q": "beatles"}, {"q": "beatles"}, {}),
    (
        "list_incentive_programs",
        False,
        {
            "status": "active",
            "type": "volume",
            "incentive_description": "maker",
            "series_ticker": "SER",
            "event_ticker": "EV",
            "limit": 20,
            "cursor": "c",
        },
        {
            "status": "active",
            "type": "volume",
            "incentive_description": "maker",
            "series_ticker": "SER",
            "event_ticker": "EV",
            "limit": "20",
            "cursor": "c",
        },
        {},
    ),
    ("get_incentive_schedules", False, {}, {}, {}),
    (
        "get_fcm_fills",
        True,
        {"min_ts": 1, "max_ts": 2, "cursor": "c"},
        {"min_ts": "1", "max_ts": "2", "cursor": "c"},
        {},
    ),
    (
        "get_fcm_orders",
        True,
        {
            "subtrader_id": "sub",
            "client_order_ids": "a,b",
            "cursor": "c",
            "event_ticker": "EV",
            "ticker": "MKT",
            "min_ts": 1,
            "max_ts": 2,
            "status": "resting",
            "limit": 10,
        },
        {
            "subtrader_id": "sub",
            "client_order_ids": "a,b",
            "cursor": "c",
            "event_ticker": "EV",
            "ticker": "MKT",
            "min_ts": "1",
            "max_ts": "2",
            "status": "resting",
            "limit": "10",
        },
        {},
    ),
    ("list_fcm_subtraders", True, {}, {}, {}),
    (
        "get_fcm_event_contract_daily_cap",
        True,
        {"subtrader_id": "sub"},
        {"subtrader_id": "sub"},
        {},
    ),
    (
        "get_fcm_subtrader_blocked_categories",
        True,
        {"subtrader_id": "sub"},
        {"subtrader_id": "sub"},
        {},
    ),
    ("get_target_balance_allocation", True, {}, {}, {}),
    (
        "get_fcm_positions",
        True,
        {
            "subtrader_id": "sub",
            "ticker": "MKT",
            "event_ticker": "EV",
            "count_filter": "position",
            "settlement_status": "unsettled",
            "limit": 10,
            "cursor": "c",
        },
        {
            "subtrader_id": "sub",
            "ticker": "MKT",
            "event_ticker": "EV",
            "count_filter": "position",
            "settlement_status": "unsettled",
            "limit": "10",
            "cursor": "c",
        },
        {},
    ),
]


def _concrete_path(name: str, args: dict[str, Any]) -> str:
    matches = [path for path, tool in GET_TOOLS.items() if tool == name]
    assert len(matches) == 1, name
    path = "/trade-api/v2" + matches[0]
    for key, value in args.items():
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, str(value))
    assert "{" not in path, path
    return path


def _resolve(
    node: Any, root: dict[str, Any], seen: frozenset[str] = frozenset()
) -> Any:
    """Resolve OpenAPI ``$ref``. Siblings of a ``$ref`` are ignored (OAS 3.0)."""
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {"$ref": ref}
        cur: Any = root
        for part in ref[2:].split("/"):
            cur = cur[part]
        return _resolve(cur, root, seen | {ref})
    if isinstance(node, dict):
        return {key: _resolve(value, root) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve(value, root) for value in node]
    return node


def _enum_values(schema: dict[str, Any]) -> list[Any] | None:
    if "enum" in schema:
        return list(schema["enum"])
    consts = [
        item["const"]
        for item in schema.get("anyOf", [])
        if isinstance(item, dict) and "const" in item
    ]
    if consts:
        return consts
    if "const" in schema:
        return [schema["const"]]
    return None


def _flat(schema: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "type",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    )
    out: dict[str, Any] = {key: schema[key] for key in keys if key in schema}
    enum = _enum_values(schema)
    if enum is not None:
        out["enum"] = sorted(enum, key=lambda item: (str(type(item)), item))
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        out["items"] = _flat(schema["items"])
    return out


def _operation_params(root: dict[str, Any], path: str) -> list[dict[str, Any]]:
    item = _resolve(root["paths"][path], root)
    operation = item["get"]
    combined = list(item.get("parameters") or []) + list(
        operation.get("parameters") or []
    )
    params: list[dict[str, Any]] = []
    for raw in combined:
        if isinstance(raw, dict) and raw.get("name"):
            params.append(raw)
    return params


async def _call(name: str, args: dict[str, Any]) -> Any:
    return await server.ToolRegistry.get_handler(name)(args)


def _assert_query(
    request: httpx.Request,
    scalar: dict[str, str],
    multi: dict[str, list[str]],
) -> None:
    params = request.url.params
    assert set(params.keys()) == set(scalar) | set(multi)
    for key, value in scalar.items():
        assert params.get_list(key) == [value], key
    for key, values in multi.items():
        assert params.get_list(key) == values, key


@pytest.fixture
def wire(make_client, rsa_key_file, monkeypatch):
    def _respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    client, requests = make_client(
        _respond,
        api_key="key-id",
        private_key_path=rsa_key_file,
    )
    monkeypatch.setattr(server, "kalshi_client", client)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "auth", "args", "scalar", "multi"),
    NEW_CASES,
    ids=[case[0] for case in NEW_CASES],
)
async def test_new_get_tool_path_query_and_parsed_response(
    wire, name: str, auth: bool, args: dict[str, Any], scalar: dict[str, str], multi
) -> None:
    assert auth in (True, False)
    path = _concrete_path(name, args)
    result = handler_result(await _call(name, args))
    sent = wire[-1]
    assert sent.method == "GET"
    assert sent.url.path == path
    _assert_query(sent, scalar, multi)
    assert result == {"ok": True, "path": path}


@pytest.mark.asyncio
async def test_new_gets_match_auth_and_public_credentials(
    make_client, monkeypatch
) -> None:
    """Authenticated GETs refuse a keyless client. Public GETs do not sign."""
    client, requests = make_client(lambda req: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(server, "kalshi_client", client)
    for name, auth, args, _scalar, _multi in NEW_CASES:
        before = len(requests)
        if auth:
            with pytest.raises(ValueError, match="requires Kalshi credentials"):
                await _call(name, args)
            assert len(requests) == before
        else:
            await _call(name, args)
            assert len(requests) == before + 1
            assert "KALSHI-ACCESS-KEY" not in requests[-1].headers


@pytest.mark.asyncio
async def test_optional_query_is_omitted_and_constraints_hold(wire) -> None:
    await _call("get_series_fee_changes", {})
    _assert_query(wire[-1], {}, {})

    await _call("list_multivariate_events", {"collection_ticker": "COL"})
    _assert_query(wire[-1], {"collection_ticker": "COL"}, {})

    await _call("get_weather_index", {"city": "miami", "last_sec": 60})
    _assert_query(wire[-1], {"last_sec": "60"}, {})
    assert "from" not in wire[-1].url.params

    with pytest.raises(ValidationError, match="cannot be used together"):
        await _call(
            "list_multivariate_events",
            {"collection_ticker": "COL", "series_ticker": "SER"},
        )
    with pytest.raises(ValidationError, match="last_sec cannot be combined"):
        await _call(
            "get_weather_index", {"city": "miami", "last_sec": 60, "from": 1, "to": 2}
        )
    with pytest.raises(ValidationError, match="last_sec cannot be combined"):
        await _call("get_weather_index", {"city": "miami", "last_sec": 60, "to": 2})
    with pytest.raises(ValidationError, match="from must be paired"):
        await _call("get_weather_index", {"city": "miami", "from": 1})

    ListMultivariateEventsRequest(series_ticker="SER")
    ListMultivariateEventsRequest()
    GetWeatherIndexRequest(city="miami", to=2)
    with pytest.raises(ValidationError):
        GetEventCandlesticksRequest(
            series_ticker="SER", ticker="EVT", start_ts=1, end_ts=2, period_interval=5
        )
    with pytest.raises(ValidationError):
        GetMarketOrderbooksRequest(tickers=[])
    with pytest.raises(ValidationError):
        SearchConcertsRequest(q="x")

    schema = GetWeatherIndexRequest.to_mcp_input_schema()
    assert "from" in schema["properties"]
    assert "from_" not in schema["properties"]
    assert "city" in schema["required"]
    assert "from" not in schema["required"]


def test_pinned_spec_get_paths_each_have_a_read_only_tool() -> None:
    """Fail if any v3.36.0 GET path has no read-only tool."""
    root = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert root["info"]["version"] == "3.36.0"
    spec_gets = {
        path
        for path, item in root["paths"].items()
        if isinstance(item, dict) and "get" in item
    }
    assert spec_gets == set(GET_TOOLS)
    assert len(GET_TOOLS) == len(set(GET_TOOLS.values()))

    tools = {tool.name: tool for tool in server.ToolRegistry.get_tools()}
    for path, name in GET_TOOLS.items():
        tool = tools[name]
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True, name
        assert path in spec_gets

    read_only = {
        name
        for name, tool in tools.items()
        if tool.annotations is not None and tool.annotations.read_only_hint is True
    }
    assert read_only - NON_GET_READS == set(GET_TOOLS.values())

    called = {
        call.normalized_path
        for call in extract_client_calls(CLIENT)
        if call.method == "GET"
    }
    missing = sorted(path for path in spec_gets if normalize_path(path) not in called)
    assert missing == []


def test_new_get_parameters_match_the_pinned_spec() -> None:
    """Names, required flags, enums, and numeric bounds match the spec."""
    root = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    tools = {tool.name: tool for tool in server.ToolRegistry.get_tools()}
    new_names = {case[0] for case in NEW_CASES}
    assert len(new_names) == 42
    for path, name in GET_TOOLS.items():
        if name not in new_names:
            continue
        spec_params = _operation_params(root, path)
        tool = tools[name]
        schema = getattr(tool, "input_schema", None) or tool.inputSchema
        assert set(schema["properties"]) == {param["name"] for param in spec_params}
        required = {
            param["name"] for param in spec_params if param.get("required") is True
        }
        assert set(schema["required"]) == required, name
        for param in spec_params:
            got = _flat(schema["properties"][param["name"]])
            want = _flat(param.get("schema") or {})
            assert got == want, (name, param["name"], got, want)
