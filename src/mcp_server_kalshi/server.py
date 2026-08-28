import asyncio
import json
import time
import uuid
from collections.abc import Callable, Coroutine
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from .config import get_settings
from .errors import redact_secrets
from .kalshi_client import KalshiAPIClient
from .kalshi_client.client import (
    build_amend_order_payload,
    build_create_order_payload,
    build_decrease_order_payload,
    series_ticker_from_market,
)
from .kalshi_client.pdf import fetch_pdf_text
from .kalshi_client.schemas import (
    AmendOrderRequest,
    BatchCancelOrdersRequest,
    BatchCreateOrdersRequest,
    CancelOrderGroupRequest,
    CancelOrderRequest,
    CreateOrderRequest,
    DecreaseOrderRequest,
    EmptyRequest,
    FetchRulesPdfRequest,
    GetEventLiveDataRequest,
    GetEventRequest,
    GetFillsRequest,
    GetMarketCandlesticksRequest,
    GetMarketOrderbookRequest,
    GetMarketRequest,
    GetMarketRulesRequest,
    GetMarketTradesRequest,
    GetMilestoneRequest,
    GetMilestonesRequest,
    GetMultivariateCollectionRequest,
    GetOrderRequest,
    GetPortfolioSummaryRequest,
    GetPositionsRequest,
    GetSeriesRequest,
    GetSettlementsRequest,
    GetSportsFiltersRequest,
    GetTagsByCategoriesRequest,
    ListEventsRequest,
    ListMarketsRequest,
    ListMultivariateCollectionsRequest,
    ListOrderGroupsRequest,
    ListOrdersRequest,
    ListSeriesRequest,
    MCPSchemaBaseModel,
)

try:
    __version__ = version("mcp-server-kalshi")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


def _background_info(env_label: str, is_production: bool) -> str:
    """Server MCP `instructions`, with the *resolved* environment stated as fact.

    The environment line is dynamic on purpose: a static "demo unless configured for prod"
    string leaves the model to guess which one is active, and it tends to assume demo. Stating
    the real environment here (instructions are always in context) is the primary signal.
    """
    env_line = (
        f"⚠️ ENVIRONMENT: this server is configured for {env_label}. Orders you place are REAL "
        "and settle for real money — confirm intent before trading."
        if is_production
        else f"ENVIRONMENT: this server is configured for {env_label}. Orders are simulated; "
        "no real money is at stake."
    )
    return f"""\
Kalshi is a regulated prediction-market exchange. You trade $1 binary contracts that settle
to YES ($1) or NO ($0) based on a real-world outcome.

{env_line}

Hierarchy:
- Series: a recurring template (e.g. 'KXELONMARS') that owns the legal contract terms,
  settlement sources, and the rules PDFs (contract_terms_url / contract_url).
- Event: a specific occurrence to bet on (e.g. 'KXELONMARS-99').
- Market: a single YES/NO question under an event, identified by a ticker.

Prices are quoted in whole cents (1-99). Buying YES at Pc costs P cents and pays $1 if YES.
Buying NO at Qc costs Q cents and pays $1 if NO; note YES + NO ~= 100c.

Workflow for deep trading:
1. Discover with list_markets / list_events / list_series (there is no free-text search).
2. Research with get_market, get_market_orderbook, get_market_candlesticks, get_market_trades.
3. Understand settlement with get_market_rules, and read the actual contract with fetch_rules_pdf.
4. Trade with create_order (requires confirm=true) / cancel_order / amend_order.

Call get_environment to re-confirm the active environment at any time. Order tools return a
preview and place nothing unless called with confirm=true.
"""


settings = get_settings()

KALSHI_BACKGROUND_INFO = _background_info(settings.env_label, settings.is_production)

server: Server = Server("kalshi-server")
kalshi_client = KalshiAPIClient(
    base_url=settings.rest_base_url,
    api_key=settings.api_key_value(),
    private_key_path=settings.KALSHI_PRIVATE_KEY_PATH,
)


def _serialize(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


def _annotations(
    read_only: bool,
    destructive: bool,
    idempotent: bool | None = None,
    open_world: bool | None = None,
) -> types.ToolAnnotations | None:
    """Build ToolAnnotations when the installed mcp version supports them, else None."""
    ann_cls = getattr(types, "ToolAnnotations", None)
    if ann_cls is None:
        return None
    kwargs: dict[str, Any] = {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
    }
    if idempotent is not None:
        kwargs["idempotentHint"] = idempotent
    if open_world is not None:
        kwargs["openWorldHint"] = open_world
    return cast(types.ToolAnnotations, ann_cls(**kwargs))


HandlerCallable = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]
WrappedHandler = Callable[
    [dict[str, Any]], Coroutine[Any, Any, list[types.TextContent]]
]


class ToolRegistry:
    _tools: dict[str, tuple[types.Tool, WrappedHandler]] = {}

    @classmethod
    def register_tool(
        cls,
        name: str,
        description: str,
        input_schema: type[MCPSchemaBaseModel],
        read_only: bool = True,
        destructive: bool = False,
        idempotent: bool | None = None,
        open_world: bool | None = None,
    ) -> Callable[[HandlerCallable], WrappedHandler]:
        def decorator(handler: HandlerCallable) -> WrappedHandler:
            @wraps(handler)
            async def wrapped_handler(
                request: dict[str, Any],
            ) -> list[types.TextContent]:
                result = await handler(request)
                return [types.TextContent(type="text", text=_serialize(result))]

            cls._tools[name] = (
                types.Tool(
                    name=name,
                    description=description,
                    inputSchema=input_schema.to_mcp_input_schema(),
                    annotations=_annotations(
                        read_only=read_only,
                        destructive=destructive,
                        idempotent=idempotent,
                        open_world=open_world,
                    ),
                ),
                wrapped_handler,
            )
            return wrapped_handler

        return decorator

    @classmethod
    def get_tools(cls) -> list[types.Tool]:
        if settings.KALSHI_READONLY:
            return [
                tool
                for tool, _ in cls._tools.values()
                if tool.annotations is not None
                and tool.annotations.readOnlyHint is True
            ]
        return [tool for tool, _ in cls._tools.values()]

    @classmethod
    def get_handler(cls, name: str) -> WrappedHandler:
        if name not in cls._tools:
            raise ValueError(f"Unknown tool: {name}")
        tool, handler = cls._tools[name]
        if settings.KALSHI_READONLY:
            if tool.annotations is None or tool.annotations.readOnlyHint is not True:
                raise ValueError(
                    f"Server is operating in read-only mode (KALSHI_READONLY=1); tool '{name}' is disabled."
                )
        return handler


def _params(
    request: dict[str, Any],
    model: type[MCPSchemaBaseModel],
    drop: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Validate `request` against `model` and return query params (None + `drop` removed)."""
    data: dict[str, Any] = model(**request).model_dump(exclude_none=True)
    for key in drop:
        data.pop(key, None)
    return data


# =============================== Discovery ===================================
@ToolRegistry.register_tool(
    name="list_markets",
    description=(
        "Browse or filter markets. Kalshi has no free-text search, so use filters like "
        "series_ticker, event_ticker, status, or a comma-separated `tickers` list. "
        "Returns markets with prices (in dollars), status, and rules_primary."
    ),
    input_schema=ListMarketsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_markets(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_markets(_params(request, ListMarketsRequest))


@ToolRegistry.register_tool(
    name="get_market",
    description="Get full detail for one market by ticker, including prices, status, and rules_primary/rules_secondary.",
    input_schema=GetMarketRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_market(request: dict[str, Any]) -> Any:
    req = GetMarketRequest(**request)
    return await kalshi_client.get_market(req.ticker)


@ToolRegistry.register_tool(
    name="list_events",
    description="Browse events (each groups related markets). Filter by series_ticker/status; set with_nested_markets to include markets inline.",
    input_schema=ListEventsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_events(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_events(_params(request, ListEventsRequest))


@ToolRegistry.register_tool(
    name="get_event",
    description="Get an event by ticker, including its settlement_sources and (optionally) nested markets.",
    input_schema=GetEventRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_event(request: dict[str, Any]) -> Any:
    req = GetEventRequest(**request)
    return await kalshi_client.get_event(req.event_ticker, req.with_nested_markets)


@ToolRegistry.register_tool(
    name="list_series",
    description="List series (recurring market templates) filtered by category/tags.",
    input_schema=ListSeriesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_series(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_series_list(_params(request, ListSeriesRequest))


@ToolRegistry.register_tool(
    name="get_series",
    description=(
        "Get a series by ticker. This is where the legal contract lives: settlement_sources, "
        "additional_prohibitions, and the rules PDFs (contract_terms_url, contract_url)."
    ),
    input_schema=GetSeriesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_series(request: dict[str, Any]) -> Any:
    req = GetSeriesRequest(**request)
    return await kalshi_client.get_series(req.series_ticker)


# =============================== Research / rules ============================
@ToolRegistry.register_tool(
    name="get_market_orderbook",
    description="Get the current order book (resting YES and NO bids) for a market. Optional depth (1-100).",
    input_schema=GetMarketOrderbookRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_market_orderbook(request: dict[str, Any]) -> Any:
    req = GetMarketOrderbookRequest(**request)
    return await kalshi_client.get_market_orderbook(req.ticker, req.depth)


@ToolRegistry.register_tool(
    name="get_market_candlesticks",
    description=(
        "Get OHLC price history for a market. Defaults to the last 24h at 60-minute candles; "
        "override with period_interval (1/60/1440), lookback_hours, or explicit start_ts/end_ts."
    ),
    input_schema=GetMarketCandlesticksRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_market_candlesticks(request: dict[str, Any]) -> Any:
    req = GetMarketCandlesticksRequest(**request)
    end_ts = req.end_ts or int(time.time())
    start_ts = req.start_ts or (end_ts - (req.lookback_hours or 24) * 3600)
    return await kalshi_client.get_market_candlesticks(
        ticker=req.ticker,
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=req.period_interval,
        series_ticker=req.series_ticker,
    )


@ToolRegistry.register_tool(
    name="get_market_trades",
    description="Get recent public trades (executions) for a market.",
    input_schema=GetMarketTradesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_market_trades(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_market_trades(
        _params(request, GetMarketTradesRequest)
    )


@ToolRegistry.register_tool(
    name="get_market_rules",
    description=(
        "Deep settlement rules for a market: consolidates the market's rules_primary/"
        "rules_secondary/early_close_condition with the event's settlement_sources and the "
        "series' additional_prohibitions and rules-PDF links. Start here to understand how a "
        "market resolves; call fetch_rules_pdf to read the full legal contract."
    ),
    input_schema=GetMarketRulesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_market_rules(request: dict[str, Any]) -> Any:
    req = GetMarketRulesRequest(**request)
    market_resp = await kalshi_client.get_market(req.ticker)
    market: dict[str, Any] = market_resp.get("market", market_resp)

    series_ticker = series_ticker_from_market(req.ticker)
    series: dict[str, Any] = {}
    try:
        series_resp = await kalshi_client.get_series(series_ticker)
        series = series_resp.get("series", series_resp)
    except Exception as exc:  # series lookup is best-effort
        series = {"error": f"could not load series {series_ticker}: {exc}"}

    event: dict[str, Any] = {}
    event_ticker = market.get("event_ticker")
    if event_ticker:
        try:
            event_resp = await kalshi_client.get_event(event_ticker)
            event = event_resp.get("event", event_resp)
        except Exception as exc:
            event = {"error": f"could not load event {event_ticker}: {exc}"}

    return {
        "ticker": req.ticker,
        "title": market.get("title"),
        "status": market.get("status"),
        "expiration_time": market.get("expiration_time"),
        "can_close_early": market.get("can_close_early"),
        "early_close_condition": market.get("early_close_condition"),
        "settlement_timer_seconds": market.get("settlement_timer_seconds"),
        "rules_primary": market.get("rules_primary"),
        "rules_secondary": market.get("rules_secondary"),
        "event_ticker": event_ticker,
        "settlement_sources": event.get("settlement_sources")
        or series.get("settlement_sources"),
        "series_ticker": series_ticker,
        "additional_prohibitions": series.get("additional_prohibitions"),
        "contract_terms_url": series.get("contract_terms_url"),
        "contract_url": series.get("contract_url"),
        "hint": "Call fetch_rules_pdf with this ticker to read the full contract terms PDF.",
    }


@ToolRegistry.register_tool(
    name="fetch_rules_pdf",
    description=(
        "Download and extract the text of a market's rules PDF so you can read the exact legal "
        "contract terms. Pass a market `ticker` or `series_ticker` (the server resolves the "
        "series' contract_terms_url or contract_url) or a direct `url`."
    ),
    input_schema=FetchRulesPdfRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=True,
)
async def handle_fetch_rules_pdf(request: dict[str, Any]) -> Any:
    req = FetchRulesPdfRequest(**request)
    url = req.url
    if not url:
        series_ticker = req.series_ticker or (
            series_ticker_from_market(req.ticker) if req.ticker else None
        )
        if not series_ticker:
            raise ValueError("Provide a url, a series_ticker, or a market ticker.")
        series_resp = await kalshi_client.get_series(series_ticker)
        series = series_resp.get("series", series_resp)
        field = (
            "contract_terms_url" if req.document == "contract_terms" else "contract_url"
        )
        url = series.get(field)
        if not url:
            raise ValueError(
                f"Series {series_ticker} has no {field}. Available: "
                f"contract_terms_url={series.get('contract_terms_url')}, "
                f"contract_url={series.get('contract_url')}"
            )
    return await fetch_pdf_text(url)


# =============================== Environment ================================
@ToolRegistry.register_tool(
    name="get_environment",
    description=(
        "Report which Kalshi environment this server is configured for (demo sandbox vs prod "
        "real money), the REST base URL in use, and whether trading credentials are configured. "
        "Call this to confirm the environment before trading rather than guessing."
    ),
    input_schema=EmptyRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_environment(request: dict[str, Any]) -> Any:
    return {
        "environment": settings.env_label,
        "is_production": settings.is_production,
        "base_url": settings.rest_base_url,
        "has_credentials": settings.has_credentials,
        "readonly_mode": settings.KALSHI_READONLY,
        "note": (
            "Real money is at stake; orders settle for real."
            if settings.is_production
            else "Sandbox environment; orders are simulated and no real money is at stake."
        ),
    }


# =============================== Exchange ===================================
@ToolRegistry.register_tool(
    name="get_exchange_status",
    description="Check whether the exchange and trading are currently active.",
    input_schema=EmptyRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_exchange_status(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_exchange_status()


@ToolRegistry.register_tool(
    name="get_exchange_schedule",
    description="Get the exchange's standard trading hours and maintenance windows.",
    input_schema=EmptyRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_exchange_schedule(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_exchange_schedule()


# =============================== Portfolio ==================================
@ToolRegistry.register_tool(
    name="get_balance",
    description="Get your account balance and portfolio value (authenticated).",
    input_schema=EmptyRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_balance(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_balance()


@ToolRegistry.register_tool(
    name="get_positions",
    description="List your current market positions (authenticated).",
    input_schema=GetPositionsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_positions(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_positions(_params(request, GetPositionsRequest))


@ToolRegistry.register_tool(
    name="get_fills",
    description="List your fills (matched trades) (authenticated).",
    input_schema=GetFillsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_fills(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_fills(_params(request, GetFillsRequest))


@ToolRegistry.register_tool(
    name="get_settlements",
    description="List your settled positions and their outcomes (authenticated).",
    input_schema=GetSettlementsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_settlements(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_settlements(_params(request, GetSettlementsRequest))


# =============================== Orders / trading ===========================
@ToolRegistry.register_tool(
    name="list_orders",
    description="List your orders (resting/canceled/executed), optionally filtered (authenticated).",
    input_schema=ListOrdersRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_orders(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_orders(_params(request, ListOrdersRequest))


@ToolRegistry.register_tool(
    name="get_order",
    description="Get a single order by id (authenticated).",
    input_schema=GetOrderRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_order(request: dict[str, Any]) -> Any:
    req = GetOrderRequest(**request)
    return await kalshi_client.get_order(req.order_id)


def _order_preview(req: CreateOrderRequest, payload: dict[str, Any]) -> dict[str, Any]:
    est_cost_cents = req.count * req.limit_price
    return {
        "preview": True,
        "message": (
            f"[{settings.env_label}] This will {req.action.upper()} {req.count} "
            f"{req.side.upper()} contract(s) of {req.ticker} at {req.limit_price}c "
            f"({req.time_in_force}). Estimated "
            f"{'cost' if req.action == 'buy' else 'proceeds'}: "
            f"${est_cost_cents / 100:.2f}. Re-run with confirm=true to place."
        ),
        "environment": settings.env_label,
        "order": {
            "ticker": req.ticker,
            "action": req.action,
            "side": req.side,
            "count": req.count,
            "limit_price_cents": req.limit_price,
            "time_in_force": req.time_in_force,
        },
        "kalshi_v2_payload": payload,
        "confirm_required": True,
    }


@ToolRegistry.register_tool(
    name="create_order",
    description=(
        "Place a limit order using the intuitive buy/sell + yes/no model with a cents price. "
        "SAFETY: returns a preview and places NOTHING unless confirm=true. Honors the demo/prod "
        "environment the server is configured for."
    ),
    input_schema=CreateOrderRequest,
    read_only=False,
    destructive=True,
    idempotent=False,
    open_world=False,
)
async def handle_create_order(request: dict[str, Any]) -> Any:
    req = CreateOrderRequest(**request)
    payload = build_create_order_payload(
        ticker=req.ticker,
        action=req.action,
        side=req.side,
        count=req.count,
        limit_price_cents=req.limit_price,
        time_in_force=req.time_in_force,
        post_only=req.post_only,
        reduce_only=req.reduce_only,
        client_order_id=req.client_order_id or str(uuid.uuid4()),
        expiration_ts=req.expiration_ts,
    )
    if not req.confirm:
        return _order_preview(req, payload)
    result = await kalshi_client.create_order(payload)
    return {
        "placed": True,
        "environment": settings.env_label,
        "submitted": payload,
        "result": result,
    }


@ToolRegistry.register_tool(
    name="cancel_order",
    description="Cancel a resting order by id (authenticated). Reduces your exposure.",
    input_schema=CancelOrderRequest,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=False,
)
async def handle_cancel_order(request: dict[str, Any]) -> Any:
    req = CancelOrderRequest(**request)
    return await kalshi_client.cancel_order(req.order_id)


@ToolRegistry.register_tool(
    name="amend_order",
    description=(
        "Amend a resting order's price and/or total count. SAFETY: returns a preview and applies "
        "NOTHING unless confirm=true."
    ),
    input_schema=AmendOrderRequest,
    read_only=False,
    destructive=True,
    idempotent=False,
    open_world=False,
)
async def handle_amend_order(request: dict[str, Any]) -> Any:
    req = AmendOrderRequest(**request)
    payload = build_amend_order_payload(
        ticker=req.ticker,
        action=req.action,
        side=req.side,
        count=req.count,
        limit_price_cents=req.limit_price,
        updated_client_order_id=req.updated_client_order_id,
    )
    if not req.confirm:
        return {
            "preview": True,
            "message": (
                f"[{settings.env_label}] Amend order {req.order_id} to {req.count} "
                f"{req.side.upper()} @ {req.limit_price}c. Re-run with confirm=true to apply."
            ),
            "environment": settings.env_label,
            "kalshi_v2_payload": payload,
            "confirm_required": True,
        }
    result = await kalshi_client.amend_order(req.order_id, payload)
    return {"amended": True, "environment": settings.env_label, "result": result}


@ToolRegistry.register_tool(
    name="decrease_order",
    description="Decrease a resting order's remaining count. Provide exactly one of reduce_by or reduce_to. Reduces exposure.",
    input_schema=DecreaseOrderRequest,
    read_only=False,
    destructive=True,
    idempotent=False,
    open_world=False,
)
async def handle_decrease_order(request: dict[str, Any]) -> Any:
    req = DecreaseOrderRequest(**request)
    payload = build_decrease_order_payload(req.reduce_by, req.reduce_to)
    return await kalshi_client.decrease_order(req.order_id, payload)


# ========================== Batch Orders & Groups ============================
@ToolRegistry.register_tool(
    name="batch_create_orders",
    description=(
        "Place up to 20 limit orders in a single atomic request (V2). "
        "SAFETY: returns a simulation preview unless 'confirm=true' is passed explicitly."
    ),
    input_schema=BatchCreateOrdersRequest,
    read_only=False,
    destructive=True,
    idempotent=False,
    open_world=False,
)
async def handle_batch_create_orders(request: dict[str, Any]) -> Any:
    req = BatchCreateOrdersRequest(**request)
    order_payloads: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    for item in req.orders:
        client_order_id = item.client_order_id or str(uuid.uuid4())
        p = build_create_order_payload(
            ticker=item.ticker,
            action=item.action,
            side=item.side,
            count=item.count,
            limit_price_cents=item.limit_price,
            time_in_force=item.time_in_force,
            post_only=item.post_only,
            reduce_only=item.reduce_only,
            expiration_ts=item.expiration_ts,
            client_order_id=client_order_id,
        )
        order_payloads.append(p)
        previews.append(
            {
                "ticker": item.ticker,
                "intent": f"{item.action.upper()} {item.count:g}x {item.side.upper()} @ {item.limit_price}¢",
                "kalshi_v2_payload": p,
            }
        )
    if not req.confirm:
        return {
            "preview": True,
            "environment": settings.env_label,
            "batch_size": len(order_payloads),
            "orders": previews,
            "confirm_required": True,
        }
    result = await kalshi_client.batch_create_orders(order_payloads)
    return {
        "placed": True,
        "environment": settings.env_label,
        "batch_size": len(order_payloads),
        "result": result,
    }


@ToolRegistry.register_tool(
    name="batch_cancel_orders",
    description="Cancel up to 20 resting orders in a single atomic request (V2). Reduces portfolio exposure.",
    input_schema=BatchCancelOrdersRequest,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=False,
)
async def handle_batch_cancel_orders(request: dict[str, Any]) -> Any:
    req = BatchCancelOrdersRequest(**request)
    result = await kalshi_client.batch_cancel_orders(req.order_ids)
    return {
        "canceled": True,
        "environment": settings.env_label,
        "count": len(req.order_ids),
        "result": result,
    }


@ToolRegistry.register_tool(
    name="get_portfolio_summary",
    description="Get total resting order value and collateral exposure (authenticated).",
    input_schema=GetPortfolioSummaryRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_portfolio_summary(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_portfolio_summary()


@ToolRegistry.register_tool(
    name="get_tags_by_categories",
    description="Get taxonomy tags grouped by series categories for market discovery.",
    input_schema=GetTagsByCategoriesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_tags_by_categories(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_tags_by_categories()


@ToolRegistry.register_tool(
    name="get_sports_filters",
    description="Get sport-level search and filtering taxonomies.",
    input_schema=GetSportsFiltersRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_sports_filters(request: dict[str, Any]) -> Any:
    return await kalshi_client.get_sports_filters()


@ToolRegistry.register_tool(
    name="get_milestones",
    description="Browse milestone trackers across sports games, elections, and events.",
    input_schema=GetMilestonesRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_milestones(request: dict[str, Any]) -> Any:
    params = _params(request, GetMilestonesRequest)
    return await kalshi_client.get_milestones(params)


@ToolRegistry.register_tool(
    name="get_milestone",
    description="Get detailed milestone status and properties by milestone ID.",
    input_schema=GetMilestoneRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_milestone(request: dict[str, Any]) -> Any:
    req = GetMilestoneRequest(**request)
    return await kalshi_client.get_milestone(req.milestone_id)


@ToolRegistry.register_tool(
    name="get_event_live_data",
    description="Get real-time live scoreboard/game state data for an event ticker.",
    input_schema=GetEventLiveDataRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_event_live_data(request: dict[str, Any]) -> Any:
    req = GetEventLiveDataRequest(**request)
    return await kalshi_client.get_event_live_data(req.event_ticker)


@ToolRegistry.register_tool(
    name="list_multivariate_collections",
    description="List multivariate / combo and parlay market collections.",
    input_schema=ListMultivariateCollectionsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_multivariate_collections(request: dict[str, Any]) -> Any:
    params = _params(request, ListMultivariateCollectionsRequest)
    return await kalshi_client.list_multivariate_collections(params)


@ToolRegistry.register_tool(
    name="get_multivariate_collection",
    description="Get details and component markets for a multivariate / combo collection.",
    input_schema=GetMultivariateCollectionRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_get_multivariate_collection(request: dict[str, Any]) -> Any:
    req = GetMultivariateCollectionRequest(**request)
    return await kalshi_client.get_multivariate_collection(req.collection_ticker)


@ToolRegistry.register_tool(
    name="list_order_groups",
    description="List active order groups (e.g. One-Cancels-Other / OCO risk groups) (authenticated).",
    input_schema=ListOrderGroupsRequest,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def handle_list_order_groups(request: dict[str, Any]) -> Any:
    params = _params(request, ListOrderGroupsRequest)
    return await kalshi_client.list_order_groups(params)


@ToolRegistry.register_tool(
    name="cancel_order_group",
    description="Cancel and dissolve an active order group (authenticated).",
    input_schema=CancelOrderGroupRequest,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=False,
)
async def handle_cancel_order_group(request: dict[str, Any]) -> Any:
    req = CancelOrderGroupRequest(**request)
    result = await kalshi_client.cancel_order_group(req.order_group_id)
    return {"canceled": True, "environment": settings.env_label, "result": result}


# =============================== Server wiring ===============================
@server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
async def handle_list_tools() -> list[types.Tool]:
    return ToolRegistry.get_tools()


@server.call_tool()  # type: ignore[untyped-decorator]
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None = None
) -> list[types.TextContent]:
    try:
        handler = ToolRegistry.get_handler(name)
        return await handler(arguments or {})
    except Exception as exc:
        sanitized = redact_secrets(str(exc))
        return [types.TextContent(type="text", text=f"Error in {name}: {sanitized}")]


async def run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="kalshi-server",
                server_version=__version__,
                instructions=KALSHI_BACKGROUND_INFO,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
