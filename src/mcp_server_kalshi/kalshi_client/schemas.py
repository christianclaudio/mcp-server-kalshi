from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, model_validator


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
    """Optional page size bounded by the endpoint's own OpenAPI v3.34.0 limits."""
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


# Shared optional portfolio scoping fields (Kalshi OpenAPI v3.34.0). Omitted unless set,
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
        # Kalshi OpenAPI v3.34.0, GET /events/{event_ticker}: exclude_markets "Cannot be
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
    limit: int | None = _page_limit(1000, minimum=0)
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

    ``limit`` and ``cursor`` predate Kalshi OpenAPI v3.34.0, which documents only
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
