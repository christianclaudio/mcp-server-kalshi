"""Every outbound request connects to the address that passed the DNS check.

Each test counts ``getaddrinfo`` calls and records where the socket layer was asked to
connect. A second lookup, or a connect to the hostname instead of the checked IP, fails.
No network is touched: the socket layer is replaced with an in-memory stream.
"""

import socket
from typing import Any

import httpcore
import pytest

from mcp_server_kalshi.kalshi_client import pdf as pdf_module
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient
from scripts.check_openapi_drift import check_drift

CHECKED_IP = "104.18.0.1"


class _Stream:
    def __init__(self, body: bytes, calls: list[dict[str, Any]]) -> None:
        self._out = (
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        self._calls = calls

    def _start_tls(self, server_hostname: str | None) -> "_Stream":
        self._calls[-1]["sni"] = server_hostname
        return self

    def _read(self) -> bytes:
        out, self._out = self._out, b""
        return out

    def get_extra_info(self, info: str) -> Any:
        return None


class _AsyncStream(_Stream, httpcore.AsyncNetworkStream):
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._read()

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._calls[-1].setdefault("sent", b"")
        self._calls[-1]["sent"] += buffer

    async def aclose(self) -> None:
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self._start_tls(server_hostname)


class _SyncStream(_Stream, httpcore.NetworkStream):
    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._read()

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._calls[-1].setdefault("sent", b"")
        self._calls[-1]["sent"] += buffer

    def close(self) -> None:
        return None

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self._start_tls(server_hostname)


@pytest.fixture
def wire(monkeypatch):
    """Count DNS lookups and capture every TCP connect target, for any httpcore backend."""
    lookups: list[str] = []
    calls: list[dict[str, Any]] = []
    state = {"body": b"{}"}

    def _getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (CHECKED_IP, 0))]

    async def _aconnect(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        calls.append({"host": host, "port": port})
        return _AsyncStream(state["body"], calls)

    def _sconnect(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        calls.append({"host": host, "port": port})
        return _SyncStream(state["body"], calls)

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _getaddrinfo)
    # AutoBackend (httpcore's default) delegates to AnyIOBackend under asyncio.
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", _aconnect)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", _sconnect)
    return lookups, calls, state


def _assert_pinned(lookups, calls, host):
    assert lookups == [host], f"expected one lookup of {host}, got {lookups}"
    assert [c["host"] for c in calls] == [CHECKED_IP]
    assert calls[0]["port"] == 443
    assert calls[0]["sni"] == host
    assert b"Host: " + host.encode() in calls[0]["sent"]


async def test_api_client_connects_to_checked_ip(wire):
    lookups, calls, _ = wire
    client = KalshiAPIClient(base_url="https://api.elections.kalshi.com/trade-api/v2")
    try:
        assert await client.get("/exchange/status") == {}
    finally:
        await client.aclose()
    _assert_pinned(lookups, calls, "api.elections.kalshi.com")


async def test_pdf_fetch_connects_to_checked_ip(wire):
    lookups, calls, state = wire
    state["body"] = b"%PDF-fake"
    final, content = await pdf_module._download_pdf(
        "https://kalshi-public-docs.s3.amazonaws.com/a.pdf"
    )
    assert content == b"%PDF-fake"
    _assert_pinned(lookups, calls, "kalshi-public-docs.s3.amazonaws.com")


def test_drift_fetch_connects_to_checked_ip(tmp_path, wire):
    lookups, calls, state = wire
    state["body"] = b"paths:\n  /exchange/status:\n    get:\n      parameters: []\n"
    client = tmp_path / "client.py"
    client.write_text(
        'class C:\n    async def s(self):\n        await self.get("/exchange/status")\n'
    )
    allow = tmp_path / "allowlist.txt"
    allow.write_text("")
    code = check_drift(
        spec_url="https://docs.kalshi.com/openapi.yaml",
        client_path=client,
        allowlist_path=allow,
    )
    assert code == 0
    _assert_pinned(lookups, calls, "docs.kalshi.com")


# ---- Transport and backend edge cases --------------------------------------------------


async def test_transport_checks_itself_when_caller_did_not(wire):
    """With no prior avalidate_* call, the transport runs the one check and pins it."""
    import httpx

    from mcp_server_kalshi.ssrf import SSRFSafeAsyncTransport

    lookups, calls, _ = wire
    async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport()) as client:
        resp = await client.get("https://docs.kalshi.com/openapi.yaml")
    assert resp.status_code == 200
    _assert_pinned(lookups, calls, "docs.kalshi.com")


async def test_transport_refuses_blocked_answer(monkeypatch, wire):
    import httpx

    from mcp_server_kalshi.ssrf import SSRFSafeAsyncTransport, UnsafeURLError

    _, calls, _ = wire
    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport()) as client:
        with pytest.raises(UnsafeURLError, match="blocked"):
            await client.get("https://docs.kalshi.com/openapi.yaml")
    assert calls == []


async def test_backend_refuses_host_without_a_checked_address():
    from mcp_server_kalshi.ssrf import UnsafeURLError, _PinnedNetworkBackend

    with pytest.raises(UnsafeURLError, match="no checked address"):
        await _PinnedNetworkBackend().connect_tcp("docs.kalshi.com", 443)


async def test_backend_tries_next_checked_address_then_raises_last():
    from mcp_server_kalshi.ssrf import _PINNED_IPS, _PinnedNetworkBackend

    tried: list[str] = []
    slept: list[float] = []

    class _Inner(httpcore.AsyncNetworkBackend):
        def __init__(self, ok: str | None) -> None:
            self._ok = ok

        async def connect_tcp(self, host, port, **kwargs):
            tried.append(host)
            if host == self._ok:
                return "stream"
            raise httpcore.ConnectError(host)

        async def sleep(self, seconds: float) -> None:
            slept.append(seconds)

    token = _PINNED_IPS.set({"docs.kalshi.com": ("104.18.0.1", "104.18.0.2")})
    try:
        ok = _PinnedNetworkBackend(_Inner("104.18.0.2"))
        assert await ok.connect_tcp("docs.kalshi.com", 443) == "stream"
        await ok.sleep(0.5)
        with pytest.raises(httpcore.ConnectError, match="104.18.0.2"):
            await _PinnedNetworkBackend(_Inner(None)).connect_tcp(
                "Docs.Kalshi.com", 443
            )
    finally:
        _PINNED_IPS.reset(token)
    assert tried == ["104.18.0.1", "104.18.0.2", "104.18.0.1", "104.18.0.2"]
    assert slept == [0.5]
