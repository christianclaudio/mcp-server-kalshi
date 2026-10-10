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
import os
import socket
import sys

# Every environment variable ``Settings`` in ``config.py`` reads. ``BASE_URL`` has no prefix,
# and ``KALSHI_API_KEY_ID`` is an alias for ``KALSHI_API_KEY``. ``KALSHI_MCP_AUTH_TOKEN`` and
# the unauthenticated-bind opt-in are handled by ``_clear_http_auth`` below.
KALSHI_SETTINGS_ENV = (
    "KALSHI_ENV",
    "BASE_URL",
    "KALSHI_API_KEY",
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_READONLY",
    "KALSHI_MCP_STATELESS_HTTP",
    "KALSHI_MCP_JSON_RESPONSE",
)


def _marker_expressions(argv: list[str]) -> list[str]:
    """Every ``-m`` expression on the command line (``-m EXPR``, ``-m=EXPR``, ``-mEXPR``)."""
    exprs = []
    for i, arg in enumerate(argv):
        if arg == "-m":
            if i + 1 < len(argv):
                exprs.append(argv[i + 1])
        elif arg.startswith("-m="):
            exprs.append(arg[3:])
        elif arg.startswith("-m") and len(arg) > 2:
            exprs.append(arg[2:])
    return exprs


def _live_run_requested(argv: list[str]) -> bool:
    """True only when the run selects exactly the live ``e2e`` tests (``-m e2e``).

    Live tests need the developer's real credentials, so they are exempt from clearing.
    Any other marker expression, such as ``not (e2e)``, counts as offline and clears.
    """
    exprs = _marker_expressions(argv)
    return bool(exprs) and exprs[-1].strip() == "e2e"


# Clear at import, before ``server.py`` builds its module-level ``settings`` from the
# environment, so a credential exported in the shell can't leak into the offline suite.
# ``.env`` loading is also turned off, so a ``.env`` file in the working directory can't
# supply credentials or ``KALSHI_ENV`` to the module-level ``settings`` either.
from mcp_server_kalshi.config import Settings  # noqa: E402

_OFFLINE_RUN = not _live_run_requested(sys.argv)
if _OFFLINE_RUN:
    for _name in KALSHI_SETTINGS_ENV:
        os.environ.pop(_name, None)
    Settings.model_config["env_file"] = None

import httpx  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import mcp_server_kalshi.server as server_mod  # noqa: E402
from mcp_server_kalshi.auth import (  # noqa: E402
    ALLOW_UNAUTHENTICATED_BIND_ENV,
    AUTH_TOKEN_ENV,
)
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient  # noqa: E402

BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.fixture(autouse=True)
def _clear_kalshi_env(monkeypatch, request):
    """Start every offline test with no Kalshi settings in the environment or ``.env``.

    Tests that need a value set it with ``monkeypatch.setenv``. Live ``e2e`` tests opt out
    because they run against the real API with the developer's credentials.
    """
    if request.node.get_closest_marker("e2e"):
        return
    for name in KALSHI_SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


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
