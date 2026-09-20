#!/usr/bin/env python3
"""Tool Contract Verification Script for mcp-server-kalshi.

Asserts tool counts, naming conventions, inputSchema structure, and MCP 2.0
behavioral annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
according to specs/mcp-server-builder-spec.md.
"""

import sys
from typing import Any

from mcp_server_kalshi import server

EXPECTED_ANNOTATIONS: dict[str, dict[str, Any]] = {
    # Discovery
    "list_markets": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_market": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "list_events": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_event": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "list_series": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_series": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    # Research / Rules
    "get_market_orderbook": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_market_candlesticks": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_market_trades": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_market_rules": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "fetch_rules_pdf": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": True,
    },
    # Environment
    "get_environment": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    # Exchange
    "get_exchange_status": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_exchange_schedule": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    # Portfolio
    "get_balance": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_positions": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_fills": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_settlements": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    # Orders / Trading
    "list_orders": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_order": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "create_order": {
        "readOnly": False,
        "destructive": True,
        "idempotent": False,
        "openWorld": False,
    },
    "cancel_order": {
        "readOnly": False,
        "destructive": True,
        "idempotent": True,
        "openWorld": False,
    },
    "amend_order": {
        "readOnly": False,
        "destructive": True,
        "idempotent": False,
        "openWorld": False,
    },
    "decrease_order": {
        "readOnly": False,
        "destructive": True,
        "idempotent": False,
        "openWorld": False,
    },
    # Batch Orders & Groups
    "batch_create_orders": {
        "readOnly": False,
        "destructive": True,
        "idempotent": False,
        "openWorld": False,
    },
    "batch_cancel_orders": {
        "readOnly": False,
        "destructive": True,
        "idempotent": True,
        "openWorld": False,
    },
    "get_portfolio_summary": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_tags_by_categories": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_sports_filters": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_milestones": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_milestone": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_event_live_data": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "list_multivariate_collections": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "get_multivariate_collection": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "list_order_groups": {
        "readOnly": True,
        "destructive": False,
        "idempotent": True,
        "openWorld": False,
    },
    "cancel_order_group": {
        "readOnly": False,
        "destructive": True,
        "idempotent": True,
        "openWorld": False,
    },
}


def verify_tool_contracts() -> int:
    tools = {t.name: t for t in server.ToolRegistry.get_tools()}
    print(f"[*] Found {len(tools)} registered tools in ToolRegistry.")

    if len(tools) != len(EXPECTED_ANNOTATIONS):
        print(
            f"[!] Tool count mismatch: expected {len(EXPECTED_ANNOTATIONS)}, got {len(tools)}"
        )
        return 1

    errors = 0
    for name, expected in EXPECTED_ANNOTATIONS.items():
        if name not in tools:
            print(f"[!] Missing expected tool: {name}")
            errors += 1
            continue

        tool = tools[name]
        # Verify description
        if not tool.description or len(tool.description.strip()) == 0:
            print(f"[!] Tool '{name}' missing description")
            errors += 1

        # Verify inputSchema / input_schema
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        if not schema or schema.get("type") != "object" or "properties" not in schema:
            print(f"[!] Tool '{name}' has invalid inputSchema")
            errors += 1

        # Verify annotations
        ann = tool.annotations
        if ann is None:
            print(f"[!] Tool '{name}' missing ToolAnnotations")
            errors += 1
            continue

        ro = getattr(ann, "read_only_hint", None)
        if ro is None:
            ro = getattr(ann, "readOnlyHint", None)
        if ro != expected["readOnly"]:
            print(
                f"[!] Tool '{name}' readOnlyHint mismatch: expected {expected['readOnly']}, got {ro}"
            )
            errors += 1
        dest = getattr(ann, "destructive_hint", None)
        if dest is None:
            dest = getattr(ann, "destructiveHint", None)
        if dest != expected["destructive"]:
            print(
                f"[!] Tool '{name}' destructiveHint mismatch: expected {expected['destructive']}, got {dest}"
            )
            errors += 1
        idem = getattr(ann, "idempotent_hint", None)
        if idem is None:
            idem = getattr(ann, "idempotentHint", None)
        if idem != expected["idempotent"]:
            print(
                f"[!] Tool '{name}' idempotentHint mismatch: expected {expected['idempotent']}, got {idem}"
            )
            errors += 1
        ow = getattr(ann, "open_world_hint", None)
        if ow is None:
            ow = getattr(ann, "openWorldHint", None)
        if ow != expected["openWorld"]:
            print(
                f"[!] Tool '{name}' openWorldHint mismatch: expected {expected['openWorld']}, got {ow}"
            )
            errors += 1

    if errors == 0:
        print("[✓] All tool contracts and MCP 2.0 annotations verified successfully.")
        return 0
    else:
        print(f"[!] Tool contract verification failed with {errors} error(s).")
        return 1


if __name__ == "__main__":
    sys.exit(verify_tool_contracts())
