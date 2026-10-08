# 🤝 Contributing to mcp-server-kalshi

Welcome! We are thrilled that you want to contribute to `mcp-server-kalshi`! 🚀  
Whether you are fixing a bug, adding support for new Kalshi V2 endpoints, polishing legal rules extractors, or expanding test coverage, your help is warmly appreciated.

---

## 🌟 Quickstart Development Setup

Get your local dev environment up and running in 30 seconds with `uv`:

```bash
# 1. Clone & enter repository
git clone https://github.com/christianclaudio/mcp-server-kalshi.git
cd mcp-server-kalshi

# 2. Sync virtual environment and dev dependencies
uv sync --locked --all-extras
```

---

## 🧪 Running Tests (No Kalshi Keys Needed!)

You don't need live Kalshi exchange credentials to develop or test! Our comprehensive unit test suite mocks the HTTP layer and cryptographic handshakes:

```bash
# Run unit & safety tests with 100% coverage requirement
uv run pytest --cov=src/mcp_server_kalshi --cov-fail-under=100 -v
```

---

## 🔍 Verification Scripts

Before submitting your pull request, run our verification gates:

### 1. Tool Contract Validation
Asserts registered tool counts, input schemas, and MCP tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`):
```bash
uv run python scripts/check_tool_contract.py
```

### 2. OpenAPI Drift Check
Guards against broken or altered API paths by comparing `client.py` against official Kalshi OpenAPI specs:
```bash
uv run python scripts/check_openapi_drift.py
```

### 3. Packaged stdio smoke test
This fork is not on public PyPI. `scripts/smoke_test_uvx.py` launches a packaged server, completes the JSON-RPC `initialize` handshake, and checks `tools/list`. Build a local wheel, then point `uvx` at that wheel (or install from git):
```bash
uv run python -m build
uv run python scripts/smoke_test_uvx.py -- uvx --from dist/mcp_server_kalshi-*.whl mcp-server-kalshi
# or: uvx --from git+https://github.com/christianclaudio/mcp-server-kalshi mcp-server-kalshi
```

### 4. Code Formatting & Static Analysis
```bash
uv run ruff check .
uv run black --check src tests
uv run mypy
```

---

## 🛠️ How to Add a New Kalshi MCP Tool

Adding a tool takes 4 steps:

1. **Add Request Schema** (`src/mcp_server_kalshi/kalshi_client/schemas.py`):
   ```python
   class GetSomethingRequest(MCPSchemaBaseModel):
       item_id: str = Field(..., description="Unique item identifier.")
   ```

2. **Add Client Method** (`src/mcp_server_kalshi/kalshi_client/client.py`):
   ```python
   async def get_something(self, item_id: str) -> Any:
       quoted = urllib.parse.quote(item_id, safe="")
       return await self.get(f"/something/{quoted}")
   ```

3. **Register Server Tool** (`src/mcp_server_kalshi/server.py`):
   ```python
   @ToolRegistry.register_tool(
       name="get_something",
       description="Get something by ID.",
       input_schema=GetSomethingRequest,
       read_only=True,
       destructive=False,
       idempotent=True,
       open_world=False,
   )
   async def handle_get_something(request: dict[str, Any]) -> Any:
       req = GetSomethingRequest(**request)
       return await kalshi_client.get_something(req.item_id)
   ```

4. **Add Contract Entry** (`scripts/check_tool_contract.py`): add the tool to `EXPECTED_ANNOTATIONS` with its four hints (see Tool Annotations below).

---

## 🏷️ Tool Annotations

Every tool passes all four hints to `@ToolRegistry.register_tool`: `read_only`, `destructive`, `idempotent`, and `open_world`. `idempotent` and `open_world` default to `None` (the hint is omitted), so set them explicitly. `scripts/check_tool_contract.py` asserts the expected `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` for every registered tool (step 4 above).

---

## 🔀 Pull Requests, Versions & Git History

- **Conventional Commits**: Format commit messages as `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`.
- **Squash Merging**: PRs are squash-merged into `main` with clean commit titles. The squash commit message is the PR body.
- **Versions**: Do not edit version numbers or `CHANGELOG.md`. The git tag is the version, and GitHub Releases are the changelog.
- **Breaking changes**: every breaking PR (any `type!:` title, such as `feat!:` or `fix!:`) carries a `BREAKING CHANGE:` footer, with the migration steps, as the final paragraph of the PR body before CodeRabbit's generated summary. `scripts/release_notes.py` stops at the CodeRabbit marker line and ignores everything after it, so a footer inside that summary never reaches the release notes.
