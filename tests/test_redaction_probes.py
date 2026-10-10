"""Kalshi redaction extras, timing probes and real ``tools/call`` error paths.

The house rules come from template v1.6.0 (``tests/test_redaction_house.py``). These tests
cover what Kalshi adds on top (signing headers, any-length ``Bearer``, PEM blocks in any case
or with no ``-----END``) and prove every tool error path masks a number, list or dict under a
credential key.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from conftest import FakeClient
from fastmcp import Client

from mcp_server_kalshi import server
from mcp_server_kalshi.errors import (
    MASK,
    redact_message,
    redact_payload,
    redact_secrets,
)

# Values that must never reach a client. Each is unique so a partial leak is caught.
SECRET_NUMBER = 987654321
SECRET_ITEM = "list-secret-item-4242"
SECRET_INNER = "dict-secret-inner-7373"
UPSTREAM_BODY = json.dumps(
    {
        "code": "unauthorized",
        "message": "invalid signature",
        "api_key": SECRET_NUMBER,
        "private_key": [SECRET_ITEM, "second"],
        "password": {"inner": SECRET_INNER},
    }
)
LEAKS = (str(SECRET_NUMBER), SECRET_ITEM, SECRET_INNER)


# ── Kalshi extras ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("KALSHI-ACCESS-KEY: abc123", f"KALSHI-ACCESS-KEY: {MASK}"),
        ("kalshi-access-signature=zz+/9==", f"kalshi-access-signature={MASK}"),
        (
            '{"KALSHI-ACCESS-SIGNATURE": "zz+/="}',
            f'{{"KALSHI-ACCESS-SIGNATURE": "{MASK}"}}',
        ),
        (
            "{'KALSHI-ACCESS-KEY': 'k-1'} next",
            f"{{'KALSHI-ACCESS-KEY': '{MASK}'}} next",
        ),
        (
            '"{\\"KALSHI-ACCESS-KEY\\": \\"k-1\\"}"',
            f'"{{\\"KALSHI-ACCESS-KEY\\": \\"{MASK}\\"}}"',
        ),
        ("Bearer ab", f"Bearer {MASK}"),
    ],
)
def test_kalshi_extras(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "x -----BEGIN private key-----\nMIIsecret\n-----END private key----- y",
        "x -----BEGIN RSA PRIVATE KEY-----\nMIIsecret\n-----END RSA PRIVATE KEY----- y",
        "x -----BEGIN Ec Private Key-----\\nMIIsecret\\n-----END Ec Private Key----- y",
    ],
)
def test_pem_block_any_case_is_masked_whole(raw: str) -> None:
    assert redact_secrets(raw) == f"x {MASK} y"
    assert "MIIsecret" not in redact_message(raw)


def test_unterminated_pem_is_masked_to_the_end() -> None:
    raw = "boom -----BEGIN PRIVATE KEY-----\nMIIsecret\nmore\napi=1"
    assert redact_secrets(raw) == f"boom {MASK}"


def test_two_pem_blocks_keep_the_text_between() -> None:
    block = "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----"
    assert redact_secrets(f"{block} mid {block}") == f"{MASK} mid {MASK}"


def test_payload_masks_kalshi_signing_headers() -> None:
    out = redact_payload(
        {"KALSHI-ACCESS-KEY": 1, "kalshi_access_signature": ["s"], "ok": "fine"}
    )
    assert out == {
        "KALSHI-ACCESS-KEY": MASK,
        "kalshi_access_signature": MASK,
        "ok": "fine",
    }


@pytest.mark.parametrize(
    "text",
    [
        "KALSHI-ACCESS-KEY: " * 20000,
        'KALSHI-ACCESS-SIGNATURE"' * 20000,
        "Bearer " * 50000,
        "Bearer " + "a" * 500000,
        "-----BEGIN private key-----\nx\n" * 5000,
        "-----BEGIN RSA PRIVATE KEY-----\nx\n" * 5000,
        "-----BEGIN " + "A" * 200000,
    ],
    ids=[
        "header_no_value",
        "header_quotes",
        "bearer_run",
        "bearer_long",
        "pem_lower_unterminated",
        "pem_upper_unterminated",
        "pem_label_unclosed",
    ],
)
def test_kalshi_patterns_are_linear(text: str) -> None:
    """Each Kalshi extra is anchored; an unterminated PEM took 5-9 s here before the fix."""
    start = time.perf_counter()
    redact_secrets(text)
    redact_message(text)
    assert time.perf_counter() - start < 2.0


# ── real tools/call error paths ───────────────────────────────────────────────


def _assert_clean(text: str) -> dict[str, Any]:
    for leak in LEAKS:
        assert leak not in text, text
    body: dict[str, Any] = json.loads(text[text.index("{") : text.rindex("}") + 1])
    assert body["api_key"] == body["private_key"] == body["password"] == MASK
    assert body["message"] == "invalid signature"
    return body


async def test_tools_call_raised_exception_masks_number_list_and_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastMCP ``tools/call`` (HTTP, ``fastmcp run``): ``KalshiFastMCPTool.run``."""
    fake = FakeClient(get_balance=RuntimeError(f"HTTP 401: {UPSTREAM_BODY}"))
    monkeypatch.setattr(server, "kalshi_client", fake)
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_balance", {}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Error in get_balance: HTTP 401: ")
    _assert_clean(text)


async def test_stdio_tools_call_masks_number_list_and_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The low-level ``tools/call`` handler the stdio transport serves."""
    fake = FakeClient(get_positions=RuntimeError(f"HTTP 500: {UPSTREAM_BODY}"))
    monkeypatch.setattr(server, "kalshi_client", fake)
    result = await server._req_call_tool(
        None, server.types.CallToolRequestParams(name="get_positions", arguments={})
    )
    assert result.is_error is True
    _assert_clean(result.content[0].text)  # type: ignore[union-attr]


async def test_tools_call_best_effort_lookup_errors_are_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_market_rules`` treats series/event lookups as best effort.

    The lookup error is kept only in a local dict and never reaches the result; this pins
    that no part of it leaks into the successful response.
    """
    fake = FakeClient(
        get_market={"market": {"event_ticker": "EV-1", "title": "T"}},
        get_series=RuntimeError(f"HTTP 401: {UPSTREAM_BODY}"),
        get_event=RuntimeError(f"HTTP 403: {UPSTREAM_BODY}"),
    )
    monkeypatch.setattr(server, "kalshi_client", fake)
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_market_rules", {"ticker": "KXELONMARS-99"}, raise_on_error=False
        )
    assert not result.is_error
    out = json.loads(result.content[0].text)  # type: ignore[union-attr]
    blob = json.dumps(out)
    assert all(leak not in blob for leak in LEAKS)
    assert "invalid signature" not in blob
    assert out["series_ticker"] == "KXELONMARS"
