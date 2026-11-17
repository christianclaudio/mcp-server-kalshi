from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class MCPSchemaBaseModel(BaseModel):
    @classmethod
    def to_mcp_input_schema(cls) -> dict[str, Any]:
        """Convert this model into a clean MCP Tool inputSchema.

        Flattens ``Optional`` fields (drops the ``anyOf``/null noise pydantic emits) and
        inlines enum ``$ref``s so MCP clients render simple, readable parameter schemas.
        """
        schema = cls.model_json_schema()
        properties: dict[str, Any] = {}
        for name, prop in schema.get("properties", {}).items():
            clean_prop: dict[str, Any] = dict(prop)
            if "anyOf" in clean_prop:
                non_null = [v for v in clean_prop["anyOf"] if v.get("type") != "null"]
                if len(non_null) == 1:
                    clean_prop = dict(non_null[0])
                    if "description" in prop:
                        clean_prop["description"] = prop["description"]

            if "$ref" in clean_prop:
                ref_key = clean_prop["$ref"].split("/")[-1]
                enum_def = schema.get("$defs", {}).get(ref_key, {})
                clean_prop = {
                    "type": enum_def.get("type", "string"),
                    "enum": enum_def.get("enum", []),
                    "description": prop.get("description"),
                }

            clean_prop.pop("title", None)
            if clean_prop.get("description") is None:
                clean_prop.pop("description", None)
            properties[name] = clean_prop

        return {
            "type": "object",
            "properties": properties,
            "required": schema.get("required", []),
            "additionalProperties": False,
        }


def _page_limit(maximum: int, *, minimum: int = 1) -> Any:
    """Optional page size bounded by the endpoint's own OpenAPI v3.36.0 limits."""
    return Field(
        default=None,
        ge=minimum,
        le=maximum,
        description=f"Results per page ({minimum}-{maximum}).",
    )


# Shared pagination fields. Each subclass sets ``limit`` to its endpoint's spec bounds;
# the default here (1-1000) is the bound of the portfolio list endpoints.
class _Paginated(MCPSchemaBaseModel):
    limit: int | None = _page_limit(1000)
    cursor: str | None = Field(
        default=None, description="Pagination cursor for the next page."
    )


# Shared optional portfolio scoping fields (Kalshi OpenAPI v3.36.0). Omitted unless set,
# so requests that do not pass them are unchanged.
_SUBACCOUNT_ALL = (
    "Subaccount number (0 for primary, 1-63 for subaccounts). Omit for all subaccounts."
)
_SUBACCOUNT_PRIMARY = (
    "Subaccount number (0 for primary, 1-63 for subaccounts). Omit for Kalshi's default "
    "(0, or the locked subaccount of a subaccount-restricted API key)."
)
_EXCHANGE_INDEX_FILTER = (
    "Filter by exchange shard index. Omit to include all exchange shards."
)
_EXCHANGE_INDEX_ROUTE = (
    "Exchange shard index. Omit to let Kalshi auto-route by market ticker (else 0); "
    "-1 requires auto-routing."
)


# ---- Discovery ---------------------------------------------------------------------
class ListMarketsRequest(_Paginated):
    """Browse/search markets. Kalshi has no free-text search endpoint; filter with these."""

    event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    series_ticker: str | None = Field(
        default=None, description="Filter by series ticker."
    )
    limit: int | None = _page_limit(1000, minimum=0)
    status: Literal["unopened", "open", "paused", "closed", "settled"] | None = Field(
        default=None, description="Filter by market status."
    )
    tickers: str | None = Field(
        default=None, description="Comma-separated list of specific market tickers."
    )
    min_close_ts: int | None = Field(
        default=None, description="Only markets closing on/after this Unix timestamp."
    )
    max_close_ts: int | None = Field(
        default=None, description="Only markets closing on/before this Unix timestamp."
    )
    min_created_ts: int | None = Field(
        default=None, description="Only markets created after this Unix timestamp."
    )
    max_created_ts: int | None = Field(
        default=None, description="Only markets created before this Unix timestamp."
    )
    min_updated_ts: int | None = Field(
        default=None,
        description="Only markets whose metadata changed after this Unix timestamp.",
    )
    max_updated_ts: int | None = Field(
        default=None,
        description="Only markets whose metadata changed at/before this Unix timestamp.",
    )
    min_settled_ts: int | None = Field(
        default=None, description="Only markets settled after this Unix timestamp."
    )
    max_settled_ts: int | None = Field(
        default=None, description="Only markets settled before this Unix timestamp."
    )
    mve_filter: Literal["only", "exclude"] | None = Field(
        default=None,
        description="'only' returns only multivariate (combo) markets; 'exclude' omits them.",
    )


class GetMarketRequest(MCPSchemaBaseModel):
    ticker: str = Field(..., description="The market ticker, e.g. 'KXELONMARS-99'.")


class ListEventsRequest(_Paginated):
    limit: int | None = _page_limit(200)
    series_ticker: str | None = Field(
        default=None, description="Filter by series ticker."
    )
    status: str | None = Field(default=None, description="Filter by event status.")
    with_nested_markets: bool | None = Field(
        default=None, description="Include each event's markets inline."
    )
    with_milestones: bool | None = Field(
        default=None, description="Include related milestones alongside the events."
    )
    tickers: str | None = Field(
        default=None, description="Comma-separated list of specific event tickers."
    )
    min_close_ts: int | None = Field(
        default=None,
        description="Only events with a market closing after this Unix timestamp.",
    )
    min_updated_ts: int | None = Field(
        default=None,
        description="Only events whose metadata changed after this Unix timestamp.",
    )


class GetEventRequest(MCPSchemaBaseModel):
    event_ticker: str = Field(..., description="The event ticker to retrieve.")
    with_nested_markets: bool | None = Field(
        default=None, description="Include the event's markets inline."
    )
    exclude_markets: bool | None = Field(
        default=None,
        description=(
            "Return event metadata only, without child markets (page them with "
            "list_markets event_ticker). Cannot be combined with with_nested_markets=true."
        ),
    )

    @model_validator(mode="after")
    def _exclusive_market_flags(self) -> "GetEventRequest":
        # Kalshi OpenAPI v3.36.0, GET /events/{event_ticker}: exclude_markets "Cannot be
        # combined with with_nested_markets=true".
        if self.exclude_markets and self.with_nested_markets:
            raise ValueError(
                "exclude_markets=true cannot be combined with with_nested_markets=true."
            )
        return self


class ListSeriesRequest(MCPSchemaBaseModel):
    category: str | None = Field(default=None, description="Filter series by category.")
    tags: str | None = Field(default=None, description="Comma-separated tag filter.")
    include_volume: bool | None = Field(
        default=None, description="Include each series' total traded volume."
    )
    include_product_metadata: bool | None = Field(
        default=None, description="Include each series' product metadata."
    )
    min_updated_ts: int | None = Field(
        default=None,
        description="Only series whose metadata changed after this Unix timestamp.",
    )


class GetSeriesRequest(MCPSchemaBaseModel):
    series_ticker: str = Field(
        ..., description="The series ticker, e.g. 'KXELONMARS'. Holds rules PDFs."
    )
    include_volume: bool | None = Field(
        default=None, description="Include the series' total traded volume."
    )


# ---- Research / rules --------------------------------------------------------------
class GetMarketOrderbookRequest(MCPSchemaBaseModel):
    ticker: str = Field(..., description="The market ticker.")
    depth: int | None = Field(
        default=None, description="Price levels to return (1-100; omit for all)."
    )


class GetMarketCandlesticksRequest(MCPSchemaBaseModel):
    ticker: str = Field(..., description="The market ticker.")
    period_interval: Literal[1, 60, 1440] = Field(
        default=60, description="Candle size in minutes: 1, 60, or 1440 (1 day)."
    )
    lookback_hours: int | None = Field(
        default=24,
        description="Convenience window ending now. Ignored if start_ts/end_ts are set.",
    )
    start_ts: int | None = Field(
        default=None, description="Explicit start Unix timestamp."
    )
    end_ts: int | None = Field(default=None, description="Explicit end Unix timestamp.")
    series_ticker: str | None = Field(
        default=None,
        description="Override the series ticker (else derived from the market ticker).",
    )
    include_latest_before_start: bool | None = Field(
        default=None,
        description="Prepend the latest candlestick available before start_ts.",
    )


class GetMarketTradesRequest(_Paginated):
    limit: int | None = Field(
        default=None,
        ge=0,
        le=1000,
        description=(
            "Results per page (0-1000). The spec sets a minimum of 0 and a default "
            "of 100."
        ),
    )
    ticker: str = Field(..., description="The market ticker.")
    min_ts: int | None = Field(
        default=None, description="Only trades after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only trades before this Unix ts."
    )
    is_block_trade: bool | None = Field(
        default=None,
        description="true: only block trades; false: only non-block trades; omit for all.",
    )


class GetMarketRulesRequest(MCPSchemaBaseModel):
    """Consolidated settlement rules for a market (market + parent series)."""

    ticker: str = Field(..., description="The market ticker to explain.")


class FetchRulesPdfRequest(MCPSchemaBaseModel):
    """Download and extract the text of a market's rules PDF."""

    series_ticker: str | None = Field(
        default=None, description="Series ticker whose contract PDF to fetch."
    )
    ticker: str | None = Field(
        default=None, description="Market ticker (its series' PDF is fetched)."
    )
    url: str | None = Field(
        default=None, description="Direct PDF URL (overrides tickers)."
    )
    document: Literal["contract_terms", "certification"] = Field(
        default="contract_terms",
        description="Which series PDF: 'contract_terms' (contract_terms_url) or 'certification' (contract_url).",
    )


# ---- Exchange ----------------------------------------------------------------------
class EmptyRequest(MCPSchemaBaseModel):
    pass


# ---- Portfolio ---------------------------------------------------------------------
class GetBalanceRequest(MCPSchemaBaseModel):
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None,
        description="Scope balance and portfolio value to one exchange shard index. Omit for all.",
    )


class GetPositionsRequest(_Paginated):
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    count_filter: str | None = Field(
        default=None,
        description="Restrict to positions with non-zero fields (e.g. 'position').",
    )
    settlement_status: Literal["unsettled", "settled", "all"] | None = Field(
        default=None,
        description=(
            "Kalshi defaults to 'unsettled'. 'settled' returns settled positions not yet "
            "archived to historical; 'all' returns both."
        ),
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None, ge=0, description=_EXCHANGE_INDEX_FILTER
    )


class GetFillsRequest(_Paginated):
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    order_id: str | None = Field(default=None, description="Filter by order id.")
    min_ts: int | None = Field(
        default=None, description="Only fills after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only fills before this Unix ts."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)
    exchange_index: int | None = Field(
        default=None, ge=0, description=_EXCHANGE_INDEX_FILTER
    )


class GetSettlementsRequest(_Paginated):
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    min_ts: int | None = Field(default=None, description="Only after this Unix ts.")
    max_ts: int | None = Field(default=None, description="Only before this Unix ts.")
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)


# ---- Orders ------------------------------------------------------------------------
class ListOrdersRequest(_Paginated):
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    status: Literal["resting", "canceled", "executed"] | None = Field(
        default=None, description="Filter by order status."
    )
    min_ts: int | None = Field(
        default=None, description="Only orders after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only orders before this Unix ts."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)
    exchange_index: int | None = Field(
        default=None, ge=0, description=_EXCHANGE_INDEX_FILTER
    )


class GetOrderRequest(MCPSchemaBaseModel):
    order_id: str = Field(..., description="The order id.")


class CreateOrderRequest(MCPSchemaBaseModel):
    """Place a limit order using the intuitive buy/sell + yes/no model.

    Prices are whole **cents** (1-99) for the outcome you name in ``side``; the server
    translates to Kalshi's YES-leg book side and fixed-point dollar price. Requires
    ``confirm=true`` to actually place — otherwise a preview is returned and nothing is sent.
    """

    ticker: str = Field(..., description="The market ticker to trade.")
    action: Literal["buy", "sell"] = Field(
        ..., description="Buy (open/add) or sell (close/reduce)."
    )
    side: Literal["yes", "no"] = Field(..., description="Which outcome: 'yes' or 'no'.")
    count: float = Field(..., gt=0, description="Number of contracts.")
    limit_price: int = Field(
        ...,
        ge=1,
        le=99,
        description="Limit price in cents (1-99) for the chosen outcome.",
    )
    time_in_force: Literal[
        "good_till_canceled", "immediate_or_cancel", "fill_or_kill"
    ] = Field(default="good_till_canceled", description="How long the order rests.")
    post_only: bool = Field(
        default=False, description="Reject if it would immediately match."
    )
    reduce_only: bool = Field(
        default=False,
        description="Cap size by current position (never flips direction).",
    )
    expiration_ts: int | None = Field(
        default=None,
        description="Optional Unix-seconds expiry (with good_till_canceled).",
    )
    client_order_id: str | None = Field(
        default=None, description="Optional idempotency id; auto-generated if omitted."
    )
    order_group_id: str | None = Field(
        default=None, description="Optional order group this order belongs to."
    )
    cancel_order_on_pause: bool | None = Field(
        default=None,
        description="If true, Kalshi cancels the open order when exchange trading pauses.",
    )
    subaccount: int | None = Field(
        default=None,
        ge=0,
        description="Subaccount to place under (0 primary, 1-63). Omit for Kalshi's default.",
    )
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to actually place the order. False returns a preview only.",
    )


class CancelOrderRequest(MCPSchemaBaseModel):
    order_id: str = Field(..., description="The order id to cancel.")
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )
    market_ticker: str | None = Field(
        default=None,
        description="Market ticker of the order; used to auto-route the cancel to its shard.",
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to cancel the order. False returns a preview only.",
    )


class AmendOrderRequest(MCPSchemaBaseModel):
    """Amend an existing order's price and/or total count (intuitive model + confirm)."""

    order_id: str = Field(..., description="The order id to amend.")
    ticker: str = Field(..., description="The market ticker of the order.")
    action: Literal["buy", "sell"] = Field(..., description="Original order direction.")
    side: Literal["yes", "no"] = Field(..., description="Original outcome side.")
    count: float = Field(..., gt=0, description="New total/max fillable count.")
    limit_price: int = Field(
        ..., ge=1, le=99, description="New limit price in cents (1-99)."
    )
    updated_client_order_id: str | None = Field(
        default=None, description="Optional new client order id."
    )
    client_order_id: str | None = Field(
        default=None, description="Optional original client order id of the order."
    )
    expiration_time: int | None = Field(
        default=None,
        ge=0,
        description=(
            "New Unix-seconds expiry. Omit to keep the current expiry; 0 removes it. "
            "For an expiry-only amend, send the current price and total count."
        ),
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to apply. False returns a preview only.",
    )


class DecreaseOrderRequest(MCPSchemaBaseModel):
    """Decrease a resting order's remaining count. Provide exactly one of reduce_by/reduce_to."""

    order_id: str = Field(..., description="The order id to decrease.")
    reduce_by: float | None = Field(default=None, description="Contracts to remove.")
    reduce_to: float | None = Field(
        default=None, description="Target remaining contracts."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )
    market_ticker: str | None = Field(
        default=None,
        description="Market ticker of the order; used to auto-route to its shard.",
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to decrease the order. False returns a preview only.",
    )


# ========================== Batch Orders & Groups ============================
class BatchOrderItem(BaseModel):
    """A single order instruction within a batch order request."""

    ticker: str = Field(..., description="The market ticker to trade.")
    action: Literal["buy", "sell"] = Field(..., description="Direction: buy or sell.")
    side: Literal["yes", "no"] = Field(..., description="Outcome: yes or no.")
    count: float = Field(..., gt=0, description="Contracts to trade.")
    limit_price: int = Field(
        ..., ge=1, le=99, description="Limit price in cents (1-99)."
    )
    time_in_force: Literal[
        "good_till_canceled", "immediate_or_cancel", "fill_or_kill"
    ] = Field(default="good_till_canceled", description="How long the order rests.")
    post_only: bool = Field(
        default=False, description="Reject if it would immediately match."
    )
    reduce_only: bool = Field(
        default=False,
        description="Cap size by current position (never flips direction).",
    )
    expiration_ts: int | None = Field(
        default=None,
        description="Optional Unix-seconds expiry (with good_till_canceled).",
    )
    client_order_id: str | None = Field(
        default=None, description="Optional idempotency id."
    )
    order_group_id: str | None = Field(
        default=None, description="Optional order group this order belongs to."
    )
    cancel_order_on_pause: bool | None = Field(
        default=None,
        description="If true, Kalshi cancels the open order when exchange trading pauses.",
    )
    subaccount: int | None = Field(
        default=None,
        ge=0,
        description="Subaccount to place under (0 primary, 1-63). Omit for Kalshi's default.",
    )
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )


class BatchCreateOrdersRequest(MCPSchemaBaseModel):
    """Batch order placement request (V2). Kalshi scales the max batch size with the account's API tier."""

    orders: list[BatchOrderItem] = Field(
        ..., min_length=1, description="List of orders to submit."
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to place the batch. False returns a simulation preview.",
    )


class BatchCancelOrderItem(BaseModel):
    """One order to cancel in a batch, with optional subaccount and shard routing."""

    order_id: str = Field(..., description="The order id to cancel.")
    subaccount: int | None = Field(
        default=None, ge=0, description="Subaccount of the order (0 primary, 1-63)."
    )
    exchange_index: int | None = Field(
        default=None, ge=-1, description=_EXCHANGE_INDEX_ROUTE
    )
    market_ticker: str | None = Field(
        default=None,
        description="Market ticker of the order; used to auto-route to its shard.",
    )


class BatchCancelOrdersRequest(MCPSchemaBaseModel):
    """Batch order cancellation request (V2). Kalshi scales the max batch size with the account's API tier.

    Pass ``order_ids`` (plain ids) or ``orders`` (ids with subaccount/shard routing), not both.
    """

    order_ids: list[str] | None = Field(
        default=None, min_length=1, description="List of order IDs to cancel."
    )
    orders: list[BatchCancelOrderItem] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Orders to cancel, each with optional subaccount, exchange_index and "
            "market_ticker. Use instead of order_ids."
        ),
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to cancel the batch. False returns a preview only.",
    )

    @model_validator(mode="after")
    def _one_of_ids_or_orders(self) -> "BatchCancelOrdersRequest":
        if (self.order_ids is None) == (self.orders is None):
            raise ValueError("Provide exactly one of order_ids or orders.")
        return self


class GetTotalRestingOrderValueRequest(EmptyRequest):
    """Request the total resting order value (FCM members only)."""


class GetTagsByCategoriesRequest(EmptyRequest):
    """Request category-level discovery tags."""


class GetSportsFiltersRequest(EmptyRequest):
    """Request sport-level search filters."""


class GetMilestonesRequest(MCPSchemaBaseModel):
    """Request milestone trackers (e.g. sports games, elections)."""

    limit: int = Field(
        default=20, ge=1, le=500, description="Number of milestones (1-500)."
    )
    category: str | None = Field(default=None, description="Filter by category.")
    competition: str | None = Field(default=None, description="Filter by competition.")
    type: str | None = Field(default=None, description="Filter by milestone type.")
    related_event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    cursor: str | None = Field(default=None, description="Pagination cursor.")
    minimum_start_date: str | None = Field(
        default=None,
        description="Only milestones starting at/after this RFC 3339 timestamp.",
    )
    source_id: str | None = Field(default=None, description="Filter by source id.")
    min_updated_ts: int | None = Field(
        default=None,
        description="Only milestones whose metadata changed after this Unix timestamp.",
    )


class GetMilestoneRequest(MCPSchemaBaseModel):
    """Request a specific milestone by ID."""

    milestone_id: str = Field(..., description="The unique milestone ID.")


class GetEventLiveDataRequest(MCPSchemaBaseModel):
    """Request real-time live data/scoreboard for an event."""

    event_ticker: str = Field(
        ...,
        description="The event ticker (e.g. KXNBA-27, KXMLB-...) to fetch live data for.",
    )
    range: str | None = Field(
        default=None,
        description=(
            "Optional chart range hint (e.g. '15min', '1h', '1d'); restricts the timeseries "
            "where the live data type supports it."
        ),
    )


class ListMultivariateCollectionsRequest(MCPSchemaBaseModel):
    """List multivariate / combo event collections."""

    status: str | None = Field(
        default=None, description="Filter by status (e.g. open)."
    )
    series_ticker: str | None = Field(
        default=None, description="Filter by parent series ticker."
    )
    associated_event_ticker: str | None = Field(
        default=None, description="Only collections associated with this event ticker."
    )
    limit: int | None = Field(
        default=20, ge=1, le=200, description="Page limit (1-200)."
    )
    cursor: str | None = Field(default=None, description="Pagination cursor.")


class GetMultivariateCollectionRequest(MCPSchemaBaseModel):
    """Get details for a specific multivariate / combo collection."""

    collection_ticker: str = Field(
        ..., description="The collection ticker (e.g. KXCOMBO-...) to fetch."
    )


class ListOrderGroupsRequest(MCPSchemaBaseModel):
    """List order groups: groups with a contracts limit over a rolling 15-second window that cancel all their orders when the limit is hit.

    ``limit`` and ``cursor`` predate Kalshi OpenAPI v3.36.0, which documents only
    ``subaccount`` on GET /portfolio/order_groups. They are kept for compatibility and
    still sent as before.
    """

    limit: int | None = Field(
        default=20,
        ge=1,
        le=100,
        description="Page limit (1-100). Not in Kalshi's OpenAPI spec; kept for compatibility.",
    )
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor. Not in Kalshi's OpenAPI spec; kept for compatibility.",
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)


class CancelOrderGroupRequest(MCPSchemaBaseModel):
    """Delete an order group, which cancels all orders in it."""

    order_group_id: str = Field(..., description="The order group ID to cancel.")
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)
    exchange_index: int | None = Field(
        default=None,
        description="Exchange shard index of the order group. Omit for Kalshi's default (0).",
    )
    confirm: StrictBool = Field(
        default=False,
        description="Must be true to cancel the order group. False returns a preview only.",
    )


# ---- Historical (OpenAPI v3.36.0) ----------------------------------------------
class GetHistoricalCutoffRequest(EmptyRequest):
    """GET /historical/cutoff — archive cutoff timestamps."""


class ListHistoricalMarketsRequest(_Paginated):
    """GET /historical/markets — archived markets (filters mutually exclusive)."""

    limit: int | None = _page_limit(1000, minimum=0)
    tickers: str | None = Field(
        default=None,
        description="Comma-separated market tickers to retrieve.",
    )
    event_ticker: str | None = Field(
        default=None, description="Filter by a single event ticker."
    )
    series_ticker: str | None = Field(
        default=None, description="Filter by series ticker."
    )
    mve_filter: Literal["exclude"] | None = Field(
        default=None,
        description="Set to exclude to omit multivariate (combo) markets.",
    )


class GetHistoricalMarketRequest(MCPSchemaBaseModel):
    ticker: str = Field(..., description="Market ticker in the historical archive.")


class GetHistoricalMarketCandlesticksRequest(MCPSchemaBaseModel):
    ticker: str = Field(..., description="Historical market ticker.")
    start_ts: int = Field(
        ..., description="Start Unix timestamp (candles ending on/after)."
    )
    end_ts: int = Field(
        ..., description="End Unix timestamp (candles ending on/before)."
    )
    period_interval: Literal[1, 60, 1440] = Field(
        ...,
        description="Candle length in minutes: 1, 60, or 1440.",
    )


class GetHistoricalTradesRequest(_Paginated):
    """GET /historical/trades — public trades older than the cutoff."""

    limit: int | None = _page_limit(1000, minimum=0)
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    min_ts: int | None = Field(
        default=None, description="Only trades after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only trades before this Unix ts."
    )
    is_block_trade: bool | None = Field(
        default=None,
        description="true=only block trades; false=only non-block; omit=all.",
    )


class GetHistoricalFillsRequest(_Paginated):
    """GET /historical/fills — authenticated fills older than the cutoff."""

    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    min_ts: int | None = Field(
        default=None, description="Only fills after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only fills before this Unix ts."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)


class ListHistoricalOrdersRequest(_Paginated):
    """GET /historical/orders — archived canceled/executed orders."""

    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    min_ts: int | None = Field(
        default=None, description="Only orders after this Unix ts."
    )
    max_ts: int | None = Field(
        default=None, description="Only orders before this Unix ts."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)


class GetHistoricalPositionsRequest(_Paginated):
    """GET /historical/positions — settled positions after live-to-historical handoff."""

    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    event_ticker: str | None = Field(
        default=None, description="Filter by a single event ticker."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)


# ---- Batch market / live data (OpenAPI v3.36.0) --------------------------------
class BatchGetMarketCandlesticksRequest(MCPSchemaBaseModel):
    """GET /markets/candlesticks — candlesticks for up to 100 market tickers."""

    market_tickers: str = Field(
        ...,
        description="Comma-separated market tickers (maximum 100).",
    )
    start_ts: int = Field(..., description="Start timestamp in Unix seconds.")
    end_ts: int = Field(..., description="End timestamp in Unix seconds.")
    period_interval: int = Field(
        ...,
        ge=1,
        description="Candlestick period interval in minutes.",
    )
    include_latest_before_start: bool | None = Field(
        default=None,
        description="If true, prepend a synthetic candle before start_ts.",
    )


class GetLiveDataBatchRequest(MCPSchemaBaseModel):
    """GET /live_data/batch — live data for multiple milestones."""

    milestone_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Milestone IDs to fetch (1-100).",
    )
    include_player_stats: bool | None = Field(
        default=None,
        description="When true, include player-level stats where supported.",
    )


# ---- Account API limits / costs (OpenAPI v3.36.0) ------------------------------
class GetAccountLimitsRequest(EmptyRequest):
    """GET /account/limits — Predictions API tier and token-bucket limits."""


class GetAccountEndpointCostsRequest(EmptyRequest):
    """GET /account/endpoint_costs — non-default endpoint token costs."""


# ---- Queue position, deposits, withdrawals, subaccounts ------------------------
class GetOrderQueuePositionsRequest(MCPSchemaBaseModel):
    """GET /portfolio/orders/queue_positions — queue depth for resting orders."""

    market_tickers: str | None = Field(
        default=None,
        description="Comma-separated market tickers to filter by.",
    )
    event_ticker: str | None = Field(
        default=None, description="Event ticker to filter by."
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_PRIMARY)


class GetOrderQueuePositionRequest(MCPSchemaBaseModel):
    """GET /portfolio/orders/{order_id}/queue_position."""

    order_id: str = Field(..., description="The resting order id.")


class ListDepositsRequest(_Paginated):
    """GET /portfolio/deposits — deposit history."""

    limit: int | None = _page_limit(500)


class ListWithdrawalsRequest(_Paginated):
    """GET /portfolio/withdrawals — withdrawal history."""

    limit: int | None = _page_limit(500)


class GetSubaccountBalancesRequest(EmptyRequest):
    """GET /portfolio/subaccounts/balances — balances for all subaccounts."""


class ListSubaccountTransfersRequest(_Paginated):
    """GET /portfolio/subaccounts/transfers — transfers between subaccounts."""


class GetSubaccountNettingRequest(EmptyRequest):
    """GET /portfolio/subaccounts/netting — netting settings for subaccounts."""


# ---- RFQ batches (OpenAPI v3.36.0, communications) ---------------------------
class ListRFQBatchesRequest(_Paginated):
    """GET /communications/rfq_batches — RFQ batches, most recently updated first."""

    limit: int | None = _page_limit(20)
    status: Literal["open", "accepted", "closed"] | None = Field(
        default=None,
        description=(
            "Filter RFQ batches by status. open takes batch quotes; accepted is "
            "waiting for winning makers to confirm or is executing; closed is finished."
        ),
    )
    user_filter: Literal["self"] | None = Field(
        default=None,
        description=(
            "Omit to return all results. Use self to filter by the authenticated user."
        ),
    )
    subaccount: int | None = Field(default=None, ge=0, description=_SUBACCOUNT_ALL)


class GetRFQBatchRequest(MCPSchemaBaseModel):
    """GET /communications/rfq_batches/{rfq_batch_id}."""

    rfq_batch_id: str = Field(
        ...,
        description=(
            "RFQ batch UUID returned when the batch was created. Pass it unchanged; "
            "malformed IDs return HTTP 400."
        ),
    )


class GetRFQBatchBestQuotesRequest(MCPSchemaBaseModel):
    """GET /communications/rfq_batches/{rfq_batch_id}/best_quotes."""

    rfq_batch_id: str = Field(
        ...,
        description=(
            "RFQ batch UUID returned when the batch was created. Pass it unchanged; "
            "malformed IDs return HTTP 400."
        ),
    )


class GetRFQBatchQuotesRequest(MCPSchemaBaseModel):
    """GET /communications/rfq_batches/{rfq_batch_id}/quotes."""

    rfq_batch_id: str = Field(
        ...,
        description=(
            "RFQ batch UUID returned when the batch was created. Pass it unchanged; "
            "malformed IDs return HTTP 400."
        ),
    )


# ---- Remaining OpenAPI v3.36.0 GETs --------------------------------------------
# Parameter names, required flags, enums, and bounds are the spec's. Optional
# fields are omitted on the wire unless set; a spec default is what Kalshi applies
# when the parameter is absent.


class GetEventContractExitTriggersRequest(MCPSchemaBaseModel):
    """GET /portfolio/positions/{ticker}/exit_trigger.

    Hidden from the published docs (``x-excluded``) until event-contract
    take-profit / stop-loss launches.
    """

    ticker: str = Field(..., description="Market ticker of the position.")
    subaccount: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Subaccount number (0 for primary, 1-63 for subaccounts). Defaults to 0. "
            "Subaccount-restricted API keys default to their locked subaccount and "
            "must omit this parameter or pass their locked subaccount."
        ),
    )
    kind: Literal["bracket"] | None = Field(
        default=None,
        description="Exit-trigger kind. The spec's only value is bracket.",
    )


class GetSeriesFeeChangesRequest(MCPSchemaBaseModel):
    """GET /series/fee_changes."""

    series_ticker: str | None = Field(
        default=None, description="Filter by series ticker."
    )
    show_historical: bool | None = Field(
        default=None,
        description="Include historical fee changes. Defaults to false.",
    )


class GetExchangeUserDataTimestampRequest(EmptyRequest):
    """GET /exchange/user_data_timestamp."""


class GetMarketOrderbooksRequest(MCPSchemaBaseModel):
    """GET /markets/orderbooks. ``tickers`` is form-style and exploded."""

    tickers: list[Annotated[str, Field(max_length=200)]] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of market tickers to fetch orderbooks for.",
    )


class GetEventCandlesticksRequest(MCPSchemaBaseModel):
    """GET /series/{series_ticker}/events/{ticker}/candlesticks."""

    series_ticker: str = Field(..., description="The series ticker.")
    ticker: str = Field(..., description="The event ticker.")
    start_ts: int = Field(..., description="Start timestamp for the range.")
    end_ts: int = Field(..., description="End timestamp for the range.")
    period_interval: Literal[1, 60, 1440] = Field(
        ...,
        description=(
            "Length of each candlestick period, in minutes. "
            "Must be one minute, one hour, or one day."
        ),
    )


class ListMultivariateEventsRequest(_Paginated):
    """GET /events/multivariate."""

    limit: int | None = _page_limit(200)
    series_ticker: str | None = Field(
        default=None, description="Filter by series ticker."
    )
    collection_ticker: str | None = Field(
        default=None,
        description=(
            "Filter events by collection ticker. Returns only multivariate events "
            "belonging to the specified collection. Cannot be used together with "
            "series_ticker."
        ),
    )
    tickers: str | None = Field(
        default=None,
        description=(
            "Filter by specific event tickers. Comma-separated list of event "
            "tickers to retrieve."
        ),
    )
    with_nested_markets: bool | None = Field(
        default=None,
        description=(
            "When true, each event includes a markets field. Defaults to false."
        ),
    )

    @model_validator(mode="after")
    def _exclusive_collection_and_series(self) -> "ListMultivariateEventsRequest":
        # GET /events/multivariate: collection_ticker "Cannot be used together with
        # series_ticker."
        if self.collection_ticker is not None and self.series_ticker is not None:
            raise ValueError(
                "collection_ticker cannot be used together with series_ticker."
            )
        return self


class GetEventFeeChangesRequest(_Paginated):
    """GET /events/fee_changes."""

    event_ticker: str | None = Field(
        default=None, description="Filter by event ticker."
    )
    limit: int | None = _page_limit(1000)


class GetEventMetadataRequest(MCPSchemaBaseModel):
    """GET /events/{event_ticker}/metadata."""

    event_ticker: str = Field(..., description="Event ticker.")


class GetEventForecastPercentileHistoryRequest(MCPSchemaBaseModel):
    """GET /series/{series_ticker}/events/{ticker}/forecast_percentile_history."""

    series_ticker: str = Field(..., description="The series ticker.")
    ticker: str = Field(..., description="The event ticker.")
    percentiles: list[Annotated[int, Field(ge=0, le=9999)]] = Field(
        ...,
        max_length=10,
        description="Percentile values to retrieve (0-9999, max 10 values).",
    )
    start_ts: int = Field(..., description="Start timestamp for the range.")
    end_ts: int = Field(..., description="End timestamp for the range.")
    period_interval: Literal[0, 1, 60, 1440] = Field(
        ...,
        description=(
            "Length of each forecast period, in minutes. 0 for 5-second intervals, "
            "or 1, 60, or 1440 for minute-based intervals."
        ),
    )


class GetOrderGroupRequest(MCPSchemaBaseModel):
    """GET /portfolio/order_groups/{order_group_id}."""

    order_group_id: str = Field(..., description="Order group ID.")
    subaccount: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Subaccount number (0 for primary, 1-63 for subaccounts). "
            "If omitted, defaults to all subaccounts."
        ),
    )


class ListIntraExchangeInstanceTransfersRequest(_Paginated):
    """GET /portfolio/intra_exchange_instance_transfers."""

    limit: int | None = _page_limit(500)


class GetIntraExchangeInstanceTransferRequest(MCPSchemaBaseModel):
    """GET /portfolio/intra_exchange_instance_transfers/{transfer_id}."""

    transfer_id: str = Field(
        ..., description="Transfer id returned by the creation endpoint."
    )


class GetCreditHistoryRequest(_Paginated):
    """GET /portfolio/credit_history. min_ts is inclusive and max_ts is exclusive."""

    min_ts: int | None = Field(
        default=None, description="Filter items after this Unix timestamp."
    )
    max_ts: int | None = Field(
        default=None, description="Filter items before this Unix timestamp."
    )


class GetCommunicationsIdRequest(EmptyRequest):
    """GET /communications/id."""


class ListBlockTradeProposalsRequest(_Paginated):
    """GET /communications/block-trade-proposals."""

    limit: int | None = _page_limit(100)
    market_ticker: str | None = Field(
        default=None, description="Filter by market ticker."
    )
    status: str | None = Field(
        default=None,
        description=(
            "Filter block trade proposals by status. The spec types this as a "
            "string and does not list the allowed values."
        ),
    )


class ListRFQsRequest(_Paginated):
    """GET /communications/rfqs."""

    limit: int | None = _page_limit(100)
    event_ticker: str | None = Field(
        default=None,
        description="Event ticker to filter by. Only a single event ticker is supported.",
    )
    market_ticker: str | None = Field(
        default=None, description="Filter by market ticker."
    )
    subaccount: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Subaccount number (0 for primary, 1-63 for subaccounts). "
            "If omitted, defaults to all subaccounts."
        ),
    )
    status: str | None = Field(
        default=None,
        description=(
            "Filter RFQs by status. The spec types this as a string and does not "
            "list the allowed values."
        ),
    )
    creator_user_id: str | None = Field(
        default=None,
        description=(
            "Deprecated. Filter RFQs by creator user UUID. Use user_filter=self "
            "for the authenticated user."
        ),
    )
    user_filter: Literal["self"] | None = Field(
        default=None,
        description=(
            "Omit to return all results. Use self to filter by the authenticated user."
        ),
    )


class GetRFQRequest(MCPSchemaBaseModel):
    """GET /communications/rfqs/{rfq_id}."""

    rfq_id: str = Field(
        ...,
        description=(
            "RFQ UUID returned when the RFQ was created. Pass it unchanged; "
            "malformed IDs return HTTP 400."
        ),
    )


class GetRFQQuoteRequest(MCPSchemaBaseModel):
    """GET /communications/rfqs/{rfq_id}/quotes/{quote_id}."""

    rfq_id: str = Field(
        ...,
        description=(
            "RFQ UUID returned when the RFQ was created. Pass it unchanged; "
            "malformed IDs return HTTP 400."
        ),
    )
    quote_id: str = Field(
        ...,
        description=(
            "Quote UUID. Pass the ID exactly as received when the quote was created; "
            "malformed IDs return HTTP 400."
        ),
    )


class ListQuotesRequest(_Paginated):
    """GET /communications/quotes."""

    limit: int | None = _page_limit(500)
    min_ts: int | None = Field(
        default=None,
        description=(
            "Restricts the response to quotes last updated after a Unix timestamp."
        ),
    )
    max_ts: int | None = Field(
        default=None,
        description=(
            "Restricts the response to quotes last updated before a Unix timestamp."
        ),
    )
    status: str | None = Field(
        default=None,
        description=(
            "Filter quotes by status. The spec types this as a string and does not "
            "list the allowed values."
        ),
    )
    quote_creator_user_id: str | None = Field(
        default=None,
        description="Deprecated. Filter quotes by quote creator user ID.",
    )
    user_filter: Literal["self"] | None = Field(
        default=None,
        description="Filter for quotes created by the authenticated user.",
    )
    rfq_user_filter: Literal["self"] | None = Field(
        default=None,
        description=(
            "Filter for quotes responding to RFQs created by the authenticated user."
        ),
    )
    rfq_creator_user_id: str | None = Field(
        default=None,
        description="Deprecated. Filter quotes by RFQ creator user ID.",
    )
    rfq_creator_subtrader_id: str | None = Field(
        default=None,
        description="Filter quotes by RFQ creator subtrader ID (FCM members only).",
    )
    rfq_id: str | None = Field(
        default=None,
        description=(
            "Filter quotes by RFQ UUID. Pass the RFQ ID unchanged; malformed IDs "
            "return HTTP 400."
        ),
    )


class GetQuoteRequest(MCPSchemaBaseModel):
    """GET /communications/quotes/{quote_id}. Deprecated in the spec."""

    quote_id: str = Field(
        ...,
        description=(
            "Quote UUID. Pass the ID exactly as received when the quote was created; "
            "malformed IDs return HTTP 400."
        ),
    )


class ListApiKeysRequest(MCPSchemaBaseModel):
    """GET /api_keys."""

    fcm_subtrader_id: str | None = Field(
        default=None,
        description=(
            "Return only API keys bound to this FCM subtrader. Spelled "
            "{your_user_id}_{suffix}; only FCM members hold bound keys. "
            "Omit to return every key."
        ),
    )


class GetAccountApiUsageLevelVolumeProgressRequest(EmptyRequest):
    """GET /account/api_usage_level/volume_progress."""


class GetLiveDataByMilestoneRequest(MCPSchemaBaseModel):
    """GET /live_data/milestone/{milestone_id}."""

    milestone_id: str = Field(..., description="Milestone ID.")
    include_player_stats: bool | None = Field(
        default=None,
        description=(
            "When true, include player-level statistics. Supported for Pro Football, "
            "Pro Basketball, and College Men's Basketball milestones that have "
            "player ID mappings. Defaults to false."
        ),
    )


class GetLiveDataRequest(MCPSchemaBaseModel):
    """GET /live_data/{type}/milestone/{milestone_id}. Legacy path; prefer the untyped one."""

    type: str = Field(..., description="Type of live data.")
    milestone_id: str = Field(..., description="Milestone ID.")
    include_player_stats: bool | None = Field(
        default=None,
        description=(
            "When true, include player-level statistics. Supported for Pro Football, "
            "Pro Basketball, and College Men's Basketball milestones that have "
            "player ID mappings. Defaults to false."
        ),
    )


class GetGameStatsRequest(MCPSchemaBaseModel):
    """GET /live_data/milestone/{milestone_id}/game_stats."""

    milestone_id: str = Field(..., description="Milestone ID.")


class GetWeatherIndexRequest(MCPSchemaBaseModel):
    """GET /live_data/weather/{city}.

    ``last_sec`` is mutually exclusive with ``from`` / ``to``. ``from`` must be
    paired with ``to`` unless ``last_sec`` is used. The spec does not forbid
    ``to`` alone.
    """

    model_config = ConfigDict(populate_by_name=True)

    city: str = Field(..., description="Index city ID (e.g. miami).")
    from_: int | None = Field(
        default=None,
        alias="from",
        description=(
            "Window start, unix milliseconds (inclusive). Defaults to to minus 24 "
            "hours. Must be paired with to unless last_sec is used."
        ),
    )
    to: int | None = Field(
        default=None,
        description="Window end, unix milliseconds (inclusive). Defaults to now.",
    )
    last_sec: int | None = Field(
        default=None,
        description=(
            "Trailing window in seconds; equivalent to from=now-last_sec, to=now. "
            "Mutually exclusive with from/to."
        ),
    )
    detailed: bool | None = Field(
        default=None,
        description="Include per-station audit readings on every point.",
    )

    @model_validator(mode="after")
    def _window(self) -> "GetWeatherIndexRequest":
        if self.last_sec is not None and (
            self.from_ is not None or self.to is not None
        ):
            raise ValueError("last_sec cannot be combined with from or to.")
        if self.from_ is not None and self.to is None:
            raise ValueError("from must be paired with to.")
        return self


class GetWeatherIndexCalibrationsRequest(MCPSchemaBaseModel):
    """GET /live_data/weather/{city}/calibrations."""

    city: str = Field(..., description="Index city ID (e.g. miami).")


class ListStructuredTargetsRequest(MCPSchemaBaseModel):
    """GET /structured_targets. ``ids`` is form-style and exploded."""

    ids: list[str] | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Filter by specific structured target IDs. Repeat the parameter "
            "(ids=uuid1&ids=uuid2)."
        ),
    )
    type: str | None = Field(
        default=None, description="Filter by structured target type."
    )
    competition: str | None = Field(
        default=None,
        description=(
            "Filter by competition. Matches the league, conference, division, or "
            "tour in the structured target details, or any entry in the details "
            "leagues array."
        ),
    )
    page_size: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        description="Number of items per page (min 1, max 2000, default 100).",
    )
    cursor: str | None = Field(default=None, description="Pagination cursor.")


class GetStructuredTargetRequest(MCPSchemaBaseModel):
    """GET /structured_targets/{structured_target_id}."""

    structured_target_id: str = Field(..., description="Structured target ID.")


class GetWeatherPointAvailabilityRequest(EmptyRequest):
    """GET /weather_point_markets/availability."""


class GetConcertCancellationAvailabilityRequest(MCPSchemaBaseModel):
    """GET /concert_cancellation_markets/availability."""

    date_from: str | None = Field(
        default=None,
        description=(
            "Count shows from this venue-local date, as YYYY-MM-DD. "
            "Omitted counts from today."
        ),
    )
    date_to: str | None = Field(
        default=None,
        description=(
            "Count shows up to this venue-local date, inclusive, as YYYY-MM-DD. "
            "Omitted for no end."
        ),
    )


class ListConcertsRequest(MCPSchemaBaseModel):
    """GET /concert_cancellation_markets/concerts."""

    metro_id: str = Field(
        ...,
        description=(
            "One of the metros from get_concert_cancellation_availability, "
            "such as jambase:1."
        ),
    )
    date_from: str | None = Field(
        default=None,
        description=(
            "List shows from this venue-local date, as YYYY-MM-DD. Omitted, or a "
            "date that has passed, lists from today."
        ),
    )
    date_to: str | None = Field(
        default=None,
        description=(
            "List shows up to this venue-local date, inclusive, as YYYY-MM-DD. "
            "Must not be before date_from. Omitted for no end."
        ),
    )
    page: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Page to return, starting at 1. Defaults to 1.",
    )


class SearchConcertsRequest(MCPSchemaBaseModel):
    """GET /concert_cancellation_markets/concerts/search."""

    q: str = Field(
        ...,
        min_length=2,
        max_length=80,
        description=(
            "The name to search for, or part of one. 2 to 80 characters; some "
            "searches need at least 3."
        ),
    )


class ListIncentiveProgramsRequest(_Paginated):
    """GET /incentive_programs."""

    limit: int | None = _page_limit(10000)
    status: Literal["all", "active", "upcoming", "closed", "paid_out"] | None = Field(
        default=None,
        description=(
            'Status filter: "all", "active", "upcoming", "closed", or "paid_out". '
            'Default is "all".'
        ),
    )
    type: (
        Literal[
            "all", "liquidity", "volume", "margin_maker_volume", "margin_taker_volume"
        ]
        | None
    ) = Field(
        default=None,
        description=(
            'Type filter: "all", "liquidity", "volume", "margin_maker_volume", or '
            '"margin_taker_volume". Default is "all".'
        ),
    )
    incentive_description: str | None = Field(
        default=None, description="Filter by exact incentive description."
    )
    series_ticker: str | None = Field(
        default=None, description="Filter to one series' incentives."
    )
    event_ticker: str | None = Field(
        default=None,
        description=(
            "Filter to one event's incentives. A perpetual's incentives belong to "
            "no event."
        ),
    )


class GetIncentiveSchedulesRequest(EmptyRequest):
    """GET /incentive_schedules."""


class GetFCMFillsRequest(MCPSchemaBaseModel):
    """GET /fcm/fills. FCM members only. The spec has no limit parameter."""

    min_ts: int | None = Field(
        default=None, description="Filter items after this Unix timestamp."
    )
    max_ts: int | None = Field(
        default=None, description="Filter items before this Unix timestamp."
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "Pagination cursor. Use the cursor value returned from the previous "
            "response to get the next page. Leave empty for the first page."
        ),
    )


class GetFCMOrdersRequest(_Paginated):
    """GET /fcm/orders. FCM members only.

    The spec marks both ``subtrader_id`` and ``client_order_ids`` optional.
    Its description says at least one is required unless the API key is bound
    to a subtrader, in which case ``subtrader_id`` defaults to that subtrader.
    """

    subtrader_id: str | None = Field(
        default=None,
        description=(
            "Restricts the response to orders for a specific subtrader "
            "(FCM members only). Required unless client_order_ids is supplied. "
            "For an API key bound to a subtrader, defaults to the bound subtrader "
            "when omitted and must equal it when supplied."
        ),
    )
    client_order_ids: str | None = Field(
        default=None,
        description=(
            "Client order IDs to filter by, as a comma-separated list (maximum 100). "
            "Required unless subtrader_id is supplied."
        ),
    )
    event_ticker: str | None = Field(
        default=None,
        description="Event ticker to filter by. Only a single event ticker is supported.",
    )
    ticker: str | None = Field(default=None, description="Filter by market ticker.")
    min_ts: int | None = Field(
        default=None,
        description="Restricts the response to orders after a Unix timestamp.",
    )
    max_ts: int | None = Field(
        default=None,
        description="Restricts the response to orders before a Unix timestamp.",
    )
    status: Literal["resting", "canceled", "executed"] | None = Field(
        default=None,
        description="Restricts the response to orders that have a certain status.",
    )


class ListFCMSubtradersRequest(EmptyRequest):
    """GET /fcm/subtraders. FCM members only."""


class GetFCMEventContractDailyCapRequest(MCPSchemaBaseModel):
    """GET /fcm/subtraders/event_contract_daily_cap. FCM members only."""

    subtrader_id: str | None = Field(
        default=None,
        description=(
            "The subtrader whose daily cap should be returned. Must belong to the "
            "requesting FCM. Required unless the API key is bound to a subtrader, "
            "in which case it defaults to the bound subtrader when omitted and must "
            "equal it when supplied."
        ),
    )


class GetFCMSubtraderBlockedCategoriesRequest(MCPSchemaBaseModel):
    """GET /fcm/subtraders/blocked_categories. FCM members only."""

    subtrader_id: str = Field(
        ...,
        description=(
            "The subtrader whose blocked categories should be returned. "
            "Must belong to the requesting FCM."
        ),
    )


class GetTargetBalanceAllocationRequest(EmptyRequest):
    """GET /portfolio/target_balance_allocation."""


class GetFCMPositionsRequest(_Paginated):
    """GET /fcm/positions. FCM members only.

    ``subtrader_id`` is optional in the spec: required unless the API key is
    bound to a subtrader, in which case it defaults to that subtrader.
    ``count_filter`` is a comma-separated list; the spec does not name the fields.
    """

    subtrader_id: str | None = Field(
        default=None,
        description=(
            "Restricts the response to positions for a specific subtrader "
            "(FCM members only). Required unless the API key is bound to a "
            "subtrader, in which case it defaults to the bound subtrader when "
            "omitted and must equal it when supplied."
        ),
    )
    ticker: str | None = Field(default=None, description="Ticker of desired positions.")
    event_ticker: str | None = Field(
        default=None, description="Event ticker of desired positions."
    )
    count_filter: str | None = Field(
        default=None,
        description=(
            "Restricts the positions to those with any of the following fields "
            "with non-zero values, as a comma-separated list. The spec does not "
            "name the fields."
        ),
    )
    settlement_status: Literal["all", "unsettled", "settled"] | None = Field(
        default=None,
        description=(
            "Settlement status of the markets to return. Defaults to unsettled."
        ),
    )
