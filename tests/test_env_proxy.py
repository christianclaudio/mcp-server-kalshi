"""The API client keeps honoring environment proxies; the pin covers direct connections.

When ``HTTP(S)_PROXY`` / ``ALL_PROXY`` applies to the base URL after ``NO_PROXY``, the
client sends through httpx's own proxied transport, after the same hostname check. With
no applicable proxy it connects only to the checked address. The socket layer is faked
by ``tests/fake_network.py`` and DNS is patched; nothing opens a socket.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient
from mcp_server_kalshi.ssrf import (
    _CHECKED_IPS,
    SSRFSafeAsyncTransport,
    UnsafeURLError,
    environment_proxy_for,
)
from tests.fake_network import FakeNetwork, answer

HOST = "api.elections.kalshi.com"
BASE = f"https://{HOST}/trade-api/v2"
PROXY_IP = "203.0.113.9"
PROXY = f"http://{PROXY_IP}:3128"
V4 = "104.18.0.1"

API_BASES = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
    "https://demo-api.kalshi.co/trade-api/v2",
    "https://external-api.demo.kalshi.co/trade-api/v2",
)


async def _get(net: FakeNetwork, **client_kwargs: bool) -> tuple[object, list[str]]:
    client = KalshiAPIClient(base_url=BASE, **client_kwargs)
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            result = await client.get("/exchange/status")
            transport = client._ensure_client()._transport_for_url(httpx.URL(BASE))
    finally:
        await client.aclose()
    return (result, transport), [c.args[0] for c in dns.call_args_list]


def _assert_direct(net: FakeNetwork, lookups: list[str], transport: object) -> None:
    assert isinstance(transport, SSRFSafeAsyncTransport)
    assert lookups == [HOST]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [HOST]


# ---- Proxy applies: httpx's proxied transport, after the hostname check ----------------


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY"])
async def test_api_client_sends_through_an_applicable_env_proxy(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, PROXY)
    net = FakeNetwork(body={})
    (result, transport), lookups = await _get(net)
    assert result == {}
    assert not isinstance(transport, SSRFSafeAsyncTransport)
    # The hostname check ran (one lookup of the Kalshi host); the proxy got the CONNECT.
    assert lookups == [HOST]
    assert net.connects == [(PROXY_IP, 3128)]
    assert f"CONNECT {HOST}:443 HTTP/1.1".encode() in net.sent
    assert net.tls_server_names == [HOST]
    # The check is not left behind for a later pinned request in this task.
    assert HOST not in (_CHECKED_IPS.get() or {})


@pytest.mark.parametrize("blocked", ["127.0.0.1", "169.254.169.254", "10.0.0.5"])
async def test_proxy_path_still_refuses_a_blocked_answer(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    net = FakeNetwork(body={})
    client = KalshiAPIClient(base_url=BASE)
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(blocked)):
            with pytest.raises(UnsafeURLError, match="blocked"):
                await client.get("/exchange/status")
    finally:
        await client.aclose()
    assert net.attempts == []


# ---- No applicable proxy: the pinned transport ----------------------------------------


async def test_no_proxy_env_uses_the_pinned_transport() -> None:
    net = FakeNetwork(body={})
    (_, transport), lookups = await _get(net)
    _assert_direct(net, lookups, transport)


@pytest.mark.parametrize(
    "no_proxy", ["kalshi.com", "api.elections.kalshi.com", "*", "x.test, kalshi.com"]
)
async def test_no_proxy_match_uses_the_pinned_transport(
    monkeypatch: pytest.MonkeyPatch, no_proxy: str
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("NO_PROXY", no_proxy)
    net = FakeNetwork(body={})
    (_, transport), lookups = await _get(net)
    _assert_direct(net, lookups, transport)


async def test_http_proxy_does_not_apply_to_the_https_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", PROXY)
    net = FakeNetwork(body={})
    (_, transport), lookups = await _get(net)
    _assert_direct(net, lookups, transport)


async def test_trust_env_off_ignores_env_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    net = FakeNetwork(body={})
    (_, transport), lookups = await _get(net, trust_env=False)
    _assert_direct(net, lookups, transport)


# ---- Same answer as httpx's own env-proxy resolution ------------------------------------

_PROXIES = [
    {"HTTPS_PROXY": PROXY},
    {"ALL_PROXY": "socks5://203.0.113.9:1080"},
    {"HTTP_PROXY": PROXY},
    {"HTTPS_PROXY": "203.0.113.9:3128"},
    {"HTTPS_PROXY": PROXY, "ALL_PROXY": "http://203.0.113.10:3128"},
]
_NO_PROXY = [
    None,
    "",
    "*",
    "kalshi.com",
    ".kalshi.com",
    "elections.kalshi.com",
    "api.elections.kalshi.com",
    "lections.kalshi.com",
    "kalshi.co",
    ".kalshi.co",
    "co",
    "https://api.elections.kalshi.com",
    "http://api.elections.kalshi.com",
    "https://api.elections.kalshi.com:8443",
    "all://*.kalshi.co",
    "https://",
    "localhost",
    "127.0.0.1",
    "10.0.0.0/8",
    "::1",
    "x.test, , demo-api.kalshi.co",
]


def _httpx_answer(url: str) -> str | None:
    """httpx 0.28's own choice for ``url`` (private API, used only as the reference)."""
    from httpx._utils import URLPattern, get_environment_proxies

    mounts = sorted(
        (URLPattern(key), proxy) for key, proxy in get_environment_proxies().items()
    )
    for pattern, proxy in mounts:
        if pattern.matches(httpx.URL(url)):
            return proxy
    return None


@pytest.mark.parametrize("proxies", _PROXIES)
@pytest.mark.parametrize("no_proxy", _NO_PROXY)
def test_environment_proxy_for_matches_httpx(
    monkeypatch: pytest.MonkeyPatch, proxies: dict[str, str], no_proxy: str | None
) -> None:
    for name, value in proxies.items():
        monkeypatch.setenv(name, value)
    if no_proxy is not None:
        monkeypatch.setenv("NO_PROXY", no_proxy)
    for url in API_BASES:
        assert environment_proxy_for(url) == _httpx_answer(url), url


# ---- The PDF and drift fetches stay pinned and unproxied --------------------------------


async def test_pdf_fetch_ignores_env_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_server_kalshi.kalshi_client import pdf as pdf_module

    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("ALL_PROXY", PROXY)
    net = FakeNetwork(body="pdf")
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)):
        await pdf_module._download_pdf("https://assets.kalshi.com/a.pdf")
    assert net.connects == [(V4, 443)]


def test_drift_fetch_ignores_env_proxies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.check_openapi_drift import check_drift

    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("ALL_PROXY", PROXY)
    net = FakeNetwork(body={"paths": {}})
    client = tmp_path / "client.py"
    client.write_text("class C:\n    pass\n")
    allow = tmp_path / "allowlist.txt"
    allow.write_text("")
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)):
        code = check_drift(
            spec_url="https://docs.kalshi.com/openapi.yaml",
            client_path=client,
            allowlist_path=allow,
        )
    assert code == 0
    assert net.connects == [(V4, 443)]
