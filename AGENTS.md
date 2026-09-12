# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Kalshi prediction market capabilities.

---

## 🎯 Project Overview & Fork Purpose

This is `mcp-server-kalshi` — an enterprise Model Context Protocol (MCP) server exposing the [Kalshi](https://kalshi.com) prediction-market Trade API v2 as MCP tools.

**Lineage & Purpose**:
- **Upstream Origin**: Maintained by christianclaudio as an enterprise-hardened fork of `9crusher/mcp-server-kalshi`.
- **Primary Function**: Built for deep end-to-end trading workflows (market discovery → candidate research → legal settlement rules extraction → order simulation/placement).
- **Core Stack**: Python 3.10+, `uv`, official `mcp` low-level `Server`, `httpx` (async connection pooling & retry engine), `pydantic` v2, and `cryptography` (RSA-PSS signing).

---

## 🏗️ Architecture Blueprint

Data flows: request → Pydantic schema validation → API client → low-level MCP server:

```
mcp-server-kalshi/
├── src/mcp_server_kalshi/
│   ├── __init__.py               # Package version (__version__) and public exports
│   ├── config.py                 # Pydantic Settings (env/.env). Safety default: KALSHI_ENV=demo
│   ├── server.py                 # ToolRegistry (@ToolRegistry.register_tool), MCP Server handlers, instructions
│   ├── kalshi_client/
│   │   ├── __init__.py           # Client module exports
│   │   ├── base.py               # BaseAPIClient (async httpx) + KalshiAuth (RSA-PSS signing) + KalshiAPIError
│   │   ├── client.py             # KalshiAPIClient: endpoint methods + build_*_order_payload translators
│   │   ├── schemas.py            # Pydantic request models extending MCPSchemaBaseModel / _Paginated
│   │   └── pdf.py                # fetch_pdf_text() — download and extract contract-terms PDF
├── scripts/
│   ├── check_tool_contract.py    # AST/reflection contract verifying 36 tools & MCP 2.0 annotations
│   └── check_openapi_drift.py    # AST visitor checking client methods against upstream Kalshi OpenAPI spec
├── tests/
│   ├── conftest.py               # Shared fixtures and mock HTTP transports (offline only)
│   ├── test_auth.py              # RSA-PSS signing format and query string exclusion tests
│   ├── test_client_endpoints.py  # Unit tests for API client methods
│   ├── test_orders.py            # Order translation, inversion, and confirm-gate tests
│   ├── test_handlers.py          # Tool handler execution tests
│   ├── test_protocol.py          # Wire-level stdio & stateless streamable HTTP protocol verification
│   └── test_server_comprehensive.py # Comprehensive MCP server lifecycle and registry tests
├── .github/workflows/
│   ├── ci.yml                    # Multi-job matrix: lint, py3.10-3.13 tests, contracts, CodeQL, docker build
│   ├── release.yml               # Automated release on v* tags: PyPI wheel/sdist, CycloneDX SBOM, GHCR docker
│   └── drift-monitor.yml         # Scheduled upstream schema & parameter drift check
├── Dockerfile                    # Multi-stage container build running as non-root USER mcp
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI, dependency pinning
├── AGENTS.md                     # Agent guidance map, gotchas, and conventions (this file)
└── README.md                     # User-facing installation, quickstart, and tool index
```

Tool registration uses an explicit registry pattern: `@ToolRegistry.register_tool(name=..., description=..., input_schema=..., read_only=..., destructive=...)`. The server decorates low-level MCP handlers (`list_tools`, `call_tool`) serving the collected registry.

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API endpoint or Kalshi documentation into an MCP tool, follow these 4 steps in exact order:

### 1. Pydantic Request Schema (`kalshi_client/schemas.py`)
- Add a request model extending `MCPSchemaBaseModel` (or `_Paginated` if endpoint paginates).
- Every field must use `Field(...)` with a clear description and accurate optionality (`Optional[...] = Field(default=None, ...)`).
- Use `Literal[...]` for enums. Field docs become parameter documentation in the generated MCP `inputSchema`.

### 2. Client Method (`kalshi_client/client.py`)
- Add an `async def` method on `KalshiAPIClient` calling `await self.get/post/delete(path, params=/json=)`.
- URL path parameters **must** be safely formatted.
- Add `self._require_auth()` as the first line for any portfolio or order endpoint.

### 3. Tool Handler & Registration (`server.py`)
- Register the handler using `@ToolRegistry.register_tool(name=..., description=..., input_schema=..., read_only=..., destructive=...)`.
- Validate arguments with `Model(**request)` (or `_params(request, Model)` for query GETs) and call the client.
- Set `read_only=False, destructive=True` for anything that places, amends, or cancels orders.

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `httpx.MockTransport`.
- **Zero live network calls during tests.** Keep tests 100% offline.
- Run `uv run python scripts/check_tool_contract.py` and update counts if adding tools.
- Ensure test coverage remains at **100.0%**.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Cents ↔ YES-Leg Model**:
   - Tools expose intuitive `action` (buy/sell) + `side` (yes/no) + whole-**cents** `limit_price`.
   - Kalshi V2 quotes everything from the YES leg as `bid`/`ask` in fixed-point dollars.
   - `build_create_order_payload` and `build_amend_order_payload` perform this translation, including the inversion:
     $$\text{buy-NO @ } p \equiv \text{sell-YES @ } (100 - p)$$
   - Never reimplement this inline — always reuse the central builders.
2. **Mandatory `confirm=true` Gate**:
   - Mutating order tools (`create_order`, `amend_order`, `batch_create_orders`) return a *preview* (human summary + exact payload) and place **nothing** unless `confirm=True`.
3. **Demo by Default**:
   - `KALSHI_ENV` defaults to `demo` (sandbox). Real money (`prod`) requires explicit opt-in. Every order response includes `settings.env_label`.
4. **RSA-PSS Auth Signing**:
   - `KalshiAuth` signs `timestamp_ms + METHOD + path`, where `path` includes `/trade-api/v2` but **strictly excludes the query string**.
   - Uses RSA-PSS, MGF1-SHA256, max salt length. Never alter signed payload format without updating `tests/test_auth.py`.
5. **Public vs. Authenticated Separation**:
   - Market discovery, legal rules, and candidate search work without credentials. Portfolio/order endpoints call `self._require_auth()` and fail-closed when keys are absent.
6. **Secret Redaction**:
   - Credentials, private keys, and session tokens must never appear in logs or error traces.
7. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry validation (longer strings trigger HTTP 422).
8. **Git Safety**:
   - Never commit private keys or API credentials. All changes proceed via feature branches and PRs.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies
uv sync --extra dev

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy

# Test suite with 100% coverage requirement
uv run pytest --cov=src/mcp_server_kalshi --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI/CD Matrix & Operational Release SOP

The GitHub Actions CI matrix enforces:
- Ruff lint & format checks.
- Mypy type checks.
- Python 3.10, 3.11, 3.12, 3.13 test matrix with 100% coverage.
- Tool contract & OpenAPI drift validation.
- Multi-stage Docker image build.
- CodeQL security scan.

For release automation and packaging, push matching `v*` tags aligned with `pyproject.toml`'s `project.version` to trigger `.github/workflows/release.yml`.
