# AGENTS.md

Instructions for AI coding agents working on `mcp-server-kalshi` or integrating Kalshi prediction market capabilities.

---

## 🎯 Project Overview & Fork Purpose

`mcp-server-kalshi` is an enterprise-hardened fork of `9crusher/mcp-server-kalshi` exposing the [Kalshi](https://kalshi.com) Trade API v2 as MCP tools (Python 3.11+, `uv >= 0.13.4`, `fastmcp>=4.2.4`, `mcp>=2.3.0`, Spec 2026-07-28, `httpx`, `pydantic` v2, `cryptography` RSA-PSS/Ed25519).

- **Distribution (NO public PyPI / MCP Registry publish):** install from git or `ghcr.io/christianclaudio/mcp-server-kalshi`. Upstream owns `mcp-server-kalshi` on PyPI and `io.github.9crusher/mcp-server-kalshi` in the MCP Registry — never publish this fork there.

---

## 🏗️ Architecture Blueprint

Data flow: request → Pydantic schema validation → API client → FastMCP 4 / low-level MCP server (`list_tools`, `call_tool`).

- `src/mcp_server_kalshi/server.py` — `ToolRegistry` (`@ToolRegistry.register_tool(name=..., description=..., input_schema=..., read_only=..., destructive=..., idempotent=..., open_world=...)` with all 4 hints explicit), tool handlers, FastMCP 4 engine, lifespan, Streamable HTTP bridge.
- `src/mcp_server_kalshi/kalshi_client/` — `base.py` (`BaseAPIClient`, `KalshiAuth` RSA-PSS/Ed25519 signing, `KalshiAPIError`), `client.py` (`KalshiAPIClient` + `build_*_order_payload` translators), `schemas.py` (`MCPSchemaBaseModel`, `_Paginated`), `pdf.py` (contract-terms PDF text).
- `src/mcp_server_kalshi/profiles.py` — `full` (default, 101 tools), `readonly` (`ReadOnlyToolFilter`, 94), and job allowlists `markets` (44) and `portfolio` (31). Allowlists filter tool names only (FastMCP disable-all-tools then enable the named ones; prompts and resources stay). No domain namespaces. 30 tools stay `full`-only. Tool Search (`KALSHI_MCP_ENABLE_TOOL_SEARCH`, `regex`/`bm25`) and Code Mode (`KALSHI_MCP_ENABLE_CODE_MODE`, `code-mode` extra) attach on `full` only and are mutually exclusive. With `KALSHI_READONLY=1`, the gate composes with any profile (a job profile lists the same tools, all read-only); `search_tools`/`search`/`get_schema` stay listed (annotated read-only), `call_tool`/`execute` stay callable for read-only tools, and `ToolRegistry.get_handler` refuses any write tool. `get_event_forecast_percentile_history` is the only `markets` tool that needs a key and is refused locally without one.
- `src/mcp_server_kalshi/config.py` — Pydantic Settings; safety default `KALSHI_ENV=demo` + `KALSHI_MCP_PROFILE` / discovery flags.
- `src/mcp_server_kalshi/ssrf.py` — allowlist and private-range checks for API base, PDF, and OpenAPI spec URLs (`check_openapi_drift.py` fetches only `https://docs.kalshi.com` after DNS check). **`SSRFSafeAsyncTransport`** connects only to a checked address, trying the next while the request's connect timeout lasts (one deadline shared by all addresses), and keeps the hostname for the Host header, TLS SNI, certificate verification, and per-hostname pooling; **proxy exception:** the API client honors `HTTP(S)_PROXY`/`ALL_PROXY` via `ssrf.environment_proxy_for` (running hostname checks first, then httpx's proxied transport), while PDF and drift fetches always connect directly pinned (`trust_env=False`).
- `src/mcp_server_kalshi/auth.py` — `KALSHI_MCP_AUTH_TOKEN` bearer verifier (`SharedTokenVerifier`, attached in `server.py`) and `KALSHI_MCP_ALLOW_UNAUTHENTICATED_BIND` opt-in; `main()` exits 2 on tokenless non-localhost HTTP bind.
- `scripts/check_tool_contract.py` — source of truth (`EXPECTED_ANNOTATIONS`, expected count; never hard-code elsewhere).
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/release_notes.py`, `scripts/check_version.py`, `scripts/setup_repo_governance.sh` (required checks list; edit only, never run from CI).
- `tests/` — 100% offline suite (`addopts` uses `-m 'not live'`). Live checks (`tests/test_live.py`, `-m live`): public tools ungated; `@pytest.mark.live(authenticated=True)` skips unless `KALSHI_API_KEY_ID`/`KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH`/`KALSHI_PRIVATE_KEY` are set. Key modules: `test_auth.py`, `test_orders.py`, `test_otel_spans.py`, `test_version.py`, `test_release_notes.py`, `test_check_version.py`; `tests/conftest.py` clears HTTP auth env & cached auth (`TESTING.md` documents conventions).
- `.github/workflows/` — `ci.yml`, `release.yml`, `dependabot-automerge.yml` (no `deploy.yml`).
- `server.json` (fork metadata only, commits `0.0.0`, one OCI GHCR package in `identifier`), `Dockerfile` (`LABEL io.modelcontextprotocol.server.name` matching `server.json` `name`), `fastmcp.json`, `pyproject.toml`.

---

## ⚡ Canonical Workflow: Building Tools from API / `llms.txt`

Follow in exact order:
1. **Pydantic request schema (`kalshi_client/schemas.py`):** extend `MCPSchemaBaseModel` (or `_Paginated`), document every field with `Field(...)` and accurate optionality (`X | None = Field(default=None, ...)`), use `Literal[...]` for enums.
2. **Client method (`kalshi_client/client.py`):** add `async def` on `KalshiAPIClient` calling `await self.get/post/delete(...)` with escaped URL path params; place `self._require_auth()` as first line on any portfolio or order endpoint.
3. **Tool handler & registration (`server.py`):** register via `@ToolRegistry.register_tool(...)` passing `idempotent` and `open_world` explicitly (both default `None`), validate with `Model(**request)` (or `_params(request, Model)`), and set `read_only=False, destructive=True` on anything placing, amending, or canceling orders.
4. **Offline tests & contract sync (`tests/`):** mock HTTP with `httpx.MockTransport` (**zero live network calls**), add tool to `EXPECTED_ANNOTATIONS` in `scripts/check_tool_contract.py`, and keep **100.0%** coverage.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Cents ↔ YES-Leg Model:** tools expose `action` (`buy`/`sell`) + `side` (`yes`/`no`) + whole-cents `limit_price`; Kalshi v2 quotes YES-leg `bid`/`ask` in fixed-point dollars. Always reuse `build_create_order_payload` and `build_amend_order_payload` (`buy-NO @ p ≡ sell-YES @ (100 - p)`), never reimplement inline.
2. **Mandatory `confirm=True` Gate:** all 7 mutating order tools (`create_order`, `amend_order`, `decrease_order`, `cancel_order`, `batch_create_orders`, `batch_cancel_orders`, `cancel_order_group`) return a preview without sending anything unless `confirm=True`.
3. **Demo by Default:** `KALSHI_ENV` defaults to `demo` (`prod` requires explicit opt-in); order responses include `settings.env_label`.
4. **Request Signing (RSA-PSS & Ed25519):** `KalshiAuth` signs `timestamp_ms + METHOD + path` (including `/trade-api/v2`, **strictly excluding query string**). Key type comes from the parsed key object, not PEM banner (Ed25519 RFC 8032; RSA-PSS SHA-256 / MGF1-SHA256 / salt len = digest len). Never alter signing format without updating `tests/test_auth.py`.
5. **Public vs. Authenticated Separation:** market discovery, legal rules, and candidate search work unauthenticated; portfolio/order endpoints fail closed via `self._require_auth()`.
6. **Secret Redaction:** `errors.py` follows template v1.6.0 rules (masks credentials/keys/tokens/PEM blocks with `[REDACTED]`, preserves `next_token`/`page_token`/`max_tokens`, adds Kalshi signing headers & any-length `Bearer` fail-closed). `KalshiFastMCPTool.run` (HTTP) and `handle_call_tool` (stdio) return `isError: true` with `redact_message(str(exc))` keeping JSON shape (see `errors.py` for known multiline/bracket limits).
7. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.12-slim-trixie AS builder` → runtime pinned by digest (both stages share the same pin; glibc is keyed by the Debian release in the tag, not the digest), non-root `USER mcp`, `LABEL io.modelcontextprotocol.server.name` matching `server.json` `name`.
   - Trust-store floor (FastMCP Oct 2026 notice): both stages upgrade OS `ca-certificates` and fail below `20250419+deb13u1` (`ARG CA_CERTIFICATES_FLOOR`, before pip runs in builder), builder bootstraps pip from `docker/pip-bootstrap.txt` (`--require-hashes`; Dependabot bumps it), `release.yml` upgrades to `pip>=26.2.2`, and `certifi>=2026.10.23` is a direct dependency. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg` (the build-arg exists only so CI can raise it for the negative floor check; CI and the release workflow never pass it otherwise).
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv; Dependabot bumps it), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt` and installs that file with pip in hash-checking mode, then installs `docker/build-backend.txt` (`--require-hashes --only-binary=:all:` outside venv), and runs `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
8. **Registry Metadata (`server.json`):** root `description` **≤ 100 chars**.
9. **Git Safety & Releases:**
   - Never commit private keys or API credentials; work via feature branches and PRs.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` declares `dynamic = ["version"]`, `__version__` uses `importlib.metadata` (`src/mcp_server_kalshi/__init__.py`), `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`). Untagged builds report `X.Y.(Z+1).devN+<sha>`; `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** carry `BREAKING CHANGE:` as the final PR body paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) and **must include migration steps** (`scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge.
   - **`CHANGELOG.md` frozen at 0.2.7:** GitHub Releases are the changelog. Keep `README.md` unpinned (`uvx --from git+https://github.com/christianclaudio/mcp-server-kalshi mcp-server-kalshi`, GHCR `:latest`).
   - **Version level:** before tagging, check the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), and that every breaking commit carries its footer with migration steps.
   - **Tagging (maintainer go only, no release PR):** preview `git fetch --tags && python3 scripts/release_notes.py` on `main` and post it with the release Ask; verify tag/version unused on git tags, GitHub Releases, and GHCR manifests (get a pull token from `https://ghcr.io/token?scope=repository:christianclaudio/mcp-server-kalshi:pull`, then `GET https://ghcr.io/v2/christianclaudio/mcp-server-kalshi/manifests/X.Y.Z` with `Authorization: Bearer <token>` and an OCI/Docker manifest `Accept` → 404; without the token GHCR returns 401 either way). In a throwaway clone, tag the release commit locally, run `rm -rf dist && uv build`, and confirm the wheel is `mcp_server_kalshi-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; discard the clone without pushing. On post-release/push failure, merge a fix and tag next patch.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

```bash
# Install editable with dev dependencies (matches the locked CI resolve)
uv sync --locked --extra dev

# Lint and formatting
uv run ruff check . && uv run ruff format --check . && uv run black --check src tests

# Strict type checking
uv run mypy

# Test suite with 100% coverage requirement
uv run pytest --cov=src/mcp_server_kalshi --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Protocol integration tests (stdio handshake, in-memory client, & streamable HTTP)
uv run pytest tests/test_protocol.py

# Protocol conformance verification (@modelcontextprotocol/conformance@0.1.16)
./scripts/check_conformance.sh

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Release notes preview since the last tag (needs full history and tags)
python3 scripts/release_notes.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** installs with `uv sync --locked --all-extras`; runs on `push` (`main`, `feat/*`), `pull_request` (`main`), weekly schedule, and `workflow_dispatch` (`fetch-depth: 0` on package jobs). Jobs: `lint-and-types`, `test` (Python 3.11–3.14, 100% cov) + `tests`, `tool-contract`, `openapi-drift`, `build-check` (`check_version.py` + `twine check`), `docker-build` (no push; verifies stripped build deps, stdio `initialize`/`tools/list`, negative `CA_CERTIFICATES_FLOOR`), `conformance`, and `drift-check` (schedule/manual only; fails on non-zero exit and opens `forge-todo` issue on `main` drift or monitor failure).
- **Dependabot (`dependabot-automerge.yml`):** squash-merges minor/patch PRs (`gh pr merge --squash --match-head-commit`) after CI passes; major & docker updates wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); checks wheel version, `twine check`, CycloneDX SBOM, GitHub Release (`release_notes.py`, wheel, sdist, SBOM), and publishes `ghcr.io/christianclaudio/mcp-server-kalshi` (`:X.Y.Z`, `:latest`, `UV_DYNAMIC_VERSIONING_BYPASS` set to tag version because the Docker build context has no `.git`). PyPI and MCP Registry jobs remain disabled.

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-kalshi
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
