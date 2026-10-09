import re
from typing import Any

# Regex patterns for credential and secret scrubbing
RE_RSA_KEY = re.compile(
    r"-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA )?PRIVATE KEY-----"
)
RE_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]+")
RE_KALSHI_HEADERS = re.compile(
    r"(?i)(KALSHI-ACCESS-KEY|KALSHI-ACCESS-SIGNATURE)\s*[:=]\s*['\"]?[A-Za-z0-9_\-+/=]+['\"]?"
)
RE_GENERIC_SECRETS = re.compile(
    r"(?i)(api[_-]?key|client[_-]?secret|password|private[_-]?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.+=/]{8,}[\"']?"
)
# api_token / access_token / refresh_token as key=value, key: value and JSON
# ("key": "value", also backslash-escaped inside an already-serialized JSON string).
RE_TOKEN_KEYS = re.compile(
    r"(?i)((?:api|access|refresh)[_-]?token(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?)"
    r"[^\s\"'\\&,;]+",
    re.IGNORECASE,
)
# Bare ``token=`` query parameter; the lookbehind keeps ``page_token=`` and
# ``next_page_token_count`` untouched.
RE_TOKEN_QUERY = re.compile(
    r"(?i)((?<![A-Za-z0-9_])token=)[^\s\"'\\&#]+", re.IGNORECASE
)


def redact_secrets(text: str) -> str:
    """Scrub private keys, API keys, signatures, Bearer tokens, and API tokens from text."""
    if not text:
        return text
    scrubbed = RE_RSA_KEY.sub("[REDACTED RSA PRIVATE KEY]", text)
    scrubbed = RE_BEARER_TOKEN.sub("Bearer [REDACTED]", scrubbed)
    scrubbed = RE_KALSHI_HEADERS.sub(r"\1: [REDACTED]", scrubbed)
    scrubbed = RE_GENERIC_SECRETS.sub(r"\1: [REDACTED]", scrubbed)
    scrubbed = RE_TOKEN_KEYS.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = RE_TOKEN_QUERY.sub(r"\1[REDACTED]", scrubbed)
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
