import urllib.parse
from typing import Any

from .base import BaseAPIClient


def series_ticker_from_market(ticker: str) -> str:
    """Best-effort derivation of a series ticker from a market ticker.

    Kalshi tickers are ``SERIES-EVENTSUFFIX[-STRIKE...]`` (e.g. ``KXELONMARS-99`` →
    ``KXELONMARS``, ``HIGHNY-24JAN01-T60`` → ``HIGHNY``), so the series is the segment
    before the first hyphen.
    """
    return ticker.split("-", 1)[0]


REDUCE_ONLY_IOC_MESSAGE = (
    "reduce_only=true requires time_in_force='immediate_or_cancel'. Kalshi rejects "
    "reduce_only orders with any other time_in_force "
    "(https://docs.kalshi.com/api-reference/orders/create-order-v2)."
)


def build_create_order_payload(
    *,
    ticker: str,
    action: str,
    side: str,
    count: float,
    limit_price_cents: int,
    time_in_force: str = "good_till_canceled",
    self_trade_prevention_type: str = "taker_at_cross",
    post_only: bool = False,
    reduce_only: bool = False,
    client_order_id: str | None = None,
    expiration_ts: int | None = None,
    order_group_id: str | None = None,
    cancel_order_on_pause: bool | None = None,
    subaccount: int | None = None,
    exchange_index: int | None = None,
) -> dict[str, Any]:
    """Translate an intuitive (action, side, cents) order into the Kalshi V2 payload.

    Kalshi's V2 order endpoint quotes everything from the YES leg via a book ``side`` of
    ``bid`` (buy YES) or ``ask`` (sell YES). We accept the natural buy/sell + yes/no model
    with a whole-cent limit price for the chosen outcome, and convert:

    | action | side | book side | YES-leg price |
    |--------|------|-----------|---------------|
    | buy    | yes  | bid       | cents         |
    | sell   | yes  | ask       | cents         |
    | buy    | no   | ask       | 100 - cents   |
    | sell   | no   | bid       | 100 - cents   |

    (Buying NO @ p ≡ selling YES @ 1-p, since YES + NO = $1.)
    """
    if reduce_only and time_in_force != "immediate_or_cancel":
        raise ValueError(REDUCE_ONLY_IOC_MESSAGE)
    action = action.lower()
    side = side.lower()
    yes_leg_cents = limit_price_cents if side == "yes" else 100 - limit_price_cents
    book_side = "bid" if (action, side) in {("buy", "yes"), ("sell", "no")} else "ask"

    payload: dict[str, Any] = {
        "ticker": ticker,
        "side": book_side,
        "count": str(count) if count != int(count) else str(int(count)),
        "price": f"{yes_leg_cents / 100:.4f}",
        "time_in_force": time_in_force,
        "self_trade_prevention_type": self_trade_prevention_type,
    }
    if client_order_id:
        payload["client_order_id"] = client_order_id
    if post_only:
        payload["post_only"] = True
    if reduce_only:
        payload["reduce_only"] = True
    if expiration_ts:
        payload["expiration_time"] = expiration_ts
    # Optional CreateOrderV2Request fields (Kalshi OpenAPI v3.34.0); sent only when set.
    if order_group_id:
        payload["order_group_id"] = order_group_id
    if cancel_order_on_pause is not None:
        payload["cancel_order_on_pause"] = cancel_order_on_pause
    if subaccount is not None:
        payload["subaccount"] = subaccount
    if exchange_index is not None:
        payload["exchange_index"] = exchange_index
    return payload


def build_amend_order_payload(
    *,
    ticker: str,
    action: str,
    side: str,
    count: float,
    limit_price_cents: int,
    updated_client_order_id: str | None = None,
    client_order_id: str | None = None,
    expiration_time: int | None = None,
    exchange_index: int | None = None,
) -> dict[str, Any]:
    """Build the Kalshi V2 amend body, using the same YES-leg translation as create.

    ``expiration_time`` follows AmendOrderV2Request: omit it (None) to keep the current
    expiry, ``0`` removes it, a future Unix-seconds value sets it.
    """
    action = action.lower()
    side = side.lower()
    yes_leg_cents = limit_price_cents if side == "yes" else 100 - limit_price_cents
    book_side = "bid" if (action, side) in {("buy", "yes"), ("sell", "no")} else "ask"

    payload: dict[str, Any] = {
        "ticker": ticker,
        "side": book_side,
        "price": f"{yes_leg_cents / 100:.4f}",
        "count": str(count) if count != int(count) else str(int(count)),
    }
    if updated_client_order_id:
        payload["updated_client_order_id"] = updated_client_order_id
    if client_order_id:
        payload["client_order_id"] = client_order_id
    if expiration_time is not None:
        payload["expiration_time"] = expiration_time
    if exchange_index is not None:
        payload["exchange_index"] = exchange_index
    return payload


def build_decrease_order_payload(
    reduce_by: float | None = None,
    reduce_to: float | None = None,
    *,
    exchange_index: int | None = None,
    market_ticker: str | None = None,
) -> dict[str, Any]:
    """Build the Kalshi V2 decrease body. Exactly one of reduce_by / reduce_to required."""
    if (reduce_by is None) == (reduce_to is None):
        raise ValueError("Provide exactly one of reduce_by or reduce_to.")
    payload: dict[str, Any]
    if reduce_by is not None:
        payload = {
            "reduce_by": (
                str(reduce_by) if reduce_by != int(reduce_by) else str(int(reduce_by))
            )
        }
    else:
        assert reduce_to is not None  # guaranteed by the exactly-one check above
        payload = {
            "reduce_to": (
                str(reduce_to) if reduce_to != int(reduce_to) else str(int(reduce_to))
            )
        }
    # Optional DecreaseOrderV2Request routing fields (Kalshi OpenAPI v3.34.0).
    if exchange_index is not None:
        payload["exchange_index"] = exchange_index
    if market_ticker:
        payload["market_ticker"] = market_ticker
    return payload


class KalshiAPIClient(BaseAPIClient):
    """Async client for the Kalshi Trade API v2.

    Paths below are relative to the version-prefixed base URL configured on the client
    (e.g. ``https://demo-api.kalshi.co/trade-api/v2``).
    """

    # ---- Exchange -----------------------------------------------------------------
    async def get_exchange_status(self) -> Any:
        return await self.get("/exchange/status")

    async def get_exchange_schedule(self) -> Any:
        return await self.get("/exchange/schedule")

    # ---- Markets ------------------------------------------------------------------
    async def get_markets(self, params: dict[str, Any] | None = None) -> Any:
        return await self.get("/markets", params=params)

    async def get_market(self, ticker: str) -> Any:
        quoted = urllib.parse.quote(ticker, safe="")
        return await self.get(f"/markets/{quoted}")

    async def get_market_orderbook(self, ticker: str, depth: int | None = None) -> Any:
        params = {"depth": depth} if depth is not None else None
        quoted = urllib.parse.quote(ticker, safe="")
        return await self.get(f"/markets/{quoted}/orderbook", params=params)

    async def get_market_candlesticks(
        self,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int,
        series_ticker: str | None = None,
        include_latest_before_start: bool | None = None,
    ) -> Any:
        series = series_ticker or series_ticker_from_market(ticker)
        params: dict[str, Any] = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval,
        }
        if include_latest_before_start is not None:
            params["include_latest_before_start"] = include_latest_before_start
        quoted_series = urllib.parse.quote(series, safe="")
        quoted_ticker = urllib.parse.quote(ticker, safe="")
        return await self.get(
            f"/series/{quoted_series}/markets/{quoted_ticker}/candlesticks",
            params=params,
        )

    async def get_market_trades(self, params: dict[str, Any] | None = None) -> Any:
        return await self.get("/markets/trades", params=params)

    # ---- Events -------------------------------------------------------------------
    async def get_events(self, params: dict[str, Any] | None = None) -> Any:
        return await self.get("/events", params=params)

    async def get_event(
        self,
        event_ticker: str,
        with_nested_markets: bool | None = None,
        exclude_markets: bool | None = None,
    ) -> Any:
        query: dict[str, Any] = {}
        if with_nested_markets is not None:
            query["with_nested_markets"] = with_nested_markets
        if exclude_markets is not None:
            query["exclude_markets"] = exclude_markets
        params = query or None
        quoted = urllib.parse.quote(event_ticker, safe="")
        return await self.get(f"/events/{quoted}", params=params)

    # ---- Series -------------------------------------------------------------------
    async def get_series_list(self, params: dict[str, Any] | None = None) -> Any:
        return await self.get("/series", params=params)

    async def get_series(
        self, series_ticker: str, include_volume: bool | None = None
    ) -> Any:
        params = (
            {"include_volume": include_volume} if include_volume is not None else None
        )
        quoted = urllib.parse.quote(series_ticker, safe="")
        return await self.get(f"/series/{quoted}", params=params)

    # ---- Portfolio (auth) ---------------------------------------------------------
    async def get_balance(self, params: dict[str, Any] | None = None) -> Any:
        self._require_auth()
        return await self.get("/portfolio/balance", params=params)

    async def get_positions(self, params: dict[str, Any] | None = None) -> Any:
        self._require_auth()
        return await self.get("/portfolio/positions", params=params)

    async def get_fills(self, params: dict[str, Any] | None = None) -> Any:
        self._require_auth()
        return await self.get("/portfolio/fills", params=params)

    async def get_settlements(self, params: dict[str, Any] | None = None) -> Any:
        self._require_auth()
        return await self.get("/portfolio/settlements", params=params)

    # ---- Orders: reads (auth) -----------------------------------------------------
    async def get_orders(self, params: dict[str, Any] | None = None) -> Any:
        self._require_auth()
        return await self.get("/portfolio/orders", params=params)

    async def get_order(self, order_id: str) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.get(f"/portfolio/orders/{quoted}")

    # ---- Orders: writes / V2 (auth) -----------------------------------------------
    async def create_order(self, payload: dict[str, Any]) -> Any:
        self._require_auth()
        return await self.post("/portfolio/events/orders", json=payload)

    async def cancel_order(
        self, order_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.delete(f"/portfolio/events/orders/{quoted}", params=params)

    async def amend_order(
        self,
        order_id: str,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.post(
            f"/portfolio/events/orders/{quoted}/amend", json=payload, params=params
        )

    async def decrease_order(
        self,
        order_id: str,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.post(
            f"/portfolio/events/orders/{quoted}/decrease", json=payload, params=params
        )

    # ---- Batch Orders (auth) ------------------------------------------------------
    async def batch_create_orders(self, orders_payload: list[dict[str, Any]]) -> Any:
        """Batch create orders (V2); Kalshi scales the max batch size with the API tier."""
        self._require_auth()
        return await self.post(
            "/portfolio/events/orders/batched", json={"orders": orders_payload}
        )

    async def batch_cancel_orders(
        self,
        order_ids: list[str] | None = None,
        *,
        items: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Batch cancel orders (V2); Kalshi scales the max batch size with the API tier.

        Pass plain ``order_ids`` or prebuilt ``items`` (each ``{"order_id", ...}`` with the
        optional ``subaccount`` / ``exchange_index`` / ``market_ticker`` routing fields).
        """
        self._require_auth()
        if items is None:
            items = [{"order_id": oid} for oid in order_ids or []]
        return await self.delete(
            "/portfolio/events/orders/batched", json={"orders": items}
        )

    # ---- Search & Discovery (public) ---------------------------------------------
    async def get_tags_by_categories(self) -> Any:
        """Get tags grouped by series categories."""
        return await self.get("/search/tags_by_categories")

    async def get_sports_filters(self) -> Any:
        """Get discovery filters for sports markets."""
        return await self.get("/search/filters_by_sport")

    # ---- Milestones & Live Data (public) ------------------------------------------
    async def get_milestones(self, params: dict[str, Any] | None = None) -> Any:
        """Get milestone trackers across categories."""
        return await self.get("/milestones", params=params)

    async def get_milestone(self, milestone_id: str) -> Any:
        """Get specific milestone details by ID."""
        quoted = urllib.parse.quote(milestone_id, safe="")
        return await self.get(f"/milestones/{quoted}")

    async def get_event_live_data(
        self, event_ticker: str, range_hint: str | None = None
    ) -> Any:
        """Get real-time live scoreboard/data for an event (optional ``range`` hint)."""
        params = {"range": range_hint} if range_hint is not None else None
        quoted = urllib.parse.quote(event_ticker, safe="")
        return await self.get(f"/live_data/events/{quoted}", params=params)

    # ---- Multivariate & Combos (public) ------------------------------------------
    async def list_multivariate_collections(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """List multivariate / combo event collections."""
        return await self.get("/multivariate_event_collections", params=params)

    async def get_multivariate_collection(self, collection_ticker: str) -> Any:
        """Get details for a multivariate / combo event collection."""
        quoted = urllib.parse.quote(collection_ticker, safe="")
        return await self.get(f"/multivariate_event_collections/{quoted}")

    # ---- Total Resting Order Value (FCM only) & Order Groups (auth) -------------
    async def get_total_resting_order_value(self) -> Any:
        """Get the total value, in cents, of resting orders. FCM members only."""
        self._require_auth()
        return await self.get("/portfolio/summary/total_resting_order_value")

    async def list_order_groups(self, params: dict[str, Any] | None = None) -> Any:
        """List active order groups."""
        self._require_auth()
        return await self.get("/portfolio/order_groups", params=params)

    async def cancel_order_group(
        self, order_group_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Delete an order group and cancel all orders in it."""
        self._require_auth()
        quoted = urllib.parse.quote(order_group_id, safe="")
        return await self.delete(f"/portfolio/order_groups/{quoted}", params=params)

    # ---- Historical (auth where noted; OpenAPI v3.36.0) -----------------------
    async def get_historical_cutoff(self) -> Any:
        """GET /historical/cutoff — archive cutoff timestamps."""
        return await self.get("/historical/cutoff")

    async def list_historical_markets(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /historical/markets — archived markets."""
        return await self.get("/historical/markets", params=params)

    async def get_historical_market(self, ticker: str) -> Any:
        """GET /historical/markets/{ticker}."""
        quoted = urllib.parse.quote(ticker, safe="")
        return await self.get(f"/historical/markets/{quoted}")

    async def get_historical_market_candlesticks(
        self,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int,
    ) -> Any:
        """GET /historical/markets/{ticker}/candlesticks."""
        quoted = urllib.parse.quote(ticker, safe="")
        params = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval,
        }
        return await self.get(
            f"/historical/markets/{quoted}/candlesticks", params=params
        )

    async def get_historical_trades(self, params: dict[str, Any] | None = None) -> Any:
        """GET /historical/trades — public trades older than the cutoff."""
        return await self.get("/historical/trades", params=params)

    async def get_historical_fills(self, params: dict[str, Any] | None = None) -> Any:
        """GET /historical/fills — authenticated fills older than the cutoff."""
        self._require_auth()
        return await self.get("/historical/fills", params=params)

    async def list_historical_orders(self, params: dict[str, Any] | None = None) -> Any:
        """GET /historical/orders — archived canceled/executed orders."""
        self._require_auth()
        return await self.get("/historical/orders", params=params)

    async def get_historical_positions(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /historical/positions — settled positions after archival handoff."""
        self._require_auth()
        return await self.get("/historical/positions", params=params)

    # ---- Batch candlesticks & live data (OpenAPI v3.36.0) ---------------------
    async def batch_get_market_candlesticks(self, params: dict[str, Any]) -> Any:
        """GET /markets/candlesticks — candlesticks for many markets."""
        return await self.get("/markets/candlesticks", params=params)

    async def get_live_data_batch(self, params: dict[str, Any]) -> Any:
        """GET /live_data/batch — live data for multiple milestones."""
        return await self.get("/live_data/batch", params=params)

    # ---- Account limits (auth) / endpoint costs (public; OpenAPI v3.36.0) -----
    async def get_account_limits(self) -> Any:
        """GET /account/limits — Predictions API tier and token-bucket limits."""
        self._require_auth()
        return await self.get("/account/limits")

    async def get_account_endpoint_costs(self) -> Any:
        """GET /account/endpoint_costs — non-default endpoint token costs."""
        return await self.get("/account/endpoint_costs")

    # ---- Queue positions, deposits, withdrawals, subaccounts (auth) -----------
    async def get_order_queue_positions(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /portfolio/orders/queue_positions."""
        self._require_auth()
        return await self.get("/portfolio/orders/queue_positions", params=params)

    async def get_order_queue_position(self, order_id: str) -> Any:
        """GET /portfolio/orders/{order_id}/queue_position."""
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.get(f"/portfolio/orders/{quoted}/queue_position")

    async def list_deposits(self, params: dict[str, Any] | None = None) -> Any:
        """GET /portfolio/deposits."""
        self._require_auth()
        return await self.get("/portfolio/deposits", params=params)

    async def list_withdrawals(self, params: dict[str, Any] | None = None) -> Any:
        """GET /portfolio/withdrawals."""
        self._require_auth()
        return await self.get("/portfolio/withdrawals", params=params)

    async def get_subaccount_balances(self) -> Any:
        """GET /portfolio/subaccounts/balances."""
        self._require_auth()
        return await self.get("/portfolio/subaccounts/balances")

    async def list_subaccount_transfers(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /portfolio/subaccounts/transfers."""
        self._require_auth()
        return await self.get("/portfolio/subaccounts/transfers", params=params)

    async def get_subaccount_netting(self) -> Any:
        """GET /portfolio/subaccounts/netting."""
        self._require_auth()
        return await self.get("/portfolio/subaccounts/netting")

    # ---- RFQ batches (auth; OpenAPI v3.36.0) ---------------------------------
    async def list_rfq_batches(self, params: dict[str, Any] | None = None) -> Any:
        """GET /communications/rfq_batches — batches with their child RFQs."""
        self._require_auth()
        return await self.get("/communications/rfq_batches", params=params)

    async def get_rfq_batch(self, rfq_batch_id: str) -> Any:
        """GET /communications/rfq_batches/{rfq_batch_id}."""
        self._require_auth()
        quoted = urllib.parse.quote(rfq_batch_id, safe="")
        return await self.get(f"/communications/rfq_batches/{quoted}")

    async def get_rfq_batch_best_quotes(self, rfq_batch_id: str) -> Any:
        """GET /communications/rfq_batches/{rfq_batch_id}/best_quotes."""
        self._require_auth()
        quoted = urllib.parse.quote(rfq_batch_id, safe="")
        return await self.get(f"/communications/rfq_batches/{quoted}/best_quotes")

    async def get_rfq_batch_quotes(self, rfq_batch_id: str) -> Any:
        """GET /communications/rfq_batches/{rfq_batch_id}/quotes."""
        self._require_auth()
        quoted = urllib.parse.quote(rfq_batch_id, safe="")
        return await self.get(f"/communications/rfq_batches/{quoted}/quotes")

    # ---- Remaining OpenAPI v3.36.0 GETs ----------------------------------------
    async def get_event_contract_exit_triggers(
        self, ticker: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /portfolio/positions/{ticker}/exit_trigger."""
        self._require_auth()
        quoted = urllib.parse.quote(ticker, safe="")
        return await self.get(
            f"/portfolio/positions/{quoted}/exit_trigger", params=params
        )

    async def get_series_fee_changes(self, params: dict[str, Any] | None = None) -> Any:
        """GET /series/fee_changes."""
        return await self.get("/series/fee_changes", params=params)

    async def get_exchange_user_data_timestamp(self) -> Any:
        """GET /exchange/user_data_timestamp."""
        return await self.get("/exchange/user_data_timestamp")

    async def get_market_orderbooks(self, params: dict[str, Any]) -> Any:
        """GET /markets/orderbooks."""
        return await self.get("/markets/orderbooks", params=params)

    async def get_event_candlesticks(
        self, series_ticker: str, ticker: str, params: dict[str, Any]
    ) -> Any:
        """GET /series/{series_ticker}/events/{ticker}/candlesticks."""
        series = urllib.parse.quote(series_ticker, safe="")
        event = urllib.parse.quote(ticker, safe="")
        return await self.get(
            f"/series/{series}/events/{event}/candlesticks", params=params
        )

    async def list_multivariate_events(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /events/multivariate."""
        return await self.get("/events/multivariate", params=params)

    async def get_event_fee_changes(self, params: dict[str, Any] | None = None) -> Any:
        """GET /events/fee_changes."""
        return await self.get("/events/fee_changes", params=params)

    async def get_event_metadata(self, event_ticker: str) -> Any:
        """GET /events/{event_ticker}/metadata."""
        quoted = urllib.parse.quote(event_ticker, safe="")
        return await self.get(f"/events/{quoted}/metadata")

    async def get_event_forecast_percentile_history(
        self, series_ticker: str, ticker: str, params: dict[str, Any]
    ) -> Any:
        """GET /series/{series_ticker}/events/{ticker}/forecast_percentile_history."""
        self._require_auth()
        series = urllib.parse.quote(series_ticker, safe="")
        event = urllib.parse.quote(ticker, safe="")
        return await self.get(
            f"/series/{series}/events/{event}/forecast_percentile_history",
            params=params,
        )

    async def get_order_group(
        self, order_group_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /portfolio/order_groups/{order_group_id}."""
        self._require_auth()
        quoted = urllib.parse.quote(order_group_id, safe="")
        return await self.get(f"/portfolio/order_groups/{quoted}", params=params)

    async def list_intra_exchange_instance_transfers(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /portfolio/intra_exchange_instance_transfers."""
        self._require_auth()
        return await self.get(
            "/portfolio/intra_exchange_instance_transfers", params=params
        )

    async def get_intra_exchange_instance_transfer(self, transfer_id: str) -> Any:
        """GET /portfolio/intra_exchange_instance_transfers/{transfer_id}."""
        self._require_auth()
        quoted = urllib.parse.quote(transfer_id, safe="")
        return await self.get(f"/portfolio/intra_exchange_instance_transfers/{quoted}")

    async def get_credit_history(self, params: dict[str, Any] | None = None) -> Any:
        """GET /portfolio/credit_history."""
        self._require_auth()
        return await self.get("/portfolio/credit_history", params=params)

    async def get_communications_id(self) -> Any:
        """GET /communications/id."""
        self._require_auth()
        return await self.get("/communications/id")

    async def list_block_trade_proposals(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /communications/block-trade-proposals."""
        self._require_auth()
        return await self.get("/communications/block-trade-proposals", params=params)

    async def list_rfqs(self, params: dict[str, Any] | None = None) -> Any:
        """GET /communications/rfqs."""
        self._require_auth()
        return await self.get("/communications/rfqs", params=params)

    async def get_rfq(self, rfq_id: str) -> Any:
        """GET /communications/rfqs/{rfq_id}."""
        self._require_auth()
        quoted = urllib.parse.quote(rfq_id, safe="")
        return await self.get(f"/communications/rfqs/{quoted}")

    async def get_rfq_quote(self, rfq_id: str, quote_id: str) -> Any:
        """GET /communications/rfqs/{rfq_id}/quotes/{quote_id}."""
        self._require_auth()
        rfq = urllib.parse.quote(rfq_id, safe="")
        quote = urllib.parse.quote(quote_id, safe="")
        return await self.get(f"/communications/rfqs/{rfq}/quotes/{quote}")

    async def list_quotes(self, params: dict[str, Any] | None = None) -> Any:
        """GET /communications/quotes."""
        self._require_auth()
        return await self.get("/communications/quotes", params=params)

    async def get_quote(self, quote_id: str) -> Any:
        """GET /communications/quotes/{quote_id}. Deprecated in the spec."""
        self._require_auth()
        quoted = urllib.parse.quote(quote_id, safe="")
        return await self.get(f"/communications/quotes/{quoted}")

    async def list_api_keys(self, params: dict[str, Any] | None = None) -> Any:
        """GET /api_keys."""
        self._require_auth()
        return await self.get("/api_keys", params=params)

    async def get_account_api_usage_level_volume_progress(self) -> Any:
        """GET /account/api_usage_level/volume_progress."""
        self._require_auth()
        return await self.get("/account/api_usage_level/volume_progress")

    async def get_live_data_by_milestone(
        self, milestone_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /live_data/milestone/{milestone_id}."""
        quoted = urllib.parse.quote(milestone_id, safe="")
        return await self.get(f"/live_data/milestone/{quoted}", params=params)

    async def get_live_data(
        self, live_type: str, milestone_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /live_data/{type}/milestone/{milestone_id}."""
        kind = urllib.parse.quote(live_type, safe="")
        quoted = urllib.parse.quote(milestone_id, safe="")
        return await self.get(f"/live_data/{kind}/milestone/{quoted}", params=params)

    async def get_game_stats(self, milestone_id: str) -> Any:
        """GET /live_data/milestone/{milestone_id}/game_stats."""
        quoted = urllib.parse.quote(milestone_id, safe="")
        return await self.get(f"/live_data/milestone/{quoted}/game_stats")

    async def get_weather_index(
        self, city: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /live_data/weather/{city}."""
        quoted = urllib.parse.quote(city, safe="")
        return await self.get(f"/live_data/weather/{quoted}", params=params)

    async def get_weather_index_calibrations(self, city: str) -> Any:
        """GET /live_data/weather/{city}/calibrations."""
        quoted = urllib.parse.quote(city, safe="")
        return await self.get(f"/live_data/weather/{quoted}/calibrations")

    async def list_structured_targets(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /structured_targets."""
        return await self.get("/structured_targets", params=params)

    async def get_structured_target(self, structured_target_id: str) -> Any:
        """GET /structured_targets/{structured_target_id}."""
        quoted = urllib.parse.quote(structured_target_id, safe="")
        return await self.get(f"/structured_targets/{quoted}")

    async def get_weather_point_availability(self) -> Any:
        """GET /weather_point_markets/availability."""
        return await self.get("/weather_point_markets/availability")

    async def get_concert_cancellation_availability(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /concert_cancellation_markets/availability."""
        return await self.get(
            "/concert_cancellation_markets/availability", params=params
        )

    async def list_concerts(self, params: dict[str, Any]) -> Any:
        """GET /concert_cancellation_markets/concerts."""
        self._require_auth()
        return await self.get("/concert_cancellation_markets/concerts", params=params)

    async def search_concerts(self, params: dict[str, Any]) -> Any:
        """GET /concert_cancellation_markets/concerts/search."""
        self._require_auth()
        return await self.get(
            "/concert_cancellation_markets/concerts/search", params=params
        )

    async def list_incentive_programs(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /incentive_programs."""
        return await self.get("/incentive_programs", params=params)

    async def get_incentive_schedules(self) -> Any:
        """GET /incentive_schedules."""
        return await self.get("/incentive_schedules")

    async def get_fcm_fills(self, params: dict[str, Any] | None = None) -> Any:
        """GET /fcm/fills. FCM members only."""
        self._require_auth()
        return await self.get("/fcm/fills", params=params)

    async def get_fcm_orders(self, params: dict[str, Any] | None = None) -> Any:
        """GET /fcm/orders. FCM members only."""
        self._require_auth()
        return await self.get("/fcm/orders", params=params)

    async def list_fcm_subtraders(self) -> Any:
        """GET /fcm/subtraders. FCM members only."""
        self._require_auth()
        return await self.get("/fcm/subtraders")

    async def get_fcm_event_contract_daily_cap(
        self, params: dict[str, Any] | None = None
    ) -> Any:
        """GET /fcm/subtraders/event_contract_daily_cap. FCM members only."""
        self._require_auth()
        return await self.get("/fcm/subtraders/event_contract_daily_cap", params=params)

    async def get_fcm_subtrader_blocked_categories(self, subtrader_id: str) -> Any:
        """GET /fcm/subtraders/blocked_categories. FCM members only."""
        self._require_auth()
        return await self.get(
            "/fcm/subtraders/blocked_categories",
            params={"subtrader_id": subtrader_id},
        )

    async def get_target_balance_allocation(self) -> Any:
        """GET /portfolio/target_balance_allocation."""
        self._require_auth()
        return await self.get("/portfolio/target_balance_allocation")

    async def get_fcm_positions(self, params: dict[str, Any] | None = None) -> Any:
        """GET /fcm/positions. FCM members only."""
        self._require_auth()
        return await self.get("/fcm/positions", params=params)
