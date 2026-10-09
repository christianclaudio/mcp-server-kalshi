import re
from typing import Any

# Regex patterns for credential and secret scrubbing
RE_RSA_KEY = re.compile(
    r"-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA )?PRIVATE KEY-----"
)
RE_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.~+/]+=*")
RE_KALSHI_HEADERS = re.compile(
    r"(?i)(KALSHI-ACCESS-KEY|KALSHI-ACCESS-SIGNATURE)\s*[:=]\s*['\"]?[A-Za-z0-9_\-+/=]+['\"]?"
)
RE_GENERIC_SECRETS = re.compile(
    r"(?i)(api[_-]?key|client[_-]?secret|password|private[_-]?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.+=/]{8,}[\"']?"
)
# House standard patterns 1-9, applied in this order with r"\1[REDACTED]".
RE_TOKEN_PATTERNS = [
    # Bearer value: base64url and base64 characters (``~``, ``+``, ``/``) plus ``=``
    # padding.
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.~+/]{8,}=*", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(client[_-]?secret[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}", re.IGNORECASE),
    # api/access/refresh/auth/id/session tokens as key=value, key: value, an
    # ``X-Auth-Token:`` header and JSON ("key": "value", also backslash-escaped inside an
    # already-serialized JSON string).
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token(?:\\?[\"'])?\s*[:=]\s*"
        r"(?:\\?[\"'])?)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # The same keys URL-encoded (``access_token%3D...``); the value stops at an encoded
    # ``%26`` (&) or ``%23`` (#), so the parameters after it survive.
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token%3D)"
        r"(?:[^\s\"'\\&,;#%]|%(?!26|23))+",
        re.IGNORECASE,
    ),
    # ``Authorization: Token <value>`` scheme, also as a quoted JSON or dict entry.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?token\s+)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # JSON ``"token": "value"``; the opening quote right before ``token`` keeps keys such
    # as ``"next_token"`` and ``"page_token"`` untouched.
    re.compile(
        r"(?i)(\\?[\"']token\\?[\"']\s*:\s*\\?[\"'])[^\s\"'\\&,;]+", re.IGNORECASE
    ),
    # Bare ``token`` key with ``:`` or ``=``, optional spaces and an optional opening quote
    # (``token=``, ``token: x``, ``token = x``, ``token: "x"``); the lookbehind keeps
    # ``page_token``, ``next_token``, ``csrf_token`` and ``max_tokens`` untouched.
    re.compile(
        r"(?i)((?<![A-Za-z0-9_])token\s*[:=]\s*(?:\\?[\"'])?)[^\s\"'\\&#]+",
        re.IGNORECASE,
    ),
]


def redact_secrets(text: str) -> str:
    """Scrub private keys, API keys, signatures, Bearer tokens, and API tokens from text."""
    if not text:
        return text
    scrubbed = RE_RSA_KEY.sub("[REDACTED RSA PRIVATE KEY]", text)
    scrubbed = RE_BEARER_TOKEN.sub("Bearer [REDACTED]", scrubbed)
    scrubbed = RE_KALSHI_HEADERS.sub(r"\1: [REDACTED]", scrubbed)
    scrubbed = RE_GENERIC_SECRETS.sub(r"\1: [REDACTED]", scrubbed)
    for pattern in RE_TOKEN_PATTERNS:
        scrubbed = pattern.sub(r"\1[REDACTED]", scrubbed)
    return scrubbed


class KalshiAPIError(Exception):
    """Raised when the Kalshi API returns a non-2xx response, carrying the sanitized error body."""

    def __init__(
        self,
        status_code: int,
        method: str,
        path: str,
        body: Any,
    ) -> None:
        self.status_code = status_code
        self.method = method
        self.path = path
        self.body = body
        sanitized_body = redact_secrets(str(body)) if isinstance(body, str) else body
        super().__init__(
            f"Kalshi API {status_code} on {method} {path}: {sanitized_body}"
        )
