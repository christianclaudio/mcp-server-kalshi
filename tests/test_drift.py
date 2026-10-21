"""Tests for OpenAPI drift monitor and parameter deprecation checking."""

import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_server_kalshi.ssrf import validate_spec_url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_openapi_drift import (
    ALLOWLIST_PATH,
    CLIENT_PATH,
    DEFAULT_SPEC_URL,
    ClientCall,
    check_drift,
    extract_client_calls,
    is_parameter_deprecated,
    load_allowlist,
    normalize_path,
    parse_spec,
)


def test_normalize_path():
    assert normalize_path("/trade-api/v2/markets/{ticker}") == "/markets/{}"
    assert normalize_path("events/{event_ticker}") == "/events/{}"
    assert normalize_path("/portfolio/orders?status=open") == "/portfolio/orders"


def test_is_parameter_deprecated():
    assert is_parameter_deprecated({"deprecated": True}) is True
    assert (
        is_parameter_deprecated({"description": "This parameter is [Deprecated] in v2"})
        is True
    )
    assert is_parameter_deprecated({"title": "Deprecated Param"}) is True
    assert (
        is_parameter_deprecated({"name": "status", "description": "Active orders"})
        is False
    )


def test_parse_spec():
    raw_spec = {
        "paths": {
            "/markets": {
                "get": {
                    "parameters": [
                        {"name": "limit", "in": "query", "required": False},
                        {"name": "old_param", "in": "query", "deprecated": True},
                    ]
                }
            },
            "/deprecated_route": {"post": {"deprecated": True, "parameters": []}},
        }
    }
    endpoints = parse_spec(raw_spec)
    assert ("GET", "/markets") in endpoints
    ep = endpoints[("GET", "/markets")]
    assert "limit" in ep.query_params
    assert "old_param" in ep.deprecated_params

    assert ("POST", "/deprecated_route") in endpoints
    assert endpoints[("POST", "/deprecated_route")].is_deprecated_route is True


def test_load_allowlist():
    with tempfile.NamedTemporaryFile("w+", delete=False) as f:
        f.write("# Comment line\n\nPOST /portfolio/withdrawals  # Justification\n")
        f_path = Path(f.name)

    try:
        allowed = load_allowlist(f_path)
        assert ("POST", "/portfolio/withdrawals") in allowed
    finally:
        f_path.unlink()


def test_check_drift_with_mock_spec(tmp_path):
    mock_spec_file = tmp_path / "mock_spec.yaml"
    mock_spec_file.write_text("""
paths:
  /exchange/status:
    get:
      parameters: []
  /exchange/schedule:
    get:
      parameters: []
  /markets:
    get:
      parameters: []
  /markets/{ticker}:
    get:
      parameters: []
  /markets/{ticker}/orderbook:
    get:
      parameters: []
  /series/{series_ticker}/markets/{ticker}/candlesticks:
    get:
      parameters: []
  /markets/trades:
    get:
      parameters: []
  /events:
    get:
      parameters: []
  /events/{event_ticker}:
    get:
      parameters: []
  /series:
    get:
      parameters: []
  /series/{series_ticker}:
    get:
      parameters: []
  /portfolio/balance:
    get:
      parameters: []
  /portfolio/positions:
    get:
      parameters: []
  /portfolio/fills:
    get:
      parameters: []
  /portfolio/settlements:
    get:
      parameters: []
  /portfolio/orders:
    get:
      parameters: []
  /portfolio/orders/{order_id}:
    get:
      parameters: []
  /portfolio/events/orders:
    post:
      parameters: []
  /portfolio/events/orders/{order_id}:
    delete:
      parameters: []
  /portfolio/events/orders/{order_id}/amend:
    post:
      parameters: []
  /portfolio/events/orders/{order_id}/decrease:
    post:
      parameters: []
  /portfolio/events/orders/batched:
    post:
      parameters: []
    delete:
      parameters: []
  /portfolio/summary/total_resting_order_value:
    get:
      parameters: []
  /search/tags_by_categories:
    get:
      parameters: []
  /search/filters_by_sport:
    get:
      parameters: []
  /milestones:
    get:
      parameters: []
  /milestones/{milestone_id}:
    get:
      parameters: []
  /live_data/events/{event_ticker}:
    get:
      parameters: []
  /multivariate_event_collections:
    get:
      parameters: []
  /multivariate_event_collections/{collection_ticker}:
    get:
      parameters: []
  /portfolio/order_groups:
    get:
      parameters: []
  /portfolio/order_groups/{order_group_id}:
    delete:
      parameters: []
""")

    code = check_drift(local_spec=mock_spec_file)
    assert code == 0


def test_client_call_path_property():
    call = ClientCall(
        method="GET",
        raw_path="/markets/{ticker}",
        normalized_path="/markets/{}",
    )
    assert call.path == "/markets/{ticker}"
    assert call.raw_path == "/markets/{ticker}"


def test_check_drift_deprecation_detection(tmp_path):
    mock_spec_file = tmp_path / "mock_spec.yaml"
    mock_spec_file.write_text("""
paths:
  /exchange/status:
    get:
      deprecated: true
      parameters: []
  /portfolio/orders:
    get:
      parameters:
        - name: status
          in: query
          deprecated: true
""")
    code = check_drift(local_spec=mock_spec_file)
    assert code == 1


def test_parameter_deprecation_error_formatting(tmp_path):
    mock_client = tmp_path / "mock_client.py"
    mock_client.write_text("""
class MockClient:
    async def get_test(self):
        await self.get("/markets", params={"deprecated_arg": "val"})
""")
    mock_spec = tmp_path / "mock_spec.yaml"
    mock_spec.write_text("""
paths:
  /markets:
    get:
      parameters:
        - name: deprecated_arg
          in: query
          deprecated: true
""")
    mock_allow = tmp_path / "allowlist.txt"
    mock_allow.write_text("")
    code = check_drift(
        local_spec=mock_spec,
        client_path=mock_client,
        allowlist_path=mock_allow,
    )
    assert code == 1


# ---- Spec URL host check (before any fetch) ------------------------------------------
# DNS is mocked at mcp_server_kalshi.ssrf.socket.getaddrinfo and httpx.AsyncClient is mocked, so
# these tests make no network calls.

_SPEC_YAML = "paths:\n  /exchange/status:\n    get:\n      parameters: []\n"


def _answers(*addrs: str):
    def _getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET6 if ":" in a else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, 0),
            )
            for a in addrs
        ]

    return _getaddrinfo


def _client_and_allowlist(tmp_path: Path) -> tuple[Path, Path]:
    client = tmp_path / "client.py"
    client.write_text(
        'class C:\n    async def s(self):\n        await self.get("/exchange/status")\n'
    )
    allow = tmp_path / "allowlist.txt"
    allow.write_text("")
    return client, allow


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://docs.kalshi.com/openapi.yaml", "https is required"),
        ("https://evil.example.com/openapi.yaml", "not allowlisted"),
        ("https://docs.kalshi.com.evil.com/openapi.yaml", "not allowlisted"),
        ("https://169.254.169.254/latest/meta-data", "blocked"),
        ("https://user@docs.kalshi.com/openapi.yaml", "userinfo"),
        ("https://docs.kalshi.com:8443/openapi.yaml", "port"),
    ],
)
def test_spec_url_rejected_before_dns_and_fetch(
    tmp_path, monkeypatch, capsys, url, reason
):
    client, allow = _client_and_allowlist(tmp_path)

    def _no_dns(*args, **kwargs):
        raise AssertionError("must not resolve a URL that fails policy")

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _no_dns)
    with patch("scripts.check_openapi_drift.httpx.AsyncClient") as get:
        code = check_drift(spec_url=url, client_path=client, allowlist_path=allow)
    assert code == 2
    get.assert_not_called()
    out = capsys.readouterr().out
    assert "Refused to fetch spec URL" in out
    assert reason in out


@pytest.mark.parametrize(
    "answer", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "fd00::1"]
)
def test_spec_url_rejected_when_docs_host_resolves_private(
    tmp_path, monkeypatch, capsys, answer
):
    client, allow = _client_and_allowlist(tmp_path)
    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo", _answers("104.18.0.1", answer)
    )
    with patch("scripts.check_openapi_drift.httpx.AsyncClient") as get:
        code = check_drift(
            spec_url=DEFAULT_SPEC_URL, client_path=client, allowlist_path=allow
        )
    assert code == 2
    get.assert_not_called()
    assert "resolves to a blocked" in capsys.readouterr().out


def test_spec_url_unresolvable_is_refused(tmp_path, monkeypatch):
    client, allow = _client_and_allowlist(tmp_path)

    def _gaierror(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _gaierror)
    with patch("scripts.check_openapi_drift.httpx.AsyncClient") as get:
        code = check_drift(
            spec_url=DEFAULT_SPEC_URL, client_path=client, allowlist_path=allow
        )
    assert code == 2
    get.assert_not_called()


def test_default_spec_url_passes_policy():
    assert validate_spec_url(DEFAULT_SPEC_URL) == DEFAULT_SPEC_URL


def test_repo_allowlist_lists_only_routes_the_client_calls():
    """Stale entries (routes the client never calls) hide nothing and must be removed."""
    called = {(c.method, c.normalized_path) for c in extract_client_calls(CLIENT_PATH)}
    stale = load_allowlist(ALLOWLIST_PATH) - called
    assert stale == set()
    assert ("POST", "/portfolio/withdrawals") not in load_allowlist(ALLOWLIST_PATH)
    assert ("POST", "/portfolio/deposits") not in load_allowlist(ALLOWLIST_PATH)
