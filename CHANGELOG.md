# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.5] - 2026-10-01

Proposed fork release. Not tagged. Flat tool names are unchanged.

### Changed
- Raised the FastMCP floor from `>=4.0.5` to `>=4.0.10`. The lockfile resolves FastMCP 4.0.10.
- CI and the drift monitor install with `uv sync --locked --all-extras`.
- Install docs point at git and `ghcr.io/christianclaudio/mcp-server-kalshi`. Removed the README link to `COOKBOOK.md` (that file is not in this repo).

### Fixed
- Merged the upstream error-handling and OpenAPI drift commits from `9crusher/mcp-server-kalshi` (their #14), including the `ClientCall.path` alias.

### Removed
- Public PyPI and MCP Registry publish from this fork. `release.yml` keeps build, twine check, CycloneDX SBOM, and GHCR image publish. The PyPI and `mcp-publisher` job is gated with `if: false`. Removed `.github/workflows/deploy.yml`.

## [0.2.4] - 2026-08-28

### Added
- **MCP 2.0 Behavioral Annotations**: Added `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` across all 36 registered tools.
- **Batch Order Placement & Cancellation**: Added `batch_create_orders` (up to 20 limit orders in atomic call with `confirm: bool = False` safety gating) and `batch_cancel_orders`.
- **Search & Filter Taxonomies**: Added `get_tags_by_categories` and `get_sports_filters`.
- **Milestones & Live Game Feeds**: Added `get_milestones`, `get_milestone`, and `get_event_live_data`.
- **Multivariate / Combos & Parlays**: Added `list_multivariate_collections` and `get_multivariate_collection`.
- **Order Groups & Risk Mitigation**: Added `list_order_groups`, `cancel_order_group`, and `get_portfolio_summary`.
- **Secret Scrubbing (`errors.py`)**: Added regex-based credential and RSA private key redaction in error traces.
- **Read-Only Mode**: Added `KALSHI_READONLY=1` environment toggle filtering mutating tools at startup.
- **Contract & Drift Verification**: Added `scripts/check_tool_contract.py`, `scripts/check_openapi_drift.py`, and `scripts/smoke_test.py`.
- **Companion Agent Skill**: Added `.agents/skills/kalshi/SKILL.md` with client configurations, safety runbooks, and composite trading recipes.
- **Governance & CI/CD**: Added `COOKBOOK.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `TESTING.md`, and multi-channel GitHub release workflows.

### Changed
- Enforced `mypy --strict` with zero type errors across all modules.
- Reached 100.0% statement line and branch test coverage.
- Sanitized all URL path parameters using `urllib.parse.quote(..., safe="")`.
- Enhanced `config.py` with `KALSHI_API_KEY_ID` alias compatibility.
