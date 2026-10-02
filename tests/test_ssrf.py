"""SSRF policy: Kalshi host allowlists and blocked private ranges. No network."""

import socket
from urllib.parse import urlsplit

import pytest

from mcp_server_kalshi.config import ENV_REST_BASE
from mcp_server_kalshi.ssrf import (
    KALSHI_API_HOSTS,
    KALSHI_PDF_HOSTS,
    UnsafeURLError,
    validate_api_base_url,
    validate_pdf_url,
)


def _dns(*addrs: str):
    def _getaddrinfo(host, port, *args, **kwargs):
        infos = []
        for addr in addrs:
            if ":" in addr:
                sockaddr = (addr, 0, 0, 0)
                family = socket.AF_INET6
            else:
                sockaddr = (addr, 0)
                family = socket.AF_INET
            infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return infos

    return _getaddrinfo


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1.",
        "https://10.0.0.5/x",
        "https://192.168.1.1/a.pdf",
        "https://172.16.0.1/a.pdf",
        "https://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/",
        "https://0.0.0.0/",
        "https://100.64.0.1/",
        "https://100.100.100.200/",
        "https://224.0.0.1/",
        "https://255.255.255.255/",
        "https://192.0.2.1/",
        "https://198.51.100.1/",
        "https://203.0.113.1/",
        "https://198.18.0.1/",
        "https://[::1]/",
        "https://[::]/",
        "https://[fd00::1]/",
        "https://[fe80::1]/",
        "https://[fd00:ec2::254]/",
        "https://[ff02::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[::ffff:10.0.0.1]/",
        "https://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://metadata.goog/",
        "https://instance-data.ec2.internal/latest/meta-data/",
    ],
)
def test_rejects_private_link_local_and_metadata(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="blocked"):
        validate_pdf_url(url, resolve=False)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://assets.kalshi.com/a.pdf",
        "ftp://assets.kalshi.com/a.pdf",
        "javascript:alert(1)",
        "assets.kalshi.com/a.pdf",
        "https:///a.pdf",
    ],
)
def test_rejects_weird_schemes_and_missing_host(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="scheme|host"):
        validate_pdf_url(url, resolve=False)


def test_rejects_non_string_and_empty() -> None:
    with pytest.raises(UnsafeURLError, match="non-empty"):
        validate_pdf_url(None)  # type: ignore[arg-type]
    with pytest.raises(UnsafeURLError, match="non-empty"):
        validate_pdf_url("")


@pytest.mark.parametrize(
    "url",
    [
        "https://assets.kalshi.com/a b.pdf",
        "https://assets.kalshi.com/a\n.pdf",
        "https://assets.kalshi.com/a\\b.pdf",
        "https://assets.kalshi.com/a.pdf#page=1",
        "https://assets.kalshi.com/a\x7f.pdf",
    ],
)
def test_rejects_disallowed_characters(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="disallowed characters"):
        validate_pdf_url(url, resolve=False)


def test_rejects_dot_only_host() -> None:
    with pytest.raises(UnsafeURLError, match="must include a host"):
        validate_pdf_url("https://.../a.pdf", resolve=False)


def test_public_ip_literals_are_not_allowlisted() -> None:
    for url in (
        "https://8.8.8.8/a.pdf",
        "https://[2600:9000:2508:2800:1b:b291:7f00:93a1]/a.pdf",
        "https://[::ffff:8.8.8.8]/a.pdf",
    ):
        with pytest.raises(UnsafeURLError, match="not allowlisted") as exc:
            validate_pdf_url(url, resolve=False)
        assert "blocked" not in str(exc.value)


def test_rejects_userinfo_port_and_lookalike_hosts() -> None:
    with pytest.raises(UnsafeURLError, match="userinfo"):
        validate_pdf_url("https://user:pass@assets.kalshi.com/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="userinfo"):
        validate_pdf_url("https://:pass@assets.kalshi.com/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="port"):
        validate_pdf_url("https://assets.kalshi.com:8443/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="port"):
        validate_pdf_url("http://assets.kalshi.com:8080/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="port"):
        validate_pdf_url("https://assets.kalshi.com:0/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="not a valid"):
        validate_pdf_url("https://assets.kalshi.com:99999/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="not a valid"):
        validate_pdf_url("https://assets.kalshi.com:abc/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="not allowlisted"):
        validate_pdf_url("https://assets.kalshi.com.evil.example/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="not allowlisted"):
        validate_pdf_url("https://evil.assets.kalshi.com/a.pdf", resolve=False)
    with pytest.raises(UnsafeURLError, match="not allowlisted"):
        validate_pdf_url("https://evil.s3.amazonaws.com/a.pdf", resolve=False)


@pytest.mark.parametrize("host", sorted(KALSHI_PDF_HOSTS))
def test_allows_every_pdf_host(host: str) -> None:
    url = f"https://{host}/contract_terms/x.pdf"
    assert validate_pdf_url(url, resolve=False) == url


def test_pdf_url_normalization() -> None:
    assert (
        validate_pdf_url(
            "https://ASSETS.KALSHI.COM:443/contract_terms/NBA.pdf", resolve=False
        )
        == "https://assets.kalshi.com/contract_terms/NBA.pdf"
    )
    assert (
        validate_pdf_url("https://assets.kalshi.com./a.pdf", resolve=False)
        == "https://assets.kalshi.com/a.pdf"
    )
    assert (
        validate_pdf_url("http://assets.kalshi.com:80/a.pdf", resolve=False)
        == "http://assets.kalshi.com/a.pdf"
    )
    assert validate_pdf_url("https://assets.kalshi.com", resolve=False) == (
        "https://assets.kalshi.com/"
    )
    assert (
        validate_pdf_url("https://assets.kalshi.com/a.pdf?token=1", resolve=False)
        == "https://assets.kalshi.com/a.pdf?token=1"
    )


def test_api_bases_match_configured_environments() -> None:
    hosts = {urlsplit(url).hostname for url in ENV_REST_BASE.values()}
    assert hosts == set(KALSHI_API_HOSTS)
    for url in ENV_REST_BASE.values():
        assert validate_api_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://demo-api.kalshi.co/trade-api/v2",
            "https://demo-api.kalshi.co/trade-api/v2",
        ),
        (
            "https://demo-api.kalshi.co/trade-api/v2/",
            "https://demo-api.kalshi.co/trade-api/v2",
        ),
        (
            "https://DEMO-API.KALSHI.CO:443/trade-api/v2/",
            "https://demo-api.kalshi.co/trade-api/v2",
        ),
        (
            "https://api.elections.kalshi.com/trade-api/v2",
            "https://api.elections.kalshi.com/trade-api/v2",
        ),
    ],
)
def test_allows_kalshi_api_bases(url: str, expected: str) -> None:
    assert validate_api_base_url(url) == expected


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("http://demo-api.kalshi.co/trade-api/v2", "https is required"),
        ("file://demo-api.kalshi.co/trade-api/v2", "scheme"),
        ("https://127.0.0.1/trade-api/v2", "blocked"),
        ("https://169.254.169.254/latest/meta-data/", "blocked"),
        ("https://proxy.internal/trade-api/v2", "not allowlisted"),
        ("https://demo-api.kalshi.co.evil.com/trade-api/v2", "not allowlisted"),
        ("https://user@demo-api.kalshi.co/trade-api/v2", "userinfo"),
        ("https://demo-api.kalshi.co:8443/trade-api/v2", "port"),
        ("https://demo-api.kalshi.co/trade-api/v2?x=1", "trade-api/v2"),
        ("https://demo-api.kalshi.co/trade-api/v2/extra", "trade-api/v2"),
        ("https://demo-api.kalshi.co", "trade-api/v2"),
        ("https://demo-api.kalshi.co/other", "trade-api/v2"),
    ],
)
def test_rejects_bad_api_bases(url: str, match: str) -> None:
    with pytest.raises(UnsafeURLError, match=match):
        validate_api_base_url(url)


def test_does_not_resolve_hosts_that_fail_policy(monkeypatch) -> None:
    calls: list[str] = []

    def _boom(host, port, *args, **kwargs):
        calls.append(host)
        raise AssertionError("DNS should not run")

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _boom)
    with pytest.raises(UnsafeURLError):
        validate_pdf_url("https://evil.example/a.pdf", resolve=True)
    with pytest.raises(UnsafeURLError):
        validate_pdf_url("http://127.0.0.1/", resolve=True)
    assert (
        validate_pdf_url("https://assets.kalshi.com/a.pdf", resolve=False)
        == "https://assets.kalshi.com/a.pdf"
    )
    assert calls == []


def test_rejects_allowlisted_host_that_resolves_private(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _dns("8.8.8.8", "10.1.2.3"),
    )
    with pytest.raises(UnsafeURLError, match="resolves to a blocked"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")


def test_allows_allowlisted_host_with_public_v4_and_v6(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _dns("108.138.64.101", "2600:9000:2508:2800:1b:b291:7f00:93a1"),
    )
    assert (
        validate_pdf_url("https://assets.kalshi.com/a.pdf")
        == "https://assets.kalshi.com/a.pdf"
    )


def test_rejects_link_local_zone_and_mapped_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _dns("fe80::1%eth0"),
    )
    with pytest.raises(UnsafeURLError, match="resolves to a blocked"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")

    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _dns("::ffff:10.0.0.1"),
    )
    with pytest.raises(UnsafeURLError, match="resolves to a blocked"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")


def test_rejects_dns_failures(monkeypatch) -> None:
    def _gaierror(host, port, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _gaierror)
    with pytest.raises(UnsafeURLError, match="could not be resolved"):
        validate_api_base_url("https://demo-api.kalshi.co/trade-api/v2", resolve=True)

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _dns())
    with pytest.raises(UnsafeURLError, match="could not be resolved"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")

    monkeypatch.setattr(
        "mcp_server_kalshi.ssrf.socket.getaddrinfo",
        _dns("not-an-ip"),
    )
    with pytest.raises(UnsafeURLError, match="could not be resolved"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")

    def _numeric(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (0, 0))]

    monkeypatch.setattr("mcp_server_kalshi.ssrf.socket.getaddrinfo", _numeric)
    with pytest.raises(UnsafeURLError, match="could not be resolved"):
        validate_pdf_url("https://assets.kalshi.com/a.pdf")
