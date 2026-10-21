"""Kalshi's dedicated production and demo REST hosts as opt-in BASE_URLs.

Kalshi changelog, May 7, 2026 (https://docs.kalshi.com/changelog, accessed 2026-10-21):
"Added the dedicated external Trade API hosts to the docs and examples: Production REST:
https://external-api.kalshi.com/trade-api/v2 ... Existing shared hosts remain supported
for compatibility. Request signing is unchanged." The OpenAPI spec v3.34.0 lists it as the
first production server. The default stays on the shared host; offline only (no network).
"""

import httpx
import pytest
from conftest import FakeClient, handler_result

from mcp_server_kalshi import server
from mcp_server_kalshi.config import (
    DEMO_DEDICATED_REST_BASE,
    ENV_REST_BASE,
    PROD_DEDICATED_REST_BASE,
    Settings,
)
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient
from mcp_server_kalshi.ssrf import UnsafeURLError, avalidate_api_base_url

DEDICATED = "https://external-api.kalshi.com/trade-api/v2"
# Same changelog entry: "Demo REST: https://external-api.demo.kalshi.co/trade-api/v2".
DEMO_DEDICATED = "https://external-api.demo.kalshi.co/trade-api/v2"


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_dedicated_constant_matches_changelog():
    assert PROD_DEDICATED_REST_BASE == DEDICATED


def test_defaults_unchanged():
    assert ENV_REST_BASE["prod"] == "https://api.elections.kalshi.com/trade-api/v2"
    assert _settings(KALSHI_ENV="prod").rest_base_url == ENV_REST_BASE["prod"]
    assert _settings().rest_base_url == "https://demo-api.kalshi.co/trade-api/v2"


def test_base_url_accepts_dedicated_host_as_production():
    s = _settings(BASE_URL=DEDICATED + "/")
    assert s.rest_base_url == DEDICATED
    # An explicit prod override counts as production even with KALSHI_ENV=demo.
    assert s.is_production is True
    assert s.env_label == "PROD (real money)"


async def test_get_environment_reports_dedicated_base(monkeypatch):
    monkeypatch.setattr(server, "settings", _settings(BASE_URL=DEDICATED))
    monkeypatch.setattr(server, "kalshi_client", FakeClient())
    out = handler_result(await server.ToolRegistry.get_handler("get_environment")({}))
    assert out["base_url"] == DEDICATED
    assert out["is_production"] is True


async def test_requests_and_signing_path_on_dedicated_host(rsa_key_file):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"balance": 0})

    client = KalshiAPIClient(
        base_url=DEDICATED, api_key="key-id", private_key_path=rsa_key_file
    )
    client._ensure_client()
    assert client._client is not None
    auth = client._client.auth
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), auth=auth
    )
    try:
        await client.get_balance({"subaccount": 0})
    finally:
        await client.close()

    req = seen[0]
    assert req.url.host == "external-api.kalshi.com"
    assert req.url.path == "/trade-api/v2/portfolio/balance"
    assert "KALSHI-ACCESS-SIGNATURE" in req.headers


async def test_dedicated_host_still_rejects_private_dns(monkeypatch):
    import socket

    def _private(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _private)
    with pytest.raises(UnsafeURLError, match="resolves to a blocked"):
        await avalidate_api_base_url(DEDICATED)


# ---- Dedicated demo host -------------------------------------------------------------
def test_demo_dedicated_constant_matches_changelog():
    assert DEMO_DEDICATED_REST_BASE == DEMO_DEDICATED


def test_base_url_accepts_dedicated_demo_host_as_demo():
    s = _settings(BASE_URL="https://External-API.demo.kalshi.co/trade-api/v2/")
    assert s.rest_base_url == DEMO_DEDICATED
    assert s.is_production is False
    assert s.env_label == "DEMO (sandbox)"
    # The override decides the environment, even next to KALSHI_ENV=prod.
    assert _settings(KALSHI_ENV="prod", BASE_URL=DEMO_DEDICATED).is_production is False


def test_demo_default_unchanged():
    assert ENV_REST_BASE["demo"] == "https://demo-api.kalshi.co/trade-api/v2"
    assert _settings(KALSHI_ENV="demo").rest_base_url == ENV_REST_BASE["demo"]


@pytest.mark.parametrize(
    "bad",
    [
        "http://external-api.demo.kalshi.co/trade-api/v2",
        "https://external-api.demo.kalshi.co.evil.com/trade-api/v2",
        "https://external-api.demo.kalshi.co:8443/trade-api/v2",
        "https://external-api.demo.kalshi.co/trade-api/v1",
    ],
)
def test_base_url_rejects_bad_dedicated_demo_urls(bad):
    with pytest.raises(ValueError):
        _settings(BASE_URL=bad)


async def test_get_environment_reports_dedicated_demo(monkeypatch):
    monkeypatch.setattr(server, "settings", _settings(BASE_URL=DEMO_DEDICATED))
    monkeypatch.setattr(server, "kalshi_client", FakeClient())
    out = handler_result(await server.ToolRegistry.get_handler("get_environment")({}))
    assert out["base_url"] == DEMO_DEDICATED
    assert out["is_production"] is False
    assert out["environment"] == "DEMO (sandbox)"


async def test_requests_signed_on_dedicated_demo_host(rsa_key_file):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"balance": 0})

    client = KalshiAPIClient(
        base_url=DEMO_DEDICATED, api_key="key-id", private_key_path=rsa_key_file
    )
    client._ensure_client()
    assert client._client is not None
    auth = client._client.auth
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), auth=auth
    )
    try:
        await client.get_balance({})
    finally:
        await client.close()

    req = seen[0]
    assert req.url.host == "external-api.demo.kalshi.co"
    assert req.url.path == "/trade-api/v2/portfolio/balance"
    assert "KALSHI-ACCESS-SIGNATURE" in req.headers


async def test_dedicated_demo_host_rejects_private_dns(monkeypatch):
    import socket

    def _private(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _private)
    with pytest.raises(UnsafeURLError, match="resolves to a blocked"):
        await avalidate_api_base_url(DEMO_DEDICATED)
