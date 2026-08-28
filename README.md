# MCP Server Kalshi

<!-- mcp-name: io.github.9crusher/mcp-server-kalshi -->

An enterprise MCP server providing AI agents with a first-class interface to [Kalshi](https://kalshi.com) prediction markets. It is built for end-to-end trading: browse markets, research them, read the *exact* settlement rules (including pulling the contract-terms PDFs), manage risk groups, and execute single & batch trades — all through type-safe MCP tools.

---

## 🚀 Highlights (36 Tools)

- **Discovery & Search** — `list_markets`, `get_market`, `list_events`, `get_event`, `list_series`, `get_series`, `get_tags_by_categories`, `get_sports_filters`.
- **Research & Live Feeds** — `get_market_orderbook`, `get_market_candlesticks`, `get_market_trades`, `get_event_live_data`, `get_milestones`, `get_milestone`.
- **Deep Contract Rules** — `get_market_rules` consolidates `rules_primary`/`rules_secondary`, early-close conditions, settlement sources, and series prohibitions; `fetch_rules_pdf` downloads and extracts the exact legal contract terms text.
- **Multivariate & Parlays** — `list_multivariate_collections`, `get_multivariate_collection`.
- **Exchange & Status** — `get_exchange_status`, `get_exchange_schedule`, `get_environment`.
- **Portfolio & Exposure** — `get_balance`, `get_portfolio_summary` (resting order value), `get_positions`, `get_fills`, `get_settlements`.
- **Trading & Execution** — `create_order`, `cancel_order`, `amend_order`, `decrease_order`, `batch_create_orders`, `batch_cancel_orders`, `list_orders`, `get_order`.
- **Risk Management & Order Groups** — `list_order_groups`, `cancel_order_group` (e.g. One-Cancels-Other OCO groups).

---

## 🛡️ Safety by Default

- **Sandbox Default:** The server targets Kalshi's **demo (sandbox)** environment unless `KALSHI_ENV=prod` is explicitly set.
- **Simulation Preview Gating:** Mutating order tools (`create_order`, `amend_order`, `batch_create_orders`) require `confirm=true`. Without it, they return a structured simulation **preview** without placing orders.
- **Read-Only Mode:** Run with `KALSHI_READONLY=1` to restrict registration exclusively to 29 read-only inspection tools.
- **Secret Scrubbing:** RSA private keys and tokens are scrubbed from error logs via `_redact_secrets()`.
- **Intuitive Order Pricing:** Exposes intuitive whole **cents** limit pricing and automatically translates buy-NO ⇄ sell-YES orderbook math.

---

## ⚙️ Configuration

| Variable | Default | Purpose |
| :--- | :---: | :--- |
| `KALSHI_ENV` | `demo` | `demo` (sandbox) or `prod` (real money). |
| `KALSHI_API_KEY` / `KALSHI_API_KEY_ID` | _(none)_ | Kalshi API key ID. Required for portfolio and order tools. |
| `KALSHI_PRIVATE_KEY_PATH` | _(none)_ | Path to your RSA private key `.pem`. |
| `KALSHI_READONLY` | `0` / `false` | When enabled (`1`), disables all mutating order endpoints at startup. |
| `BASE_URL` | _(derived)_ | Optional explicit REST base override (must include `/trade-api/v2`). |

---

## 📦 Client Configuration

### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "kalshi": {
      "command": "uvx",
      "args": ["mcp-server-kalshi"],
      "env": {
        "KALSHI_ENV": "demo",
        "KALSHI_API_KEY": "<YOUR_KALSHI_API_KEY>",
        "KALSHI_PRIVATE_KEY_PATH": "/path/to/kalshi-rsa.pem"
      }
    }
  }
}
```

### Docker
```json
{
  "mcpServers": {
    "kalshi": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
        "-e", "KALSHI_ENV", "-e", "KALSHI_API_KEY", "-e", "KALSHI_PRIVATE_KEY_PATH",
        "ghcr.io/christianclaudio/mcp-server-kalshi:latest"
      ]
    }
  }
}
```

---

## 🧪 Verification & Local Development

```bash
# 1. Install dependencies
uv sync --all-extras

# 2. Run unit & offline tests (100% coverage enforced)
uv run pytest --cov=src/mcp_server_kalshi --cov-fail-under=100

# 3. Static type analysis & linting
uv run mypy --strict
uv run ruff check .
uv run black --check src tests

# 4. Tool contract & OpenAPI drift validation
uv run python scripts/check_tool_contract.py
uv run python scripts/check_openapi_drift.py
uv run python scripts/smoke_test.py
```

---

## 📚 Documentation & Guides

- 📖 [Runbooks & Operational Recipes](COOKBOOK.md)
- 🛡️ [Security Policy & Guidelines](SECURITY.md)
- 🤝 [Contributing Guidelines](CONTRIBUTING.md)
- 🧪 [Testing & Verification Invariants](TESTING.md)
- 📝 [Changelog](CHANGELOG.md)
