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
    return payload


def build_amend_order_payload(
    *,
    ticker: str,
    action: str,
    side: str,
    count: float,
    limit_price_cents: int,
    updated_client_order_id: str | None = None,
) -> dict[str, Any]:
    """Build the Kalshi V2 amend body, using the same YES-leg translation as create."""
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
    return payload


def build_decrease_order_payload(
    reduce_by: float | None = None, reduce_to: float | None = None
) -> dict[str, Any]:
    """Build the Kalshi V2 decrease body. Exactly one of reduce_by / reduce_to required."""
    if (reduce_by is None) == (reduce_to is None):
        raise ValueError("Provide exactly one of reduce_by or reduce_to.")
    if reduce_by is not None:
        return {
            "reduce_by": (
                str(reduce_by) if reduce_by != int(reduce_by) else str(int(reduce_by))
            )
        }
    assert reduce_to is not None  # guaranteed by the exactly-one check above
    return {
        "reduce_to": (
            str(reduce_to) if reduce_to != int(reduce_to) else str(int(reduce_to))
        )
    }


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
        self, event_ticker: str, with_nested_markets: bool | None = None
    ) -> Any:
        params = (
            {"with_nested_markets": with_nested_markets}
            if with_nested_markets is not None
            else None
        )
        quoted = urllib.parse.quote(event_ticker, safe="")
        return await self.get(f"/events/{quoted}", params=params)

    # ---- Series -------------------------------------------------------------------
    async def get_series_list(self, params: dict[str, Any] | None = None) -> Any:
        return await self.get("/series", params=params)

    async def get_series(self, series_ticker: str) -> Any:
        quoted = urllib.parse.quote(series_ticker, safe="")
        return await self.get(f"/series/{quoted}")

    # ---- Portfolio (auth) ---------------------------------------------------------
    async def get_balance(self) -> Any:
        self._require_auth()
        return await self.get("/portfolio/balance")

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

    async def cancel_order(self, order_id: str) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.delete(f"/portfolio/events/orders/{quoted}")

    async def amend_order(self, order_id: str, payload: dict[str, Any]) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.post(f"/portfolio/events/orders/{quoted}/amend", json=payload)

    async def decrease_order(self, order_id: str, payload: dict[str, Any]) -> Any:
        self._require_auth()
        quoted = urllib.parse.quote(order_id, safe="")
        return await self.post(
            f"/portfolio/events/orders/{quoted}/decrease", json=payload
        )

    # ---- Batch Orders (auth) ------------------------------------------------------
    async def batch_create_orders(self, orders_payload: list[dict[str, Any]]) -> Any:
        """Batch create up to 20 orders in one atomic call."""
        self._require_auth()
        return await self.post(
            "/portfolio/events/orders/batched", json={"orders": orders_payload}
        )

    async def batch_cancel_orders(self, order_ids: list[str]) -> Any:
        """Batch cancel up to 20 orders in one atomic call."""
        self._require_auth()
        items = [{"order_id": oid} for oid in order_ids]
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

    async def get_event_live_data(self, event_ticker: str) -> Any:
        """Get real-time live scoreboard/data for an event."""
        quoted = urllib.parse.quote(event_ticker, safe="")
        return await self.get(f"/live_data/events/{quoted}")

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

    # ---- Portfolio Summary & Order Groups (auth) ---------------------------------
    async def get_portfolio_summary(self) -> Any:
        """Get total resting order value and exposure."""
        self._require_auth()
        return await self.get("/portfolio/summary/total_resting_order_value")

    async def list_order_groups(self, params: dict[str, Any] | None = None) -> Any:
        """List active order groups."""
        self._require_auth()
        return await self.get("/portfolio/order_groups", params=params)

    async def cancel_order_group(self, order_group_id: str) -> Any:
        """Cancel/delete an active order group."""
        self._require_auth()
        quoted = urllib.parse.quote(order_group_id, safe="")
        return await self.delete(f"/portfolio/order_groups/{quoted}")
