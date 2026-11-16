"""Retry-After handling on 429 and 5xx retries in ``BaseAPIClient._request``.

Only GET, HEAD, and OPTIONS are retried. Orders and every other method are not.
"""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

import mcp_server_kalshi.kalshi_client.base as base_mod
from mcp_server_kalshi.errors import KalshiAPIError
from mcp_server_kalshi.kalshi_client.base import (
    _retry_after_seconds,
)
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient

BASE = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every retry wait instead of sleeping, with the jitter fixed at 0.3 s."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(base_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(base_mod.random, "uniform", lambda a, b: 0.3)
    return recorded


async def _run(
    statuses: list[tuple[int, dict[str, str]]],
    *,
    method: str = "GET",
    max_retries: int = 3,
    timeout: int = 30,
) -> tuple[Any, int]:
    """Serve the given (status, headers) responses in order, then 200."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= len(statuses):
            status, headers = statuses[calls - 1]
            return httpx.Response(status, headers=headers, text="busy")
        return httpx.Response(200, json={"ok": True})

    client = KalshiAPIClient(base_url=BASE, max_retries=max_retries, timeout=timeout)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        result = await client._request(method, "/retry")
    finally:
        await client.aclose()
    return result, calls


def _assert_api_error(exc: BaseException, status: int) -> None:
    """A failed call is ``KalshiAPIError`` itself, carrying the status."""
    assert type(exc) is KalshiAPIError
    assert exc.status_code == status


@pytest.mark.asyncio
async def test_retry_after_seconds_wins_when_longer(sleeps: list[float]) -> None:
    result, calls = await _run([(429, {"Retry-After": "7"})])
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_backoff_wins_when_longer(sleeps: list[float]) -> None:
    result, _ = await _run(
        [
            (429, {"Retry-After": "0"}),
            (429, {"Retry-After": "1"}),
            (429, {"Retry-After": "2"}),
        ]
    )
    assert result == {"ok": True}
    # 2**attempt + 0.3 jitter: 1.3, 2.3, 4.3 all exceed the header.
    assert sleeps == [1.3, 2.3, 4.3]


@pytest.mark.asyncio
async def test_retry_after_http_date(sleeps: list[float]) -> None:
    when = datetime.now(UTC) + timedelta(seconds=20)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert len(sleeps) == 1
    assert 17.0 <= sleeps[0] <= 20.0


@pytest.mark.asyncio
async def test_retry_after_past_http_date_falls_back_to_backoff(
    sleeps: list[float],
) -> None:
    when = datetime.now(UTC) - timedelta(hours=1)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert sleeps == [1.3]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["soon", "-5", "1.5", "+3", "", "Wed, 99 Foo 2026"])
async def test_unparseable_retry_after_fails_immediately(
    sleeps: list[float], value: str
) -> None:
    """A Retry-After that cannot be parsed is hostile and is not waited out."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": value}, text="busy")

    client = KalshiAPIClient(base_url=BASE, max_retries=3)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 429)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_missing_retry_after_uses_backoff(sleeps: list[float]) -> None:
    await _run([(429, {})])
    assert sleeps == [1.3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["100000", "9" * 400, "121"], ids=["large", "400-digit", "just-over"]
)
async def test_retry_after_over_cap_fails_immediately(
    sleeps: list[float], value: str
) -> None:
    """A Retry-After above 120 seconds is not shortened; the call fails at once."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": value}, text="busy")

    client = KalshiAPIClient(base_url=BASE, max_retries=3, timeout=5)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 429)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_after_at_cap_is_honored(sleeps: list[float]) -> None:
    """120 seconds is waited out in full, even when the request timeout is shorter."""
    result, calls = await _run([(429, {"Retry-After": "120"})], timeout=5)
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [120.0]


@pytest.mark.asyncio
async def test_retry_after_http_date_over_cap_fails_immediately(
    sleeps: list[float],
) -> None:
    when = datetime.now(UTC) + timedelta(days=365)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            headers={"Retry-After": format_datetime(when, usegmt=True)},
            text="busy",
        )

    client = KalshiAPIClient(base_url=BASE, max_retries=3)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 503)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_503_retry_after_over_cap_is_api_error(sleeps: list[float]) -> None:
    """A 503 whose Retry-After is over 120 seconds fails once, with no wait."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"Retry-After": "121"}, text="down")

    client = KalshiAPIClient(base_url=BASE, max_retries=3)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 503)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_after_honored_on_503(sleeps: list[float]) -> None:
    result, calls = await _run([(503, {"Retry-After": "9"})])
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [9.0]


@pytest.mark.asyncio
async def test_retry_count_unchanged_with_retry_after(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "8"})

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 429)
    assert calls == 3
    assert sleeps == [8.0, 8.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_post_http_error_is_not_retried(sleeps: list[float], status: int) -> None:
    """POST is not retried on 429 or 5xx."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="no")

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request("POST", "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, status)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["DELETE", "PATCH"])
async def test_order_methods_are_not_retried(sleeps: list[float], method: str) -> None:
    """DELETE and PATCH, the other order verbs, are not retried on 429."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "5"}, text="no")

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(KalshiAPIError) as exc_info:
            await client._request(method, "/retry")
    finally:
        await client.aclose()
    _assert_api_error(exc_info.value, 429)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_head_is_retried(sleeps: list[float]) -> None:
    """HEAD is idempotent, so a 429 is retried."""
    result, calls = await _run([(429, {"Retry-After": "4"})], method="HEAD")
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [4.0]


@pytest.mark.asyncio
async def test_post_transport_error_is_not_retried(sleeps: list[float]) -> None:
    """POST is not retried when the transport fails."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection failed")

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(httpx.ConnectError):
            await client._request("POST", "/retry")
    finally:
        await client.aclose()
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_get_transport_error_is_retried_then_succeeds(
    sleeps: list[float],
) -> None:
    """GET retries a transport error, then returns the next success."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection failed")
        return httpx.Response(200, json={"ok": True})

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        result = await client._request("GET", "/retry")
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [1.3]


@pytest.mark.asyncio
async def test_get_transport_error_exhausted_raises(sleeps: list[float]) -> None:
    """GET re-raises the transport error once the retry budget is spent."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection failed")

    client = KalshiAPIClient(base_url=BASE, max_retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE
    )
    try:
        with pytest.raises(httpx.ConnectError):
            await client._request("GET", "/retry")
    finally:
        await client.aclose()
    assert calls == 3
    assert sleeps == [1.3, 2.3]


def test_retry_after_parser_naive_date_is_utc() -> None:
    # "-0000" makes parsedate_to_datetime return a naive datetime; it is read as UTC.
    response = httpx.Response(
        429, headers={"Retry-After": "Thu, 01 Jan 2099 00:00:00 -0000"}
    )
    seconds = _retry_after_seconds(response)
    assert seconds is not None and seconds > 0


def test_retry_after_parser_absent() -> None:
    assert _retry_after_seconds(httpx.Response(429)) is None


def test_retry_after_parser_rejects_non_ascii_digits() -> None:
    # A superscript two passes str.isdigit() but is not delta-seconds.
    response = httpx.Response(429, headers={"Retry-After": "\u00b2".encode("latin-1")})
    assert response.headers["Retry-After"] == "\u00b2"
    assert _retry_after_seconds(response) is None
