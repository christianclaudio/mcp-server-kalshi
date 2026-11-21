"""Profiles, Tool Search, and Code Mode. The default catalog stays the 101 tools."""

from __future__ import annotations

import importlib.util
import json
import logging
from typing import Any

import pytest
from fastmcp.tools.base import Tool

from mcp_server_kalshi.config import Settings
from mcp_server_kalshi.kalshi_client.base import MISSING_CREDENTIALS_ERROR
from mcp_server_kalshi.profiles import (
    FULL_ONLY_TOOLS,
    MARKETS_TOOLS,
    PORTFOLIO_TOOLS,
    PROFILES,
    Profile,
    ReadOnlyAnnotations,
    get_profile,
    is_read_only_tool,
    validate_allowlist,
)
from mcp_server_kalshi.server import (
    CODE_MODE_READ_ONLY_TOOLS,
    TOOL_SEARCH_READ_ONLY_TOOLS,
    ToolRegistry,
    create_server,
)

ORDER = {
    "ticker": "KXTEST",
    "action": "buy",
    "side": "yes",
    "count": 1,
    "limit_price": 50,
    "confirm": False,
}

SYNTHETIC = frozenset({"search", "get_schema", "search_tools"})


def _names(tools: list[Any]) -> set[str]:
    return {tool.name for tool in tools}


def _text(result: Any) -> str:
    content = getattr(result, "content", None)
    if content:
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts)
    data = getattr(result, "structured_content", None)
    if data is None:
        data = getattr(result, "data", None)
    if data is not None:
        return json.dumps(data)
    return str(result)


def _catalog_write_names() -> set[str]:
    """Write tools in the registry, independent of KALSHI_READONLY."""
    names: set[str] = set()
    for name, (tool, _handler) in ToolRegistry._tools.items():
        hint = None if tool.annotations is None else tool.annotations.read_only_hint
        if hint is not True:
            names.add(name)
    return names


async def _search_names(app: Any, query: str) -> set[str]:
    result = await app.call_tool("search_tools", {"pattern": query})
    text = _text(result)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = text
    if isinstance(payload, list):
        return {
            item["name"]
            for item in payload
            if isinstance(item, dict) and "name" in item
        }
    if isinstance(payload, dict):
        tools = payload.get("tools") or payload.get("result") or []
        if isinstance(tools, list):
            return {
                item["name"]
                for item in tools
                if isinstance(item, dict) and "name" in item
            }
    return set()


async def test_full_profile_is_the_unchanged_catalog() -> None:
    app = create_server(profile="full")
    tools = await app.list_tools()
    assert _names(tools) == set(ToolRegistry._tools)
    assert len(tools) == 101
    assert SYNTHETIC.isdisjoint(_names(tools))


async def test_every_catalog_tool_has_an_explicit_read_only_hint() -> None:
    """readOnlyHint is true or false on every one of the 101 tools."""
    tools = await create_server(profile="full").list_tools()
    assert len(tools) == 101
    for tool in tools:
        assert tool.annotations is not None, tool.name
        hint = tool.annotations.read_only_hint
        assert hint is True or hint is False, tool.name


async def test_readonly_filter_get_tool_hides_writes() -> None:
    app = create_server(profile="readonly")
    assert await app.get_tool("list_markets") is not None
    assert await app.get_tool("create_order") is None
    assert await app.get_tool("not-a-real-tool") is None


async def test_readonly_profile_and_gate_list_the_read_only_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gates together still list the annotated read-only tools."""
    monkeypatch.setattr("mcp_server_kalshi.server.settings.KALSHI_READONLY", True)
    app = create_server(profile="readonly")
    names = _names(await app.list_tools())
    assert "list_markets" in names
    assert "create_order" not in names


async def test_readonly_profile_keeps_only_read_only_tools() -> None:
    full = await create_server(profile="full").list_tools()
    readonly = await create_server(profile="readonly").list_tools()
    expected = {tool.name for tool in full if is_read_only_tool(tool)}
    assert _names(readonly) == expected
    assert len(readonly) == 94
    for name in (
        "get_fcm_positions",
        "list_rfqs",
        "get_quote",
        "get_weather_index",
        "list_structured_targets",
        "list_incentive_programs",
        "get_event_metadata",
        "get_order_group",
        "list_intra_exchange_instance_transfers",
        "get_credit_history",
        "get_game_stats",
    ):
        assert name in _names(readonly)
    assert "create_order" not in _names(readonly)
    assert "list_markets" in _names(readonly)


def test_unknown_profile_raises() -> None:
    with pytest.raises(ValueError, match="Unknown profile 'nope'"):
        get_profile("nope")
    with pytest.raises(ValueError, match="valid profiles"):
        create_server(profile="markets-read")


def test_profile_name_is_case_insensitive() -> None:
    assert get_profile(" READONLY ").name == "readonly"
    assert get_profile(" Markets ").name == "markets"
    assert set(PROFILES) == {"full", "readonly", "markets", "portfolio"}
    assert PROFILES["markets"].is_allowlist
    assert PROFILES["portfolio"].is_allowlist
    assert not PROFILES["full"].is_allowlist
    assert not PROFILES["readonly"].is_allowlist


def test_discovery_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full", enable_tool_search=True, enable_code_mode=True)


async def test_search_and_code_mode_attach_only_on_full(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile="readonly", enable_tool_search=True)
        coded = create_server(profile="readonly", enable_code_mode=True)
    flat = await create_server(profile="readonly").list_tools()
    assert _names(await searched.list_tools()) == _names(flat)
    assert _names(await coded.list_tools()) == _names(flat)
    messages = [record.message for record in caplog.records]
    assert any("Tool Search requested with profile='readonly'" in m for m in messages)
    assert any("Code Mode requested with profile='readonly'" in m for m in messages)


async def test_tool_search_regex_and_bm25_on_full() -> None:
    regex = create_server(
        profile="full", enable_tool_search=True, tool_search_backend="regex"
    )
    bm25 = create_server(
        profile="full", enable_tool_search=True, tool_search_backend="bm25"
    )

    def _mro_names(app: Any) -> set[str]:
        names: set[str] = set()
        for item in app.transforms:
            names.update(base.__name__ for base in type(item).__mro__)
        return names

    assert {"RegexSearchTransform", "ReadOnlyAnnotations"} <= _mro_names(regex)
    assert {"BM25SearchTransform", "ReadOnlyAnnotations"} <= _mro_names(bm25)
    listed = _names(await regex.list_tools())
    assert listed == {"search_tools", "call_tool"}
    await regex.call_tool("search_tools", {"pattern": "market"})
    await bm25.call_tool("search_tools", {"query": "market"})
    search = await regex.get_tool("search_tools")
    assert search is not None
    assert search.annotations is not None
    assert search.annotations.read_only_hint is True


async def test_code_mode_attaches_on_full_when_the_sandbox_is_installed() -> None:
    pytest.importorskip("pydantic_monty")
    app = create_server(profile="full", enable_code_mode=True)
    listed = _names(await app.list_tools())
    assert listed == {"search", "get_schema", "execute"}
    search = await app.get_tool("search")
    assert search is not None and search.annotations is not None
    assert search.annotations.read_only_hint is True


async def test_code_mode_skips_without_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True)
    assert _names(await app.list_tools()) == set(ToolRegistry._tools)
    assert any("pydantic-monty" in record.message for record in caplog.records)


def test_create_server_defaults_follow_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted arguments read the process settings."""
    monkeypatch.setattr(
        "mcp_server_kalshi.server.settings",
        Settings(
            _env_file=None,
            KALSHI_MCP_PROFILE="readonly",
            KALSHI_MCP_ENABLE_TOOL_SEARCH=False,
            KALSHI_MCP_ENABLE_CODE_MODE=False,
        ),
    )
    app = create_server()
    assert any(type(item).__name__ == "ReadOnlyToolFilter" for item in app.transforms)


def test_backend_is_normalized_and_unknown_is_rejected() -> None:
    from pydantic import ValidationError

    settings = Settings(_env_file=None, KALSHI_MCP_TOOL_SEARCH_BACKEND=" BM25 ")
    assert settings.KALSHI_MCP_TOOL_SEARCH_BACKEND == "bm25"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, KALSHI_MCP_TOOL_SEARCH_BACKEND="fuzzy")


def test_is_read_only_tool_fails_closed() -> None:
    assert is_read_only_tool(None) is False
    bare = Tool(name="bare", description="d", parameters={"type": "object"})
    assert is_read_only_tool(bare) is False


def test_read_only_annotation_names() -> None:
    assert TOOL_SEARCH_READ_ONLY_TOOLS == ("search_tools",)
    assert CODE_MODE_READ_ONLY_TOOLS == ("search", "get_schema")


async def test_read_only_annotations_pass_a_miss_through() -> None:
    transform = ReadOnlyAnnotations(("search_tools",))

    async def _miss(name: str, *, version: Any = None) -> None:
        del name, version
        return None

    assert await transform.get_tool("missing", _miss) is None


async def test_readonly_gate_with_tool_search_keeps_discovery_and_refuses_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KALSHI_READONLY hides writes on top of Tool Search.

    search_tools and call_tool stay usable. search_tools does not return a
    write tool. A write the proxy or a direct call reaches is refused by
    ToolRegistry.get_handler.
    """
    monkeypatch.setattr("mcp_server_kalshi.server.settings.KALSHI_READONLY", True)
    app = create_server(
        profile="full", enable_tool_search=True, tool_search_backend="regex"
    )
    listed = await app.list_tools()
    assert _names(listed) == {"search_tools", "call_tool"}
    found = await _search_names(app, "order")
    assert found
    assert found.isdisjoint(_catalog_write_names())
    assert "create_order" not in found

    read = await app.call_tool(
        "call_tool", {"name": "get_environment", "arguments": {}}
    )
    assert "read-only mode" not in _text(read).lower()
    assert getattr(read, "is_error", False) is False

    seen: list[str] = []
    real = ToolRegistry.get_handler

    def _spy(name: str) -> Any:
        seen.append(name)
        return real(name)

    monkeypatch.setattr(ToolRegistry, "get_handler", _spy)
    proxied = await app.call_tool(
        "call_tool", {"name": "create_order", "arguments": ORDER}
    )
    direct = await app.call_tool("create_order", ORDER)
    blob = _text(proxied) + "\n" + _text(direct)
    assert "create_order" in seen
    assert "read-only mode" in blob


async def test_readonly_gate_with_code_mode_keeps_discovery_and_refuses_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KALSHI_READONLY keeps Code Mode's discovery and execute tools usable.

    A write reached through execute is refused by ToolRegistry.get_handler.
    """
    pytest.importorskip("pydantic_monty")
    monkeypatch.setattr("mcp_server_kalshi.server.settings.KALSHI_READONLY", True)
    app = create_server(profile="full", enable_code_mode=True)
    listed = _names(await app.list_tools())
    assert listed == {"search", "get_schema", "execute"}
    for name in ("search", "get_schema"):
        tool = await app.get_tool(name)
        assert tool is not None and tool.annotations is not None
        assert tool.annotations.read_only_hint is True

    found = await app.call_tool("search", {"query": "order"})
    assert "create_order" not in _text(found)
    read = await app.call_tool(
        "execute", {"code": "return await call_tool('get_environment', {})"}
    )
    assert getattr(read, "is_error", False) is False

    seen: list[str] = []
    real = ToolRegistry.get_handler

    def _spy(name: str) -> Any:
        seen.append(name)
        return real(name)

    monkeypatch.setattr(ToolRegistry, "get_handler", _spy)
    code = (
        "return await call_tool('create_order', "
        "{'ticker': 'KXTEST', 'action': 'buy', 'side': 'yes', "
        "'count': 1, 'limit_price': 50, 'confirm': False})"
    )
    with pytest.raises(Exception, match="read-only mode"):
        await app.call_tool("execute", {"code": code})
    assert "create_order" in seen


def test_main_profile_flag_rebuilds_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_server_kalshi.server as server

    original = server.mcp

    async def _noop() -> None:
        return None

    monkeypatch.setattr(server, "run", _noop)
    monkeypatch.setattr("sys.argv", ["mcp-server-kalshi", "--profile", "readonly"])
    try:
        server.main()
        assert any(
            type(item).__name__ == "ReadOnlyToolFilter"
            for item in server.mcp.transforms
        )
    finally:
        server.mcp = original
        server.server = original
        server.streamable_http_app = original.streamable_http_app


_PROFILE_COUNTS = {
    "full": (101, 94),
    "readonly": (94, 94),
    "markets": (44, 44),
    "portfolio": (31, 31),
}


def _read_only_count(tools: list[Any]) -> int:
    return sum(1 for tool in tools if is_read_only_tool(tool))


@pytest.mark.parametrize(
    ("profile", "expected"),
    sorted(_PROFILE_COUNTS.items()),
)
async def test_profile_counts(profile: str, expected: tuple[int, int]) -> None:
    """Each profile lists its signed-off total and read-only counts."""
    tools = list(await create_server(profile=profile).list_tools())
    assert (len(tools), _read_only_count(tools)) == expected
    if profile == "markets":
        assert _names(tools) == set(MARKETS_TOOLS)
    elif profile == "portfolio":
        assert _names(tools) == set(PORTFOLIO_TOOLS)


@pytest.mark.parametrize("profile", sorted(_PROFILE_COUNTS))
async def test_readonly_env_composes(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """KALSHI_READONLY=1 keeps exactly that profile's read-only tools."""
    plain = await create_server(profile=profile).list_tools()
    expected = {tool.name for tool in plain if is_read_only_tool(tool)}
    monkeypatch.setattr("mcp_server_kalshi.server.settings.KALSHI_READONLY", True)
    listed = _names(await create_server(profile=profile).list_tools())
    assert listed == expected
    assert len(listed) == _PROFILE_COUNTS[profile][1]


async def test_job_lists_and_full_only_partition_the_catalog() -> None:
    """Every full tool is in a job allowlist or FULL_ONLY_TOOLS, and not both."""
    full = _names(await create_server(profile="full").list_tools())
    jobs = MARKETS_TOOLS | PORTFOLIO_TOOLS
    assert jobs | FULL_ONLY_TOOLS == full
    assert jobs.isdisjoint(FULL_ONLY_TOOLS)
    assert len(FULL_ONLY_TOOLS) == 30
    assert len(MARKETS_TOOLS) == 44
    assert len(PORTFOLIO_TOOLS) == 31
    assert len(jobs) == 71
    catalog = {
        tool.name: tool for tool in await create_server(profile="full").list_tools()
    }
    assert all(is_read_only_tool(catalog[name]) for name in jobs)


async def test_job_profile_hides_tools_outside_its_list() -> None:
    markets = create_server(profile="markets")
    portfolio = create_server(profile="portfolio")
    assert await markets.get_tool("get_market") is not None
    assert await markets.get_tool("get_balance") is None
    assert await markets.get_tool("create_order") is None
    assert await portfolio.get_tool("get_balance") is not None
    assert await portfolio.get_tool("list_markets") is None
    assert await portfolio.get_tool("create_order") is None


def test_unknown_allowlist_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An allowlisted name missing from the catalog fails at server build."""
    broken = Profile(
        name="broken",
        job="Test only.",
        tools=frozenset({"list_markets", "not_a_real_tool", "also_missing"}),
    )
    monkeypatch.setitem(PROFILES, "broken", broken)
    with pytest.raises(ValueError, match="also_missing, not_a_real_tool"):
        create_server(profile="broken")


def test_validate_allowlist_without_tools_is_empty() -> None:
    assert validate_allowlist(PROFILES["full"], {"list_markets"}) == frozenset()


async def test_job_profiles_keep_prompts_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowlist filtering is tools-only: prompts and resources match full."""
    import mcp_server_kalshi.server as server

    real = server._copy_catalog

    def _copy(root: Any) -> None:
        real(root)

        @root.prompt
        def profile_note() -> str:
            """Visible on every profile."""
            return "note"

        @root.resource("kalshi://profile-note")
        def profile_note_resource() -> str:
            """Visible on every profile."""
            return "note"

    monkeypatch.setattr(server, "_copy_catalog", _copy)
    full = create_server(profile="full")
    full_prompts = {item.name for item in await full.list_prompts()}
    full_resources = {str(item.uri) for item in await full.list_resources()}
    assert full_prompts == {"profile_note"}
    assert "kalshi://profile-note" in full_resources
    for profile in ("markets", "portfolio"):
        job = create_server(profile=profile)
        assert {item.name for item in await job.list_prompts()} == full_prompts
        assert {str(item.uri) for item in await job.list_resources()} == full_resources


@pytest.mark.parametrize("profile", ["markets", "portfolio"])
async def test_search_and_code_mode_do_not_attach_on_job_profiles(
    caplog: pytest.LogCaptureFixture, profile: str
) -> None:
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile=profile, enable_tool_search=True)
        coded = create_server(profile=profile, enable_code_mode=True)
    flat = _names(await create_server(profile=profile).list_tools())
    assert _names(await searched.list_tools()) == flat
    assert _names(await coded.list_tools()) == flat
    assert SYNTHETIC.isdisjoint(flat)
    messages = [record.message for record in caplog.records]
    assert any(
        f"Tool Search requested with profile={profile!r}" in message
        for message in messages
    )
    assert any(
        f"Code Mode requested with profile={profile!r}" in message
        for message in messages
    )


async def test_markets_profile_refuses_percentile_history_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one keyed markets tool is refused locally and sends no request."""
    import mcp_server_kalshi.server as server

    calls: list[str] = []

    async def _no_network(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("request")
        raise AssertionError("get_event_forecast_percentile_history made a request")

    monkeypatch.setattr(server.kalshi_client, "get", _no_network)
    monkeypatch.setattr(server.kalshi_client, "_request", _no_network)
    app = create_server(profile="markets")
    listed = await app.get_tool("get_event_forecast_percentile_history")
    assert listed is not None
    assert listed.description is not None
    assert "KALSHI_API_KEY" in listed.description
    assert "any profile" in listed.description
    result = await app.call_tool(
        "get_event_forecast_percentile_history",
        {
            "series_ticker": "SERIES",
            "ticker": "EVENT",
            "percentiles": [5000],
            "start_ts": 1,
            "end_ts": 2,
            "period_interval": 60,
        },
    )
    text = _text(result)
    assert MISSING_CREDENTIALS_ERROR in text
    assert "KALSHI_API_KEY" in text
    assert calls == []
