"""Wire-level tests for representative KalshiAPIClient endpoint methods.

Confirms each builds the right path + params/body, through an injected mock transport.
"""

import httpx


async def test_get_market_builds_path(make_client):
    client, requests = make_client(
        lambda req: httpx.Response(200, json={"market": {"ticker": "KXELONMARS-99"}})
    )
    out = await client.get_market("KXELONMARS-99")
    assert out == {"market": {"ticker": "KXELONMARS-99"}}
    assert requests[0].url.path == "/trade-api/v2/markets/KXELONMARS-99"


async def test_get_market_orderbook_omits_depth_when_none(make_client):
    client, requests = make_client(lambda req: httpx.Response(200, json={}))
    await client.get_market_orderbook("X-1")
    assert requests[0].url.path == "/trade-api/v2/markets/X-1/orderbook"
    assert "depth" not in dict(requests[0].url.params)

    await client.get_market_orderbook("X-1", depth=5)
    assert dict(requests[1].url.params) == {"depth": "5"}


async def test_candlesticks_derives_series_and_assembles_params(make_client):
    client, requests = make_client(lambda req: httpx.Response(200, json={}))
    await client.get_market_candlesticks(
        ticker="KXELONMARS-99",
        start_ts=1000,
        end_ts=2000,
        period_interval=60,
    )
    # Series ('KXELONMARS') is derived from the market ticker when not supplied.
    assert (
        requests[0].url.path
        == "/trade-api/v2/series/KXELONMARS/markets/KXELONMARS-99/candlesticks"
    )
    assert dict(requests[0].url.params) == {
        "start_ts": "1000",
        "end_ts": "2000",
        "period_interval": "60",
    }


async def test_candlesticks_honors_explicit_series(make_client):
    client, requests = make_client(lambda req: httpx.Response(200, json={}))
    await client.get_market_candlesticks(
        ticker="KXELONMARS-99",
        start_ts=1,
        end_ts=2,
        period_interval=1,
        series_ticker="OVERRIDE",
    )
    assert requests[0].url.path.startswith("/trade-api/v2/series/OVERRIDE/")


async def test_create_order_posts_payload_when_authenticated(make_client, rsa_key_file):
    import json as _json

    captured = {}

    def responder(req):
        captured["path"] = req.url.path
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={"order": {"order_id": "ord_1"}})

    client, _ = make_client(responder, api_key="key-id", private_key_path=rsa_key_file)
    payload = {"ticker": "X-1", "side": "bid", "price": "0.4000", "count": "3"}
    out = await client.create_order(payload)

    assert out == {"order": {"order_id": "ord_1"}}
    assert captured["path"] == "/trade-api/v2/portfolio/events/orders"
    assert captured["body"] == payload


async def test_all_unauthenticated_and_authenticated_endpoints(
    make_client, rsa_key_file
):
    client, requests = make_client(
        lambda req: httpx.Response(200, json={"ok": True}),
        api_key="key-id",
        private_key_path=rsa_key_file,
    )

    # Exchange
    await client.get_exchange_status()
    assert requests[-1].url.path == "/trade-api/v2/exchange/status"

    await client.get_exchange_schedule()
    assert requests[-1].url.path == "/trade-api/v2/exchange/schedule"

    # Markets
    await client.get_markets({"status": "open"})
    assert requests[-1].url.path == "/trade-api/v2/markets"

    await client.get_market_trades({"ticker": "X-1"})
    assert requests[-1].url.path == "/trade-api/v2/markets/trades"

    # Candlesticks with include_latest_before_start
    await client.get_market_candlesticks(
        ticker="X-1",
        start_ts=100,
        end_ts=200,
        period_interval=60,
        include_latest_before_start=True,
    )
    assert requests[-1].url.params["include_latest_before_start"] == "true"

    # Events
    await client.get_events({"status": "open"})
    assert requests[-1].url.path == "/trade-api/v2/events"

    await client.get_event("EV-1", with_nested_markets=True)
    assert requests[-1].url.path == "/trade-api/v2/events/EV-1"
    assert requests[-1].url.params["with_nested_markets"] == "true"

    # Series
    await client.get_series_list({"category": "politics"})
    assert requests[-1].url.path == "/trade-api/v2/series"

    await client.get_series("SERIES-1")
    assert requests[-1].url.path == "/trade-api/v2/series/SERIES-1"

    # Portfolio
    await client.get_balance()
    assert requests[-1].url.path == "/trade-api/v2/portfolio/balance"

    await client.get_positions({"ticker": "X-1"})
    assert requests[-1].url.path == "/trade-api/v2/portfolio/positions"

    await client.get_fills({"ticker": "X-1"})
    assert requests[-1].url.path == "/trade-api/v2/portfolio/fills"

    await client.get_settlements({"ticker": "X-1"})
    assert requests[-1].url.path == "/trade-api/v2/portfolio/settlements"

    # Orders
    await client.get_orders({"status": "resting"})
    assert requests[-1].url.path == "/trade-api/v2/portfolio/orders"

    await client.get_order("ord-123")
    assert requests[-1].url.path == "/trade-api/v2/portfolio/orders/ord-123"

    await client.cancel_order("ord-123")
    assert requests[-1].url.path == "/trade-api/v2/portfolio/events/orders/ord-123"
    assert requests[-1].method == "DELETE"

    await client.amend_order("ord-123", {"count": "5"})
    assert (
        requests[-1].url.path == "/trade-api/v2/portfolio/events/orders/ord-123/amend"
    )

    await client.decrease_order("ord-123", {"reduce_by": "1"})
    assert (
        requests[-1].url.path
        == "/trade-api/v2/portfolio/events/orders/ord-123/decrease"
    )

    # Batch orders
    await client.batch_create_orders([{"ticker": "X-1"}])
    assert requests[-1].url.path == "/trade-api/v2/portfolio/events/orders/batched"
    assert requests[-1].method == "POST"

    await client.batch_cancel_orders(["ord-1", "ord-2"])
    assert requests[-1].url.path == "/trade-api/v2/portfolio/events/orders/batched"
    assert requests[-1].method == "DELETE"

    # Search & Discovery
    await client.get_tags_by_categories()
    assert requests[-1].url.path == "/trade-api/v2/search/tags_by_categories"

    await client.get_sports_filters()
    assert requests[-1].url.path == "/trade-api/v2/search/filters_by_sport"

    # Milestones & Live Data
    await client.get_milestones({"limit": 10})
    assert requests[-1].url.path == "/trade-api/v2/milestones"

    await client.get_milestone("mile-1")
    assert requests[-1].url.path == "/trade-api/v2/milestones/mile-1"

    await client.get_event_live_data("EV-1")
    assert requests[-1].url.path == "/trade-api/v2/live_data/events/EV-1"

    # Multivariate
    await client.list_multivariate_collections({"limit": 5})
    assert requests[-1].url.path == "/trade-api/v2/multivariate_event_collections"

    await client.get_multivariate_collection("COL-1")
    assert requests[-1].url.path == "/trade-api/v2/multivariate_event_collections/COL-1"

    # Summary & Order Groups
    await client.get_portfolio_summary()
    assert (
        requests[-1].url.path
        == "/trade-api/v2/portfolio/summary/total_resting_order_value"
    )

    await client.list_order_groups({"limit": 5})
    assert requests[-1].url.path == "/trade-api/v2/portfolio/order_groups"

    await client.cancel_order_group("grp-1")
    assert requests[-1].url.path == "/trade-api/v2/portfolio/order_groups/grp-1"
    assert requests[-1].method == "DELETE"
