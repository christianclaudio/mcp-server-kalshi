"""Outbound URL checks for the Kalshi API base and rules-PDF fetches.

Allowlists the hosts this server is meant to call, and rejects private, loopback,
link-local, and cloud-metadata targets before any GET. Hostnames that are not on
the allowlist are not resolved.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import re
import socket
import ssl
from collections.abc import Collection, Iterable
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx

# Trade API hosts: the shared demo and production hosts (config.ENV_REST_BASE) and the
# dedicated production and demo hosts (config.PROD_DEDICATED_REST_BASE and
# config.DEMO_DEDICATED_REST_BASE), which Kalshi added to its docs on 2026-05-07
# (https://docs.kalshi.com/changelog) and lists in the OpenAPI servers. The shared hosts
# "remain supported for compatibility".
KALSHI_API_HOSTS: frozenset[str] = frozenset(
    {
        "demo-api.kalshi.co",
        "api.elections.kalshi.com",
        "external-api.kalshi.com",
        "external-api.demo.kalshi.co",
    }
)

# Host of Kalshi's published OpenAPI spec, the only host scripts/check_openapi_drift.py
# fetches from.
KALSHI_SPEC_HOSTS: frozenset[str] = frozenset({"docs.kalshi.com"})

# Series contract_terms_url / contract_url hosts (prod assets CDN, demo staging
# bucket) plus the legacy public-docs bucket those PDFs were originally hosted on.
KALSHI_PDF_HOSTS: frozenset[str] = frozenset(
    {
        "assets.kalshi.com",
        "kalshi-public-docs.s3.amazonaws.com",
        "kalshi-public-docs.s3.us-east-1.amazonaws.com",
        "kalshi-public-docs.s3-us-east-1.amazonaws.com",
        "kalshi-public-docs-staging.s3.amazonaws.com",
        "kalshi-public-docs-staging.s3.us-east-1.amazonaws.com",
        "kalshi-public-docs-staging.s3-us-east-1.amazonaws.com",
    }
)

_METADATA_HOSTS: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data.ec2.internal",
    }
)

_BLOCKED_V4: tuple[ipaddress.IPv4Network, ...] = tuple(
    cast(ipaddress.IPv4Network, ipaddress.ip_network(cidr))
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT; includes some cloud metadata addresses
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, including 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

_BLOCKED_V6: tuple[ipaddress.IPv6Network, ...] = tuple(
    cast(ipaddress.IPv6Network, ipaddress.ip_network(cidr))
    for cidr in (
        "::/128",
        "::1/128",
        "fc00::/7",  # unique local, including fd00:ec2::254
        "fe80::/10",
        "ff00::/8",
    )
)

# Whitespace, controls, backslash, and fragments. Userinfo is checked on netloc.
_FORBIDDEN_URL_CHARS = re.compile(r"[\s\\\x00-\x1f\x7f#]")

_BLOCKED_ADDRESS = (
    "URL targets a blocked private, loopback, link-local, or metadata address"
)
_API_BASE = (
    "Kalshi API base URL must be https://demo-api.kalshi.co/trade-api/v2, "
    "https://api.elections.kalshi.com/trade-api/v2, "
    "https://external-api.kalshi.com/trade-api/v2, "
    "or https://external-api.demo.kalshi.co/trade-api/v2"
)


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to request."""


def _ipv4_blocked(ip: ipaddress.IPv4Address) -> bool:
    for network in _BLOCKED_V4:
        if ip in network:
            return True
    return False


def _ipv6_blocked(ip: ipaddress.IPv6Address) -> bool:
    mapped = ip.ipv4_mapped
    if mapped is not None:
        return _ipv4_blocked(mapped)
    for network in _BLOCKED_V6:
        if ip in network:
            return True
    return False


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        return _ipv6_blocked(ip)
    return _ipv4_blocked(ip)


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _port_allowed(scheme: str, port: int | None) -> bool:
    if port is None:
        return True
    if scheme == "https":
        return port == 443
    return port == 80


async def _assert_public_dns(host: str) -> tuple[str, ...]:
    """Reject allowlisted hosts that resolve to a blocked address.

    The lookup uses the running loop's ``getaddrinfo``, which runs in the default
    executor, so API and PDF hops do not block the event loop. Fail-closed: any
    lookup error or blocked answer rejects the URL. Returns the checked addresses and
    records them for ``host`` so the next request connects to one of them without a
    second lookup (see ``SSRFSafeAsyncTransport``).
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"URL host {host!r} could not be resolved") from exc
    if not infos:
        raise UnsafeURLError(f"URL host {host!r} could not be resolved")
    checked: list[str] = []
    for info in infos:
        raw = info[4][0]
        if not isinstance(raw, str):
            raise UnsafeURLError(f"URL host {host!r} could not be resolved")
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeURLError(f"URL host {host!r} could not be resolved") from exc
        if _is_blocked_ip(address):
            raise UnsafeURLError(
                f"URL host {host!r} resolves to a blocked private, "
                "loopback, link-local, or metadata address"
            )
        checked.append(str(address))
    ips = tuple(dict.fromkeys(checked))
    _record_checked(host, ips)
    return ips


def _parse_outbound_url(
    url: str,
    *,
    allowed_hosts: Collection[str],
    https_only: bool,
) -> tuple[str, str]:
    """Return ``(canonical_url, host)`` or raise ``UnsafeURLError``.

    IP literals and unexpected hosts are judged here without a DNS lookup.
    """
    if not isinstance(url, str):
        raise UnsafeURLError("URL must be a non-empty string")
    if url == "":
        raise UnsafeURLError("URL must be a non-empty string")
    if _FORBIDDEN_URL_CHARS.search(url):
        raise UnsafeURLError("URL contains disallowed characters")

    try:
        parsed = urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise UnsafeURLError("URL is not a valid http(s) URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UnsafeURLError(f"URL scheme {scheme!r} is not allowed")
    if https_only and scheme != "https":
        raise UnsafeURLError("URL scheme 'http' is not allowed; https is required")
    if "@" in parsed.netloc:
        raise UnsafeURLError("URL must not include userinfo")
    if not hostname:
        raise UnsafeURLError("URL must include a host")
    host = hostname.rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL must include a host")
    if not _port_allowed(scheme, port):
        raise UnsafeURLError("URL port is not allowed")
    if host in _METADATA_HOSTS:
        raise UnsafeURLError(_BLOCKED_ADDRESS)

    parsed_ip = _parse_ip(host)
    if parsed_ip is not None and _is_blocked_ip(parsed_ip):
        raise UnsafeURLError(_BLOCKED_ADDRESS)
    if host not in allowed_hosts:
        raise UnsafeURLError(f"URL host {host!r} is not allowlisted")

    path = parsed.path or "/"
    return urlunsplit((scheme, host, path, parsed.query, "")), host


def _parse_api_base(url: str) -> tuple[str, str]:
    normalized, host = _parse_outbound_url(
        url,
        allowed_hosts=KALSHI_API_HOSTS,
        https_only=True,
    )
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/")
    if parts.query:
        raise UnsafeURLError(_API_BASE)
    if path != "/trade-api/v2":
        raise UnsafeURLError(_API_BASE)
    return normalized.rstrip("/"), host


def validate_api_base_url(url: str) -> str:
    """Validate a Kalshi REST base without DNS (https, allowlisted host, ``/trade-api/v2``)."""
    normalized, _ = _parse_api_base(url)
    return normalized


async def avalidate_api_base_url(url: str) -> str:
    """Validate a Kalshi REST base and reject a blocked DNS answer.

    Use this on the request path. The lookup does not block the event loop.
    """
    normalized, host = _parse_api_base(url)
    await _assert_public_dns(host)
    return normalized


def validate_pdf_url(url: str) -> str:
    """Validate a rules-PDF URL without DNS (http or https, allowlisted document host)."""
    normalized, _ = _parse_outbound_url(
        url,
        allowed_hosts=KALSHI_PDF_HOSTS,
        https_only=False,
    )
    return normalized


async def avalidate_pdf_url(url: str) -> str:
    """Validate a rules-PDF URL and reject a blocked DNS answer.

    Use this before each PDF GET, including redirect hops. The lookup does not
    block the event loop.
    """
    normalized, host = _parse_outbound_url(
        url,
        allowed_hosts=KALSHI_PDF_HOSTS,
        https_only=False,
    )
    await _assert_public_dns(host)
    return normalized


def validate_spec_url(url: str) -> str:
    """Validate an OpenAPI spec URL without DNS (https, port 443, allowlisted docs host)."""
    normalized, _ = _parse_outbound_url(
        url,
        allowed_hosts=KALSHI_SPEC_HOSTS,
        https_only=True,
    )
    return normalized


async def avalidate_spec_url(url: str) -> str:
    """Validate an OpenAPI spec URL and reject a blocked DNS answer.

    The drift script calls this before its one GET, so a docs host that resolves to a
    private, loopback, link-local, or metadata address is refused before any request.
    """
    normalized, host = _parse_outbound_url(
        url,
        allowed_hosts=KALSHI_SPEC_HOSTS,
        https_only=True,
    )
    await _assert_public_dns(host)
    return normalized


# ---- Connect to the checked IP --------------------------------------------------------
# The transport checks the host once per request and hands httpcore the addresses that
# passed; the network backend dials one of them, so httpcore never resolves the name
# again. The URL keeps the hostname, so the Host header, SNI and certificate
# verification all use the original name, and httpcore pools connections per hostname.

_CHECKED_IPS: contextvars.ContextVar[dict[str, tuple[str, ...]] | None] = (
    contextvars.ContextVar("kalshi_checked_ips", default=None)
)
_PINNED_IPS: contextvars.ContextVar[dict[str, tuple[str, ...]] | None] = (
    contextvars.ContextVar("kalshi_pinned_ips", default=None)
)


def _record_checked(host: str, ips: tuple[str, ...]) -> None:
    """Remember the addresses that just passed the check, for this task's next request."""
    current = dict(_CHECKED_IPS.get() or {})
    current[host.lower()] = ips
    _CHECKED_IPS.set(current)


def _take_checked(host: str) -> tuple[str, ...] | None:
    """Consume a recorded check for ``host`` (one check serves one request)."""
    current = _CHECKED_IPS.get()
    if not current or host.lower() not in current:
        return None
    remaining = dict(current)
    ips = remaining.pop(host.lower())
    _CHECKED_IPS.set(remaining)
    return ips


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial only an address that passed ``_assert_public_dns`` for the requested host."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pins = _PINNED_IPS.get() or {}
        ips = pins.get(host.lower())
        if not ips:
            raise UnsafeURLError(
                f"URL host {host!r} has no checked address to connect to"
            )
        last: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeURLError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Async transport that connects only to an address checked for the request's host.

    If the caller just validated the host (``avalidate_*``), those addresses are used;
    otherwise the transport runs the same check itself. Either way there is exactly one
    lookup per request. The connection pool is built with a network backend that dials
    only those addresses, in resolver order, so a second DNS answer cannot send the
    request somewhere unchecked. A host with no checked address is refused at connect.
    Proxies and Unix sockets are not supported: both would bypass the pin. httpx
    ignores environment proxies whenever a transport is passed in, so every client
    built on this transport connects directly.
    """

    def __init__(
        self,
        verify: ssl.SSLContext | str | bool = True,
        cert: Any = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx.Limits | None = None,
        retries: int = 0,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> None:
        limits = limits or httpx.Limits(
            max_connections=100, max_keepalive_connections=20
        )
        ssl_context = httpx.create_ssl_context(
            verify=verify, cert=cert, trust_env=trust_env
        )
        super().__init__(verify=ssl_context, limits=limits)
        # httpcore documents ``network_backend`` as a public AsyncConnectionPool argument
        # (https://www.encode.io/httpcore/network-backends/). httpx.AsyncHTTPTransport
        # does not take one, so the pool it serves requests through is rebuilt here with
        # the same settings plus the pinned backend.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            local_address=local_address,
            socket_options=socket_options,
            network_backend=_PinnedNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Check the destination off the event loop, then connect only to a checked address."""
        # httpcore dials, and TLS checks, the ASCII (IDNA) form of the host, so the check
        # resolves that same name and the pin is keyed by it.
        hostname = request.url.raw_host.decode("ascii")
        if not hostname:
            return await super().handle_async_request(request)
        ips = _take_checked(hostname)
        if ips is None:
            ips = await _assert_public_dns(hostname)
            _take_checked(hostname)
        token = _PINNED_IPS.set({hostname.lower(): ips})
        try:
            return await super().handle_async_request(request)
        finally:
            _PINNED_IPS.reset(token)
