"""Server profiles and annotation-driven tool visibility.

``full`` (the default) is the complete catalog. ``readonly`` keeps only tools
whose MCP ``readOnlyHint`` is ``True``. ``markets`` and ``portfolio`` are job
allowlists: an explicit set of tool names applied over that catalog. Filtering
is tools-only, so prompts and resources stay listed. There are no domain
mounts, so wire names stay as they are.

The annotation is the source of truth for read-only classification. A missing
annotation, or a ``readOnlyHint`` other than ``True``, counts as a write.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# Job profile ``markets`` (44 tools), in catalog order.
# Research markets. No account data and no orders.
MARKETS_TOOLS: frozenset[str] = frozenset(
    {
        "list_markets",
        "get_market",
        "list_events",
        "get_event",
        "list_series",
        "get_series",
        "get_market_orderbook",
        "get_market_candlesticks",
        "get_market_trades",
        "get_market_rules",
        "fetch_rules_pdf",
        "get_environment",
        "get_exchange_status",
        "get_exchange_schedule",
        "get_tags_by_categories",
        "get_sports_filters",
        "get_milestones",
        "get_milestone",
        "get_event_live_data",
        "list_multivariate_collections",
        "get_multivariate_collection",
        "get_historical_cutoff",
        "list_historical_markets",
        "get_historical_market",
        "get_historical_market_candlesticks",
        "get_historical_trades",
        "batch_get_market_candlesticks",
        "get_live_data_batch",
        "get_series_fee_changes",
        "get_market_orderbooks",
        "get_event_candlesticks",
        "list_multivariate_events",
        "get_event_fee_changes",
        "get_event_metadata",
        "get_event_forecast_percentile_history",
        "get_live_data_by_milestone",
        "get_live_data",
        "get_game_stats",
        "get_weather_index",
        "get_weather_index_calibrations",
        "list_structured_targets",
        "get_structured_target",
        "list_incentive_programs",
        "get_incentive_schedules",
    }
)

# Job profile ``portfolio`` (31 tools), in catalog order.
# Review the account. No order writes.
PORTFOLIO_TOOLS: frozenset[str] = frozenset(
    {
        "get_market",
        "get_environment",
        "get_exchange_status",
        "get_balance",
        "get_positions",
        "get_fills",
        "get_settlements",
        "list_orders",
        "get_order",
        "list_order_groups",
        "get_historical_cutoff",
        "get_historical_fills",
        "list_historical_orders",
        "get_historical_positions",
        "get_account_limits",
        "get_account_endpoint_costs",
        "get_order_queue_positions",
        "get_order_queue_position",
        "list_deposits",
        "list_withdrawals",
        "get_subaccount_balances",
        "list_subaccount_transfers",
        "get_subaccount_netting",
        "get_event_contract_exit_triggers",
        "get_exchange_user_data_timestamp",
        "get_order_group",
        "list_intra_exchange_instance_transfers",
        "get_intra_exchange_instance_transfer",
        "get_credit_history",
        "get_account_api_usage_level_volume_progress",
        "get_target_balance_allocation",
    }
)

# Tools in no job allowlist. They stay reachable on ``full``.
# The 7 order writes, FCM-member tools, RFQ / quote / block-trade /
# communications reads, list_api_keys, and the concert and weather-point tools.
FULL_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "create_order",
        "cancel_order",
        "amend_order",
        "decrease_order",
        "batch_create_orders",
        "batch_cancel_orders",
        "get_total_resting_order_value",
        "cancel_order_group",
        "list_rfq_batches",
        "get_rfq_batch",
        "get_rfq_batch_best_quotes",
        "get_rfq_batch_quotes",
        "get_communications_id",
        "list_block_trade_proposals",
        "list_rfqs",
        "get_rfq",
        "get_rfq_quote",
        "list_quotes",
        "get_quote",
        "list_api_keys",
        "get_weather_point_availability",
        "get_concert_cancellation_availability",
        "list_concerts",
        "search_concerts",
        "get_fcm_fills",
        "get_fcm_orders",
        "list_fcm_subtraders",
        "get_fcm_event_contract_daily_cap",
        "get_fcm_subtrader_blocked_categories",
        "get_fcm_positions",
    }
)


@dataclass(frozen=True)
class Profile:
    """A named catalog: full, read-only, or a tool-name allowlist."""

    name: str
    job: str
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        Profile(
            name="full",
            job="Complete catalog: every tool, including order tools (still gated).",
        ),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            readonly=True,
        ),
        Profile(
            name="markets",
            job=(
                "Research markets: series, events and markets, prices, order books, "
                "candlesticks, trades, settlement rules, live data and historical "
                "market data. No account data, no orders."
            ),
            tools=MARKETS_TOOLS,
        ),
        Profile(
            name="portfolio",
            job=(
                "Review my account: balance, positions, fills, settlements, orders "
                "and order groups, queue positions, subaccounts, transfers, deposits "
                "and withdrawals, historical fills, orders and positions, API limits. "
                "No order writes."
            ),
            tools=PORTFOLIO_TOOLS,
        ),
    )
}


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(
            f"Profile {profile.name!r} allowlists tools not in the full catalog: "
            f"{', '.join(unknown)}."
        )
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    Tool Search's ``search_tools`` and Code Mode's ``search`` / ``get_schema``
    ship without annotations. They only read the catalog, so they stay visible
    under a read-only listing. ``call_tool`` and ``execute`` are not annotated:
    a write reached through them is still refused by the handler gate.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
