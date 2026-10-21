"""Every outbound request connects only to an address that passed the DNS check.

The socket layer is replaced with ``tests/fake_network.py``: it records every connect
target, TLS server name and request, and resolves a hostname through
``socket.getaddrinfo`` the way httpcore's real backends do, so a second lookup (or a
connect to the hostname instead of a checked IP) shows up in the resolver's call
count. No network is touched.
"""

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpcore
import httpx
import pytest

from mcp_server_kalshi.kalshi_client import pdf as pdf_module
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient
from mcp_server_kalshi.ssrf import (
    _PINNED_IPS,
    SSRFSafeAsyncTransport,
    UnsafeURLError,
    _PinnedNetworkBackend,
    avalidate_spec_url,
)
from scripts.check_openapi_drift import check_drift
from tests.fake_network import FakeNetwork, answer

V6 = "2606:4700::6812:1"
V4 = "104.18.0.1"


def _assert_pinned(net: FakeNetwork, dns: MagicMock, host: str) -> None:
    """One lookup of ``host``; one connect, to the checked IP; hostname on the wire."""
    assert [c.args[0] for c in dns.call_args_list] == [host]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [host]
    assert b"\r\nHost: " + host.encode() + b"\r\n" in net.sent


# ---- Every client in the server --------------------------------------------------------


async def test_api_client_connects_to_checked_ip() -> None:
    net = FakeNetwork(body={})
    client = KalshiAPIClient(base_url="https://api.elections.kalshi.com/trade-api/v2")
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            assert await client.get("/exchange/status") == {}
    finally:
        await client.aclose()
    _assert_pinned(net, dns, "api.elections.kalshi.com")


async def test_pdf_fetch_connects_to_checked_ip() -> None:
    net = FakeNetwork(body="pdf")
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        _, content = await pdf_module._download_pdf(
            "https://kalshi-public-docs.s3.amazonaws.com/a.pdf"
        )
    assert content == b'"pdf"'
    _assert_pinned(net, dns, "kalshi-public-docs.s3.amazonaws.com")


def test_drift_fetch_connects_to_checked_ip(tmp_path: Path) -> None:
    spec = {"paths": {"/exchange/status": {"get": {"parameters": []}}}}
    net = FakeNetwork(body=spec)
    client = tmp_path / "client.py"
    client.write_text(
        'class C:\n    async def s(self):\n        await self.get("/exchange/status")\n'
    )
    allow = tmp_path / "allowlist.txt"
    allow.write_text("")
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        code = check_drift(
            spec_url="https://docs.kalshi.com/openapi.yaml",
            client_path=client,
            allowlist_path=allow,
        )
    assert code == 0
    _assert_pinned(net, dns, "docs.kalshi.com")


def test_drift_fetch_does_not_follow_redirects(tmp_path: Path) -> None:
    """A 302 from the checked docs host fails the fetch; the Location is never requested."""
    net = FakeNetwork(status=302)
    net.response = (
        b"HTTP/1.1 302 Found\r\nLocation: https://docs.kalshi.com/moved.yaml\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    client = tmp_path / "client.py"
    client.write_text("class C:\n    pass\n")
    allow = tmp_path / "allowlist.txt"
    allow.write_text("")
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        code = check_drift(
            spec_url="https://docs.kalshi.com/openapi.yaml",
            client_path=client,
            allowlist_path=allow,
        )
    assert code == 1
    assert net.requests_by_connection() == [[b"GET /openapi.yaml HTTP/1.1"]]
    assert [c.args[0] for c in dns.call_args_list] == ["docs.kalshi.com"]


# ---- SSRFSafeAsyncTransport ------------------------------------------------------------


async def test_transport_checks_itself_when_caller_did_not() -> None:
    """With no prior avalidate_* call, the transport runs the one check and pins it."""
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport()) as client:
            resp = await client.get("https://docs.kalshi.com/openapi.yaml")
    assert resp.status_code == 200
    _assert_pinned(net, dns, "docs.kalshi.com")


async def test_transport_takes_a_recorded_check_once() -> None:
    """An avalidate_* check serves the next request only; the one after checks again."""
    url = "https://docs.kalshi.com/openapi.yaml"
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        assert await avalidate_spec_url(url) == url
        async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport()) as client:
            await client.get(url)
            await client.get(url)
    assert [c.args[0] for c in dns.call_args_list] == ["docs.kalshi.com"] * 2
    assert net.connects == [(V4, 443)]


async def test_transport_connects_to_the_checked_address() -> None:
    """One lookup; the connect goes to a checked address; the URL keeps the hostname.

    The Host header and the TLS server name (SNI and certificate verification) are the
    hostname, and the caller's request is left as it was.
    """
    net = FakeNetwork()
    request = httpx.Request(
        "POST", "https://api.customdomain.org:8443/data?q=1", json={"a": 1}
    )
    async with SSRFSafeAsyncTransport() as transport:
        with (
            net.patches(),
            patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns,
        ):
            response = await transport.handle_async_request(request)
            await response.aread()
    assert response.status_code == 200
    dns.assert_called_once()
    assert dns.call_args.args[0] == "api.customdomain.org"
    assert net.connects == [(V6, 8443)]
    assert net.tls_server_names == ["api.customdomain.org"]
    assert net.sent.startswith(b"POST /data?q=1 HTTP/1.1\r\n")
    assert b"\r\nHost: api.customdomain.org:8443\r\n" in net.sent
    assert net.sent.endswith(b'{"a":1}')
    assert str(request.url) == "https://api.customdomain.org:8443/data?q=1"
    assert "sni_hostname" not in request.extensions


async def test_transport_falls_back_to_the_next_checked_address() -> None:
    """A failed connect moves to the next checked address in resolver order, never further."""
    url = "https://api.customdomain.org/data"
    net = FakeNetwork(fail={V6})
    async with SSRFSafeAsyncTransport() as transport:
        with (
            net.patches(),
            patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns,
        ):
            response = await transport.handle_async_request(httpx.Request("GET", url))
            await response.aread()
    assert response.status_code == 200
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == ["api.customdomain.org"]

    # Every checked address failing is a connect error; nothing unchecked is tried.
    net = FakeNetwork(fail={V6, V4})
    async with SSRFSafeAsyncTransport() as transport:
        with (
            net.patches(),
            patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns,
        ):
            with pytest.raises(httpx.ConnectError, match=f"connect to {V4} failed"):
                await transport.handle_async_request(httpx.Request("GET", url))
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == []


async def test_transport_pools_connections_per_hostname() -> None:
    """Two hostnames on one IP never share a TLS connection; one hostname reuses its own."""
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            for host in (
                "a.customdomain.org",
                "b.customdomain.org",
                "a.customdomain.org",
            ):
                request = httpx.Request("GET", f"https://{host}/{host[0]}")
                response = await transport.handle_async_request(request)
                await response.aread()
                await response.aclose()
    # The address is checked for every request, pooled or not.
    assert [c.args[0] for c in dns.call_args_list] == [
        "a.customdomain.org",
        "b.customdomain.org",
        "a.customdomain.org",
    ]
    assert net.connects == [(V4, 443), (V4, 443)]
    assert net.tls_server_names == ["a.customdomain.org", "b.customdomain.org"]
    assert net.requests_by_connection() == [
        [b"GET /a HTTP/1.1", b"GET /a HTTP/1.1"],
        [b"GET /b HTTP/1.1"],
    ]


@pytest.mark.parametrize("blocked", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
async def test_transport_refuses_blocked_answer(blocked: str) -> None:
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4, blocked)):
        async with httpx.AsyncClient(transport=SSRFSafeAsyncTransport()) as client:
            with pytest.raises(UnsafeURLError, match="blocked"):
                await client.get("https://docs.kalshi.com/openapi.yaml")
    assert net.attempts == []


async def test_transport_refuses_a_host_with_no_checked_address() -> None:
    """No checked address means no connect: an empty answer, or no pin at all."""
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=[]) as dns:
            with pytest.raises(UnsafeURLError, match="could not be resolved"):
                await transport.handle_async_request(
                    httpx.Request("GET", "https://api.customdomain.org/x")
                )
            dns.assert_called_once()
            # A request with no host is passed straight to httpx, which rejects it.
            with pytest.raises(httpx.UnsupportedProtocol):
                await transport.handle_async_request(httpx.Request("GET", "/relative"))
            dns.assert_called_once()
    assert net.attempts == []

    # Outside a transport request nothing is pinned, so the backend dials nothing.
    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(UnsafeURLError, match="no checked address"):
        await backend.connect_tcp("api.customdomain.org", 443)
    await backend.sleep(0)


async def test_transport_checks_the_name_it_dials() -> None:
    """An internationalized host is checked under the ASCII (IDNA) name used to connect.

    The check, the pin and the TLS server name all use ``raw_host``, so they cannot
    disagree on which name was resolved.
    """
    ascii_host = "xn--fa-hia.customdomain.org"
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            response = await transport.handle_async_request(
                httpx.Request("GET", "https://fa\u00df.customdomain.org/x")
            )
            await response.aread()
    dns.assert_called_once()
    assert dns.call_args.args[0] == ascii_host
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [ascii_host]
    assert f"\r\nHost: {ascii_host}\r\n".encode() in net.sent


async def test_transport_refuses_unix_sockets() -> None:
    """The pinned backend never dials a Unix socket."""
    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(UnsafeURLError, match="unix sockets are not allowed"):
        await backend.connect_unix_socket("/var/run/docker.sock")


async def test_backend_matches_pins_case_insensitively() -> None:
    net = FakeNetwork()
    token = _PINNED_IPS.set({"docs.kalshi.com": (V4,)})
    try:
        with net.patches(), patch("socket.getaddrinfo") as dns:
            await _PinnedNetworkBackend().connect_tcp("Docs.Kalshi.com", 443)
    finally:
        _PINNED_IPS.reset(token)
    dns.assert_not_called()
    assert net.connects == [(V4, 443)]


def test_transport_builds_its_pool_through_public_httpcore_api() -> None:
    """Guard for dependency bumps: fail loudly if the pinned pool stops being used.

    ``network_backend`` is a documented ``httpcore.AsyncConnectionPool`` argument; httpx
    serves requests through the transport's ``_pool``. The connect tests above fail too
    if httpx stops doing that, but this names the cause.
    """
    assert (
        "network_backend" in inspect.signature(httpcore.AsyncConnectionPool).parameters
    )
    transport = SSRFSafeAsyncTransport(limits=httpx.Limits(max_connections=7))
    pool = vars(transport).get("_pool")
    assert isinstance(pool, httpcore.AsyncConnectionPool), (
        "httpx.AsyncHTTPTransport no longer serves requests through self._pool; "
        "SSRFSafeAsyncTransport must be rebuilt for this httpx version"
    )
    assert "_pool" in inspect.getsource(httpx.AsyncHTTPTransport.handle_async_request)
    backends = [v for v in vars(pool).values() if isinstance(v, _PinnedNetworkBackend)]
    assert len(backends) == 1
    assert pool._max_connections == 7
