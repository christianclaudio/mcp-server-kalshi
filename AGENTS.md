# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Kalshi prediction market capabilities.

---

## 🎯 Project Overview & Fork Purpose

This is `mcp-server-kalshi` — an enterprise Model Context Protocol (MCP) server exposing the [Kalshi](https://kalshi.com) prediction-market Trade API v2 as MCP tools.

**Lineage & Purpose**:
- **Upstream Origin**: Maintained by christianclaudio as an enterprise-hardened fork of `9crusher/mcp-server-kalshi`.
- **Primary Function**: Built for deep end-to-end trading workflows (market discovery → candidate research → legal settlement rules extraction → order simulation/placement).
- **Core Stack**: Python 3.10+, `uv`, `fastmcp>=4.0.11` + `mcp>=2.2.0` (FastMCP 4 engine, lifespan management, host & origin protection, Spec 2026-07-28), `httpx` (async connection pooling & retry engine), `pydantic` v2, and `cryptography` (RSA-PSS and Ed25519 signing).
- **Distribution**: Install from git or `ghcr.io/christianclaudio/mcp-server-kalshi`. This fork does not publish to public PyPI or the MCP Registry. Upstream owns the public package name `mcp-server-kalshi` and registry id `io.github.9crusher/mcp-server-kalshi`.

---

## 🏗️ Architecture Blueprint

Data flows: request → Pydantic schema validation → API client → FastMCP 4 / low-level MCP server.

Key paths:
- `src/mcp_server_kalshi/server.py` — `ToolRegistry`, every tool handler, FastMCP 4 engine, lifespan, streamable HTTP bridge.
- `src/mcp_server_kalshi/kalshi_client/` — `base.py` (async httpx `BaseAPIClient`, `KalshiAuth` RSA-PSS/Ed25519 signing, `KalshiAPIError`), `client.py` (`KalshiAPIClient` endpoints + `build_*_order_payload` translators), `schemas.py` (Pydantic request models), `pdf.py` (contract-terms PDF text).
- `src/mcp_server_kalshi/config.py` — Pydantic Settings; safety default `KALSHI_ENV=demo`. `ssrf.py` — allowlist and private-range checks for API base and PDF URLs.
- `scripts/check_tool_contract.py` — source of truth for the expected tool set and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after the build and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `tests/` — unit tests are offline; live network tests live in `tests/test_e2e_live.py` (marked `pytest.mark.e2e`, deselected by the default pytest `addopts` `-m 'not e2e'` and in CI; run via `-m e2e`). Signing format in `tests/test_auth.py`, order translation and confirm gate in `tests/test_orders.py`. Version and release-tooling tests are `tests/test_version.py`, `tests/test_release_notes.py`, and `tests/test_check_version.py`. `TESTING.md` covers test conventions.
- `.github/workflows/` — `ci.yml`, `release.yml` (on a `v*` tag: build with full history, check the wheel version matches the tag, build the release notes, create the GitHub Release from `scripts/release_notes.py` with the wheel, sdist, and SBOM attached, then push the GHCR image only after that job succeeds; PyPI and MCP Registry publishing stay commented out), `kalshi-drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (fork catalog metadata, not published to the registry; commits version `0.0.0` and one OCI package, the GHCR image, whose tag lives in `identifier`), `Dockerfile` (the runtime image carries `LABEL io.modelcontextprotocol.server.name`, equal to the `server.json` `name`), `fastmcp.json`, `pyproject.toml` (hatchling + uv-dynamic-versioning; console script `mcp-server-kalshi`).

Tool registration uses an explicit registry pattern: `@ToolRegistry.register_tool(name=..., description=..., input_schema=..., read_only=..., destructive=..., idempotent=..., open_world=...)`. Every tool sets all four hints; `scripts/check_tool_contract.py` asserts them. The server decorates low-level MCP handlers (`list_tools`, `call_tool`) serving the collected registry.

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
- Register the handler using `@ToolRegistry.register_tool(name=..., description=..., input_schema=..., read_only=..., destructive=..., idempotent=..., open_world=...)`. `idempotent` and `open_world` default to `None`, so pass them explicitly.
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
   - Mutating order tools (`create_order`, `amend_order`, `decrease_order`, `cancel_order`, `batch_create_orders`, `batch_cancel_orders`, `cancel_order_group`) return a *preview* (human summary + exact payload) and send **nothing** unless `confirm=True`.
3. **Demo by Default**:
   - `KALSHI_ENV` defaults to `demo` (sandbox). Real money (`prod`) requires explicit opt-in. Every order response includes `settings.env_label`.
4. **Request Signing (RSA-PSS and Ed25519)**:
   - `KalshiAuth` signs `timestamp_ms + METHOD + path`, where `path` includes `/trade-api/v2` but **strictly excludes the query string**.
   - The key type comes from the parsed key object, not the PEM banner. Ed25519 signs that message directly (RFC 8032) and the signature is base64. RSA stays RSA-PSS with SHA-256, MGF1-SHA256, and a salt length equal to the digest length, then base64. Never alter the signed payload format without updating `tests/test_auth.py`.
5. **Public vs. Authenticated Separation**:
   - Market discovery, legal rules, and candidate search work without credentials. Portfolio/order endpoints call `self._require_auth()` and fail-closed when keys are absent.
6. **Secret Redaction**:
   - Credentials, private keys, and session tokens must never appear in logs or error traces.
7. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry validation (longer strings trigger HTTP 422).
8. **Git Safety & Releases**:
   - Never commit private keys or API credentials. All changes proceed via feature branches and PRs.
   - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata` (in `src/mcp_server_kalshi/__init__.py`, reused by `server.py`), and `server.json` commits `0.0.0` (the version and the image tag in `identifier`). PRs never edit a version: no bump in `pyproject.toml`, `src/mcp_server_kalshi/__init__.py`, `server.json`, `uv.lock`, the skill files, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after the build.
   - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) carries a `BREAKING CHANGE:` footer as the final paragraph of the PR body, and the footer text must include the migration steps. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
   - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
   - **`CHANGELOG.md` is frozen** as of 0.2.7. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
   - `skills/kalshi-mcp/SKILL.md` and `.agents/skills/kalshi/SKILL.md` carry no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skills only when their operator guidance changes.
   - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from git+https://github.com/christianclaudio/mcp-server-kalshi mcp-server-kalshi`, the GHCR image `:latest`) or point readers to GitHub Releases.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies (matches the locked CI resolve)
uv sync --locked --extra dev

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

CI is defined in `.github/workflows/ci.yml` (Ruff lint and format, Black, mypy, tests on Python 3.10–3.13 at 100% coverage, tool contract, OpenAPI drift, build + `scripts/check_version.py` + `twine check`, conformance) and installs with `uv sync --locked --all-extras`. CI also runs `uv run black --check src tests`, which the command list above omits. Jobs that install or build the package check out with `fetch-depth: 0`, because a shallow checkout has no reachable tag and reports `0.0.1.devN`. The Docker build context has no `.git`, so `release.yml` passes the tag version as `UV_DYNAMIC_VERSIONING_BYPASS`. Run all of these before opening a PR. Scheduled drift checks live in `kalshi-drift-monitor.yml`: it captures the drift check's exit code explicitly, and on a non-zero exit opens a `forge-todo` issue (titled as drift only when the check reported drift; otherwise "drift monitor failed") and fails the run.

A `v*` tag runs `.github/workflows/release.yml`: build the wheel with full history, check the wheel's version matches the tag, `twine check`, write a CycloneDX SBOM, build the release notes, create the GitHub Release (notes, wheel, sdist, SBOM), and only then publish the Docker image to `ghcr.io/christianclaudio/mcp-server-kalshi` (`:X.Y.Z` and `:latest`). The PyPI step and the MCP Registry job stay commented out: upstream owns the public PyPI name `mcp-server-kalshi` and the registry id `io.github.9crusher/mcp-server-kalshi`. There is no `deploy.yml`. Do not publish this fork to public PyPI or the MCP Registry.

Do not create tags or releases unless the maintainer asks. There is no release PR: merged commits accumulate on `main`, and releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag:

- The release owner previews the release body on an up-to-date `main` with full history and tags (`git fetch --tags && python3 scripts/release_notes.py`) and posts it with the release Ask.
- The reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), that every breaking commit carries its footer with migration steps, and that the version is unused in the places this fork ships:
  ```bash
  git ls-remote --tags origin vX.Y.Z                      # prints nothing
  gh release view vX.Y.Z --repo christianclaudio/mcp-server-kalshi   # fails: release not found
  TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:christianclaudio/mcp-server-kalshi:pull" | jq -r .token)
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
    -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    https://ghcr.io/v2/christianclaudio/mcp-server-kalshi/manifests/X.Y.Z   # prints 404
  ```
  In a throwaway clone, the reviewer tags the release commit locally, runs `rm -rf dist && uv build`, and confirms the wheel is `mcp_server_kalshi-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; then discards the clone without pushing.
- Only the maintainer's go creates the tag. If a release fails after the GitHub Release is created or the image is pushed, do not re-run it; merge a fix and tag the next patch.
