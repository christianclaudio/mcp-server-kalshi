"""Outbound URL checks for the Kalshi API base and rules-PDF fetches.

Allowlists the hosts this server is meant to call, and rejects private, loopback,
link-local, and cloud-metadata targets before any GET. Hostnames that are not on
the allowlist are not resolved.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Collection
from typing import cast
from urllib.parse import urlsplit, urlunsplit

# Trade API hosts this fork already uses. See config.ENV_REST_BASE.
KALSHI_API_HOSTS: frozenset[str] = frozenset(
    {
        "demo-api.kalshi.co",
        "api.elections.kalshi.com",
    }
)

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
    "Kalshi API base URL must be https://demo-api.kalshi.co/trade-api/v2 "
    "or https://api.elections.kalshi.com/trade-api/v2"
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


def _assert_public_dns(host: str) -> None:
    """Reject allowlisted hosts that resolve to a blocked address."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"URL host {host!r} could not be resolved") from exc
    if not infos:
        raise UnsafeURLError(f"URL host {host!r} could not be resolved")
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


def validate_outbound_url(
    url: str,
    *,
    allowed_hosts: Collection[str],
    https_only: bool,
    resolve: bool,
) -> str:
    """Return a canonical URL or raise ``UnsafeURLError``.

    When ``resolve`` is true, allowlisted hostnames are looked up and rejected if
    any address is in a blocked range. IP literals and unexpected hosts are judged
    without a DNS lookup.
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
    if resolve:
        _assert_public_dns(host)

    path = parsed.path or "/"
    return urlunsplit((scheme, host, path, parsed.query, ""))


def validate_api_base_url(url: str, *, resolve: bool = False) -> str:
    """Validate a Kalshi REST base (https, allowlisted host, ``/trade-api/v2``)."""
    normalized = validate_outbound_url(
        url,
        allowed_hosts=KALSHI_API_HOSTS,
        https_only=True,
        resolve=resolve,
    )
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/")
    if parts.query:
        raise UnsafeURLError(_API_BASE)
    if path != "/trade-api/v2":
        raise UnsafeURLError(_API_BASE)
    return normalized.rstrip("/")


def validate_pdf_url(url: str, *, resolve: bool = True) -> str:
    """Validate a rules-PDF URL (http or https, allowlisted Kalshi document host)."""
    return validate_outbound_url(
        url,
        allowed_hosts=KALSHI_PDF_HOSTS,
        https_only=False,
        resolve=resolve,
    )
