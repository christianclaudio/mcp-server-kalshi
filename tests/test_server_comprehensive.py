"""Comprehensive tests for server module, all tool handlers, readonly mode, and entrypoints."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import FakeClient, handler_result

from mcp_server_kalshi import server
from mcp_server_kalshi.config import Settings


async def test_all_24_handlers_execute_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(
        get_markets={"markets": []},
        get_market={"market": {"ticker": "M-1", "title": "Market 1"}},
        get_events={"events": []},
        get_event={"event": {"event_ticker": "E-1"}},
        get_series_list={"series": []},
        get_series={
            "series": {
                "series_ticker": "S-1",
                "contract_url": "https://example.com/cert.pdf",
            }
        },
        get_market_orderbook={"orderbook": {}},
        get_market_candlesticks={"candlesticks": []},
        get_market_trades={"trades": []},
        get_exchange_status={"status": "active"},
        get_exchange_schedule={"schedule": {}},
        get_balance={"balance": 1000},
        get_positions={"positions": []},
        get_fills={"fills": []},
        get_settlements={"settlements": []},
        get_orders={"orders": []},
        get_order={"order": {"order_id": "ord-1"}},
        create_order={"order": {"order_id": "ord-1"}},
        cancel_order={"canceled": True},
        amend_order={"amended": True},
        decrease_order={"decreased": True},
    )
    monkeypatch.setattr(server, "kalshi_client", fake)

    # 1. list_markets
    out = handler_result(await server.handle_list_markets({"status": "open"}))
    assert "markets" in out

    # 2. get_market
    out = handler_result(await server.handle_get_market({"ticker": "M-1"}))
    assert "market" in out

    # 3. list_events
    out = handler_result(await server.handle_list_events({}))
    assert "events" in out

    # 4. get_event
    out = handler_result(await server.handle_get_event({"event_ticker": "E-1"}))
    assert "event" in out

    # 5. list_series
    out = handler_result(await server.handle_list_series({}))
    assert "series" in out

    # 6. get_series
    out = handler_result(await server.handle_get_series({"series_ticker": "S-1"}))
    assert "series" in out

    # 7. get_market_orderbook
    out = handler_result(await server.handle_get_market_orderbook({"ticker": "M-1"}))
    assert "orderbook" in out

    # 8. get_market_candlesticks
    out = handler_result(
        await server.handle_get_market_candlesticks(
            {"ticker": "M-1", "lookback_hours": 12, "period_interval": 60}
        )
    )
    assert "candlesticks" in out

    # 9. get_market_trades
    out = handler_result(await server.handle_get_market_trades({"ticker": "M-1"}))
    assert "trades" in out

    # 10. get_market_rules
    out = handler_result(await server.handle_get_market_rules({"ticker": "M-1"}))
    assert out["ticker"] == "M-1"

    # 11. fetch_rules_pdf direct url and document=certification
    with patch(
        "mcp_server_kalshi.server.fetch_pdf_text",
        new=AsyncMock(return_value={"text": "rules"}),
    ):
        out = handler_result(
            await server.handle_fetch_rules_pdf(
                {"url": "https://example.com/rules.pdf"}
            )
        )
        assert out["text"] == "rules"

        out_cert = handler_result(
            await server.handle_fetch_rules_pdf(
                {"series_ticker": "S-1", "document": "certification"}
            )
        )
        assert out_cert["text"] == "rules"

    # 12. get_environment
    out = handler_result(await server.handle_get_environment({}))
    assert "environment" in out

    # 13. get_exchange_status
    out = handler_result(await server.handle_get_exchange_status({}))
    assert "status" in out

    # 14. get_exchange_schedule
    out = handler_result(await server.handle_get_exchange_schedule({}))
    assert "schedule" in out

    # 15. get_balance
    out = handler_result(await server.handle_get_balance({}))
    assert "balance" in out

    # 16. get_positions
    out = handler_result(await server.handle_get_positions({}))
    assert "positions" in out

    # 17. get_fills
    out = handler_result(await server.handle_get_fills({}))
    assert "fills" in out

    # 18. get_settlements
    out = handler_result(await server.handle_get_settlements({}))
    assert "settlements" in out

    # 19. list_orders
    out = handler_result(await server.handle_list_orders({}))
    assert "orders" in out

    # 20. get_order
    out = handler_result(await server.handle_get_order({"order_id": "ord-1"}))
    assert "order" in out

    # 21. create_order (confirmed)
    out = handler_result(
        await server.handle_create_order(
            {
                "ticker": "M-1",
                "action": "buy",
                "side": "yes",
                "count": 10,
                "limit_price": 50,
                "confirm": True,
            }
        )
    )
    assert out["placed"] is True

    # 22. cancel_order
    out = handler_result(await server.handle_cancel_order({"order_id": "ord-1"}))
    assert out["canceled"] is True

    # 23. amend_order (preview vs confirmed)
    preview = handler_result(
        await server.handle_amend_order(
            {
                "order_id": "ord-1",
                "ticker": "M-1",
                "action": "buy",
                "side": "yes",
                "count": 10,
                "limit_price": 55,
                "confirm": False,
            }
        )
    )
    assert preview["preview"] is True
    confirmed = handler_result(
        await server.handle_amend_order(
            {
                "order_id": "ord-1",
                "ticker": "M-1",
                "action": "buy",
                "side": "yes",
                "count": 10,
                "limit_price": 55,
                "confirm": True,
            }
        )
    )
    assert confirmed["amended"] is True

    # 24. decrease_order
    out = handler_result(
        await server.handle_decrease_order({"order_id": "ord-1", "reduce_by": 2})
    )
    assert out["decreased"] is True

    # 25. batch_create_orders (preview vs confirm)
    preview_batch = handler_result(
        await server.handle_batch_create_orders(
            {
                "orders": [
                    {
                        "ticker": "M-1",
                        "action": "buy",
                        "side": "yes",
                        "count": 10,
                        "limit_price": 50,
                    }
                ],
                "confirm": False,
            }
        )
    )
    assert preview_batch["preview"] is True
    assert preview_batch["batch_size"] == 1

    confirmed_batch = handler_result(
        await server.handle_batch_create_orders(
            {
                "orders": [
                    {
                        "ticker": "M-1",
                        "action": "buy",
                        "side": "yes",
                        "count": 10,
                        "limit_price": 50,
                    },
                    {
                        "ticker": "M-1",
                        "action": "sell",
                        "side": "yes",
                        "count": 5,
                        "limit_price": 60,
                        "client_order_id": "custom-id-123",
                    },
                ],
                "confirm": True,
            }
        )
    )
    assert confirmed_batch["placed"] is True
    assert confirmed_batch["batch_size"] == 2

    # 26. batch_cancel_orders
    out = handler_result(
        await server.handle_batch_cancel_orders({"order_ids": ["ord-1", "ord-2"]})
    )
    assert out["canceled"] is True
    assert out["count"] == 2

    # 27. get_portfolio_summary
    out = handler_result(await server.handle_get_portfolio_summary({}))
    assert out == {"ok": True}

    # 28. get_tags_by_categories
    out = handler_result(await server.handle_get_tags_by_categories({}))
    assert out == {"ok": True}

    # 29. get_sports_filters
    out = handler_result(await server.handle_get_sports_filters({}))
    assert out == {"ok": True}

    # 30. get_milestones
    out = handler_result(await server.handle_get_milestones({"limit": 5}))
    assert out == {"ok": True}

    # 31. get_milestone
    out = handler_result(await server.handle_get_milestone({"milestone_id": "m-1"}))
    assert out == {"ok": True}

    # 32. get_event_live_data
    out = handler_result(
        await server.handle_get_event_live_data({"event_ticker": "EV-1"})
    )
    assert out == {"ok": True}

    # 33. list_multivariate_collections
    out = handler_result(
        await server.handle_list_multivariate_collections({"limit": 5})
    )
    assert out == {"ok": True}

    # 34. get_multivariate_collection
    out = handler_result(
        await server.handle_get_multivariate_collection({"collection_ticker": "COL-1"})
    )
    assert out == {"ok": True}

    # 35. list_order_groups
    out = handler_result(await server.handle_list_order_groups({"limit": 5}))
    assert out == {"ok": True}

    # 36. cancel_order_group
    out = handler_result(
        await server.handle_cancel_order_group({"order_group_id": "grp-1"})
    )
    assert out["canceled"] is True


async def test_get_market_rules_handles_event_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(
        get_market={"market": {"event_ticker": "EV-1", "ticker": "M-1"}},
        get_series={"series": {"contract_terms_url": "url"}},
        get_event=RuntimeError("event fail"),
    )
    monkeypatch.setattr(server, "kalshi_client", fake)
    out = handler_result(await server.handle_get_market_rules({"ticker": "M-1"}))
    assert out["ticker"] == "M-1"


async def test_handle_call_tool_dispatch_and_error_sanitization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Success call
    resp = await server.handle_call_tool("get_environment", {})
    assert len(resp) == 1
    assert "environment" in resp[0].text

    # None arguments passed
    resp_none = await server.handle_call_tool("get_environment", None)
    assert len(resp_none) == 1

    # Exception raised in handler with sensitive token
    def exploding_handler(req: dict[str, Any]) -> Any:
        raise RuntimeError("Failed with Bearer my_secret_token_12345")

    monkeypatch.setattr(
        server.ToolRegistry, "get_handler", lambda name: exploding_handler
    )
    err_resp = await server.handle_call_tool("get_environment", {})
    assert "my_secret_token" not in err_resp[0].text
    assert "Bearer [REDACTED]" in err_resp[0].text


def test_readonly_mode_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    readonly_settings = Settings(_env_file=None, KALSHI_READONLY=True)
    monkeypatch.setattr(server, "settings", readonly_settings)

    tools = server.ToolRegistry.get_tools()
    tool_names = {t.name for t in tools}
    assert "list_markets" in tool_names
    assert "create_order" not in tool_names
    assert "cancel_order" not in tool_names
    assert "amend_order" not in tool_names
    assert "decrease_order" not in tool_names
    assert "batch_create_orders" not in tool_names
    assert "batch_cancel_orders" not in tool_names
    assert "cancel_order_group" not in tool_names
    assert len(tools) == 29

    # Handler lookup for read-only tool in readonly mode succeeds
    handler = server.ToolRegistry.get_handler("list_markets")
    assert callable(handler)

    # Handler lookup for mutating tool in readonly mode raises
    with pytest.raises(ValueError, match="read-only mode"):
        server.ToolRegistry.get_handler("create_order")


async def test_handle_list_tools() -> None:
    tools = await server.handle_list_tools()
    assert len(tools) >= 20


def test_params_drop_fields() -> None:
    from mcp_server_kalshi.kalshi_client.schemas import ListMarketsRequest

    data = server._params(
        {"status": "open", "limit": 10}, ListMarketsRequest, drop=("status",)
    )
    assert "status" not in data
    assert data["limit"] == 10


def test_serialize_string_and_dict() -> None:
    assert server._serialize("plain string") == "plain string"
    assert '"key": "value"' in server._serialize({"key": "value"})


def test_annotations_variations(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp.types as t

    ann1 = server._annotations(read_only=True, destructive=False)
    ro1 = getattr(ann1, "read_only_hint", getattr(ann1, "readOnlyHint", None))
    assert ann1 is not None and ro1 is True

    ann2 = server._annotations(
        read_only=False, destructive=True, idempotent=True, open_world=True
    )
    dest2 = getattr(ann2, "destructive_hint", getattr(ann2, "destructiveHint", None))
    assert ann2 is not None and dest2 is True

    monkeypatch.setattr(t, "ToolAnnotations", None, raising=False)
    ann_none = server._annotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    assert ann_none is None


async def test_server_run(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_stdio = MagicMock()
    mock_stdio.__aenter__ = AsyncMock(return_value=("r", "w"))
    mock_stdio.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("mcp.server.stdio.stdio_server", lambda: mock_stdio)
    monkeypatch.setattr(server.server, "run", AsyncMock())

    await server.run()
    server.server.run.assert_called_once()


def test_main_and_entrypoints(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock = AsyncMock()
    monkeypatch.setattr(server, "run", run_mock)
    server.main()
    run_mock.assert_called_once()

    with patch("mcp_server_kalshi.server.main") as mock_main:
        import mcp_server_kalshi.__main__

        # test calling the entry point
        mcp_server_kalshi.__main__.main()
        mock_main.assert_called_once()


def test_handle_shutdown() -> None:
    with patch("os._exit") as mock_exit:
        server._handle_shutdown(15, None)
        mock_exit.assert_called_once_with(0)


def test_annotations_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class LegacyAnnotations:
        def __init__(self, **kwargs: Any) -> None:
            if any("_hint" in k for k in kwargs):
                raise TypeError("unexpected keyword argument")
            self.kwargs = kwargs

    monkeypatch.setattr(server.types, "ToolAnnotations", LegacyAnnotations)
    res: Any = server._annotations(
        read_only=True, destructive=False, idempotent=True, open_world=True
    )
    assert res.kwargs["readOnlyHint"] is True
    assert res.kwargs["destructiveHint"] is False
    assert res.kwargs["idempotentHint"] is True
    assert res.kwargs["openWorldHint"] is True

    res2: Any = server._annotations(read_only=True, destructive=False)
    assert "idempotentHint" not in res2.kwargs
    assert "openWorldHint" not in res2.kwargs


async def test_req_handlers() -> None:
    res = await server._req_list_tools(None)
    assert len(res.tools) > 0

    call_res = await server._req_call_tool(
        None, server.types.CallToolRequestParams(name="get_environment", arguments={})
    )
    assert call_res.is_error is False

    err_res = await server._req_call_tool(
        None, server.types.CallToolRequestParams(name="unknown_tool", arguments={})
    )
    assert err_res.is_error is True


async def test_run_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_app = MagicMock()
    mock_server = MagicMock()
    mock_server.serve = AsyncMock()

    monkeypatch.setattr(
        server.server, "streamable_http_app", MagicMock(return_value=mock_app)
    )
    monkeypatch.setattr("uvicorn.Server", MagicMock(return_value=mock_server))

    await server.run_streamable_http(host="0.0.0.0", port=9000)
    mock_server.serve.assert_called_once()


def test_main_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "mcp-server-kalshi",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
        ],
    )
    run_streamable_mock = AsyncMock()
    monkeypatch.setattr(server, "run_streamable_http", run_streamable_mock)
    server.main()
    run_streamable_mock.assert_called_once_with(host="127.0.0.1", port=9000)


async def test_handle_list_series_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_series = [{"ticker": f"SERIES-{i}"} for i in range(75)]
    monkeypatch.setattr(
        server.kalshi_client,
        "get_series_list",
        AsyncMock(return_value={"series": mock_series}),
    )
    out = handler_result(await server.handle_list_series({}))
    assert out["truncated"] is True
    assert out["total_series_count"] == 75
    assert len(out["series"]) == 50
    assert "capped to 50 items" in out["note"]

    monkeypatch.setattr(
        server.kalshi_client,
        "get_series_list",
        AsyncMock(return_value={}),
    )
    assert handler_result(await server.handle_list_series({})) == {}
