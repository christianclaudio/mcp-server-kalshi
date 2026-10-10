"""Shared fixtures for the offline test suite.

Two building blocks used across the new tests:

- ``make_client`` — build a real ``KalshiAPIClient`` wired to an injected
  ``httpx.MockTransport`` so endpoint methods and the base HTTP/error layer are exercised
  with zero network. The client's lazily-created ``_client`` is pre-set, so ``_ensure_client``
  returns our mock-transport client unchanged.
- ``FakeClient`` — a duck-typed stand-in for the module-level ``kalshi_client`` singleton in
  ``server.py``. Records every call and returns canned data (or raises), letting handler tests
  assert wiring without touching the network.
"""

import json
import socket

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import mcp_server_kalshi.server as server_mod
from mcp_server_kalshi.auth import ALLOW_UNAUTHENTICATED_BIND_ENV, AUTH_TOKEN_ENV
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient

BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.fixture(autouse=True)
def _clear_http_auth(monkeypatch):
    """Start every test with HTTP auth off.

    The module-level ``mcp`` reads ``KALSHI_MCP_AUTH_TOKEN`` when it is built at import, so a
    token or opt-in exported in the developer's shell would change what every test serves.
    Both variables are cleared and the module-level server's ``auth`` is reset to ``None``.
    """
    monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNAUTHENTICATED_BIND_ENV, raising=False)
    monkeypatch.setattr(server_mod.mcp, "auth", None)


@pytest.fixture(autouse=True)
def _public_dns_for_ssrf_checks(monkeypatch, request):
    """Keep SSRF DNS checks offline for unit tests.

    Allowlisted hosts resolve to a public address so request-time checks do not
    touch the network. Live ``e2e`` tests opt out. Tests that reject private DNS
    answers patch ``getaddrinfo`` themselves.
    """
    if request.node.get_closest_marker("e2e"):
        return

    def _public_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _public_getaddrinfo,
    )


def json_response(payload, status_code: int = 200) -> httpx.Response:
    """Build an httpx.Response with a JSON body (for use inside a responder)."""
    return httpx.Response(status_code, json=payload)


@pytest.fixture
async def make_client():
    """Return a factory: ``make_client(responder, **client_kwargs) -> (client, requests)``.

    ``responder`` is called with each ``httpx.Request`` and must return an ``httpx.Response``.
    ``requests`` is a list that accumulates every request the client sent, for assertions.
    All clients created are closed on teardown.
    """
    clients = []

    def _make(responder, **client_kwargs):
        client = KalshiAPIClient(base_url=BASE_URL, **client_kwargs)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return responder(request)

        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=BASE_URL
        )
        clients.append(client)
        return client, requests

    yield _make

    for client in clients:
        await client.aclose()


@pytest.fixture(scope="session")
def rsa_key_file(tmp_path_factory) -> str:
    """Write a throwaway RSA private key to a PEM file and return its path.

    Lets tests construct an authenticated client (so ``_require_auth`` passes) without any
    real Kalshi credentials.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("keys") / "rsa.pem"
    path.write_bytes(pem)
    return str(path)


class FakeClient:
    """Duck-typed stand-in for ``server.kalshi_client``.

    Any attribute access returns an async method that records ``(name, args, kwargs)`` and
    returns the canned response configured for that name (default ``{"ok": True}``). A canned
    value that is an ``Exception`` is raised; a callable is invoked with the call args.
    """

    def __init__(self, **responses):
        self.calls: list[tuple] = []
        self._responses = responses

    def __getattr__(self, name):
        # Only reached for names not set in __init__ (calls/_responses), i.e. API methods.
        async def _method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            resp = self._responses.get(name, {"ok": True})
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(*args, **kwargs)
            return resp

        return _method

    def called(self, name: str) -> bool:
        return any(c[0] == name for c in self.calls)


def handler_result(text_content_list) -> dict:
    """Decode a handler's ``list[TextContent]`` JSON return into a dict."""
    return json.loads(text_content_list[0].text)
