---
name: kalshi
description: Enterprise Agent Skill for researching, discovery, rules extraction, and executing trades on Kalshi prediction markets via mcp-server-kalshi.
version: 0.2.4
---

# Kalshi Prediction Markets Agent Skill

This skill guides AI agents on interacting with the Kalshi Exchange via `mcp-server-kalshi`.

---

## 1. Client Configuration

### 1.1 Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "kalshi": {
      "command": "uvx",
      "args": ["mcp-server-kalshi"],
      "env": {
        "KALSHI_ENV": "demo",
        "KALSHI_API_KEY": "${KALSHI_API_KEY}",
        "KALSHI_PRIVATE_KEY_PATH": "/path/to/kalshi-rsa.pem"
      }
    }
  }
}
```

### 1.2 Antigravity CLI / AGY (`mcp_config.json`)
```json
{
  "mcpServers": {
    "kalshi": {
      "command": "uvx",
      "args": ["mcp-server-kalshi"],
      "env": {
        "KALSHI_ENV": "demo",
        "KALSHI_API_KEY": "${KALSHI_API_KEY}",
        "KALSHI_PRIVATE_KEY_PATH": "${HOME}/.kalshi/rsa.pem"
      }
    }
  }
}
```

### 1.3 Cortex (`mcp.json`)
```json
{
  "servers": {
    "kalshi": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-kalshi"],
      "env": {
        "KALSHI_ENV": "demo",
        "KALSHI_API_KEY": "${KALSHI_API_KEY}",
        "KALSHI_PRIVATE_KEY_PATH": "${HOME}/.kalshi/rsa.pem"
      }
    }
  }
}
```

---

## 2. Safety & Guardrail Runbook

1. **Environment Awareness**:
   - Always call `get_environment` first to determine whether the server is operating in `DEMO (sandbox)` or `PROD (real money)`.
2. **Order Placement Safety Gate**:
   - `create_order`, `amend_order`, and `batch_create_orders` will return a simulation **preview** unless explicitly called with `confirm=True`.
   - Never call `confirm=True` on real money (`PROD`) without explicit user confirmation.
3. **Read-Only Mode**:
   - When configured with `KALSHI_READONLY=1`, mutating order endpoints (`create_order`, `cancel_order`, `amend_order`, `decrease_order`, `batch_create_orders`, `batch_cancel_orders`, `cancel_order_group`) are disabled at startup.

---

## 3. Tool Annotations (36 Tools)

| Tool Name | Type / Scope | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `list_markets` | Discovery | `true` | `false` | `true` | `false` |
| `get_market` | Discovery | `true` | `false` | `true` | `false` |
| `list_events` | Discovery | `true` | `false` | `true` | `false` |
| `get_event` | Discovery | `true` | `false` | `true` | `false` |
| `list_series` | Discovery | `true` | `false` | `true` | `false` |
| `get_series` | Discovery | `true` | `false` | `true` | `false` |
| `get_tags_by_categories` | Discovery | `true` | `false` | `true` | `false` |
| `get_sports_filters` | Discovery | `true` | `false` | `true` | `false` |
| `get_market_orderbook` | Research | `true` | `false` | `true` | `false` |
| `get_market_candlesticks`| Research | `true` | `false` | `true` | `false` |
| `get_market_trades` | Research | `true` | `false` | `true` | `false` |
| `get_market_rules` | Rules | `true` | `false` | `true` | `false` |
| `fetch_rules_pdf` | Rules / PDF | `true` | `false` | `true` | `true` |
| `get_milestones` | Live / Tracker | `true` | `false` | `true` | `false` |
| `get_milestone` | Live / Tracker | `true` | `false` | `true` | `false` |
| `get_event_live_data` | Live Data | `true` | `false` | `true` | `false` |
| `list_multivariate_collections`| Combos / Parlays | `true` | `false` | `true` | `false` |
| `get_multivariate_collection` | Combos / Parlays | `true` | `false` | `true` | `false` |
| `get_environment` | System | `true` | `false` | `true` | `false` |
| `get_exchange_status` | Exchange | `true` | `false` | `true` | `false` |
| `get_exchange_schedule`| Exchange | `true` | `false` | `true` | `false` |
| `get_balance` | Portfolio | `true` | `false` | `true` | `false` |
| `get_portfolio_summary`| Portfolio | `true` | `false` | `true` | `false` |
| `get_positions` | Portfolio | `true` | `false` | `true` | `false` |
| `get_fills` | Portfolio | `true` | `false` | `true` | `false` |
| `get_settlements` | Portfolio | `true` | `false` | `true` | `false` |
| `list_orders` | Orders | `true` | `false` | `true` | `false` |
| `get_order` | Orders | `true` | `false` | `true` | `false` |
| `create_order` | Trading | `false` | `true` | `false` | `false` |
| `cancel_order` | Trading | `false` | `true` | `true` | `false` |
| `amend_order` | Trading | `false` | `true` | `false` | `false` |
| `decrease_order` | Trading | `false` | `true` | `false` | `false` |
| `batch_create_orders` | Trading | `false` | `true` | `false` | `false` |
| `batch_cancel_orders` | Trading | `false` | `true` | `true` | `false` |
| `list_order_groups` | Risk / Groups | `true` | `false` | `true` | `false` |
| `cancel_order_group` | Risk / Groups | `false` | `true` | `true` | `false` |

---

## 4. Composite Trading Recipes

### Recipe 1: End-to-End Market Diligence & Order Execution
1. Discover open markets: `list_markets(status="open", series_ticker="...")`.
2. Inspect market details: `get_market(ticker="...")`.
3. Check market orderbook depth: `get_market_orderbook(ticker="...", depth=10)`.
4. Read legal settlement terms: `get_market_rules(ticker="...")` and `fetch_rules_pdf(ticker="...")`.
5. Preview order: `create_order(ticker="...", action="buy", side="yes", count=10, limit_price=54, confirm=False)`.
6. Confirm with user and submit: `create_order(..., confirm=True)`.

### Recipe 2: Batch Quoting & Emergency Flattening
1. Generate batch simulation preview (`confirm=False`):
   ```python
   batch_create_orders(orders=[
       {"ticker": "KXNBA-27-PHI", "action": "buy", "side": "yes", "count": 10, "limit_price": 50},
       {"ticker": "KXNBA-27-PHI", "action": "sell", "side": "yes", "count": 10, "limit_price": 55},
   ], confirm=False)
   ```
2. Request explicit user confirmation with the previewed batch summary.
3. Submit confirmed batch orders (`confirm=True`):
   ```python
   batch_create_orders(orders=[...], confirm=True)
   ```
4. Batch cancel resting exposure:
   ```python
   batch_cancel_orders(order_ids=["ord-1", "ord-2", "ord-3"])
   ```

### Recipe 3: Live Score Grounding & In-Game Hedging
1. Query live scoreboard: `get_event_live_data(event_ticker="KXNBA-27")`.
2. Check milestones: `get_milestones(related_event_ticker="KXNBA-27")`.
3. Review total resting order value: `get_portfolio_summary()`.
4. Adjust resting orders dynamically.
