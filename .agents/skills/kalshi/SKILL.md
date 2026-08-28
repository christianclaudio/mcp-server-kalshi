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
   - `create_order` and `amend_order` will return a simulation **preview** unless explicitly called with `confirm=True`.
   - Never call `confirm=True` on real money (`PROD`) without explicit user permission.
3. **Read-Only Mode**:
   - When configured with `KALSHI_READONLY=1`, mutating order endpoints (`create_order`, `cancel_order`, `amend_order`, `decrease_order`) are disabled at startup.

---

## 3. Tool Annotations

| Tool Name | Type / Scope | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `list_markets` | Discovery | `true` | `false` | `true` | `false` |
| `get_market` | Discovery | `true` | `false` | `true` | `false` |
| `list_events` | Discovery | `true` | `false` | `true` | `false` |
| `get_event` | Discovery | `true` | `false` | `true` | `false` |
| `list_series` | Discovery | `true` | `false` | `true` | `false` |
| `get_series` | Discovery | `true` | `false` | `true` | `false` |
| `get_market_orderbook` | Research | `true` | `false` | `true` | `false` |
| `get_market_candlesticks`| Research | `true` | `false` | `true` | `false` |
| `get_market_trades` | Research | `true` | `false` | `true` | `false` |
| `get_market_rules` | Rules | `true` | `false` | `true` | `false` |
| `fetch_rules_pdf` | Rules / PDF | `true` | `false` | `true` | `true` |
| `get_environment` | System | `true` | `false` | `true` | `false` |
| `get_exchange_status` | Exchange | `true` | `false` | `true` | `false` |
| `get_exchange_schedule`| Exchange | `true` | `false` | `true` | `false` |
| `get_balance` | Portfolio | `true` | `false` | `true` | `false` |
| `get_positions` | Portfolio | `true` | `false` | `true` | `false` |
| `get_fills` | Portfolio | `true` | `false` | `true` | `false` |
| `get_settlements` | Portfolio | `true` | `false` | `true` | `false` |
| `list_orders` | Orders | `true` | `false` | `true` | `false` |
| `get_order` | Orders | `true` | `false` | `true` | `false` |
| `create_order` | Trading | `false` | `true` | `false` | `false` |
| `cancel_order` | Trading | `false` | `true` | `true` | `false` |
| `amend_order` | Trading | `false` | `true` | `false` | `false` |
| `decrease_order` | Trading | `false` | `true` | `false` | `false` |

---

## 4. Composite Trading Recipes

### Recipe 1: End-to-End Market Diligence & Order Execution
1. Discover open markets: `list_markets(status="open", series_ticker="...")`.
2. Inspect market details: `get_market(ticker="...")`.
3. Check market orderbook depth: `get_market_orderbook(ticker="...", depth=10)`.
4. Read legal settlement terms: `get_market_rules(ticker="...")` and `fetch_rules_pdf(ticker="...")`.
5. Preview order: `create_order(ticker="...", action="buy", side="yes", count=10, limit_price=54, confirm=False)`.
6. Confirm with user and submit: `create_order(..., confirm=True)`.

### Recipe 2: Position Risk & Order Management
1. Check available collateral: `get_balance()`.
2. List open positions: `get_positions()`.
3. List resting limit orders: `list_orders(status="resting")`.
4. Cancel or adjust resting risk: `cancel_order(order_id="...")` or `decrease_order(order_id="...", reduce_by=5)`.
