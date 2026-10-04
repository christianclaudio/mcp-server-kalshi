import base64
import time
from collections.abc import Generator
from typing import Any

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from ..errors import KalshiAPIError, redact_secrets
from ..ssrf import avalidate_api_base_url, validate_api_base_url

# Parsed key object, not the PEM banner. PKCS#8 RSA and Ed25519 both use
# "BEGIN PRIVATE KEY".
KalshiPrivateKey = rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey


def load_private_key_from_file(file_path: str) -> KalshiPrivateKey:
    """Load an RSA or Ed25519 private key object from a PEM file.

    The type is taken from the parsed key. A PKCS#8 RSA key and an Ed25519 key
    both start with ``BEGIN PRIVATE KEY``, so the banner is not a type check.
    """
    with open(file_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(), password=None, backend=default_backend()
        )
    if isinstance(private_key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        return private_key
    raise ValueError(
        f"Expected an RSA or Ed25519 private key in {file_path}, "
        f"got {type(private_key).__name__}"
    )


def sign_pss_text(private_key: rsa.RSAPrivateKey, text: str) -> str:
    """Sign text with RSA-PSS and base64-encode it.

    SHA-256, MGF1-SHA256, salt length equal to the digest length. This is the
    RSA scheme Kalshi uses for the KALSHI-ACCESS-SIGNATURE header.
    """
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def sign_text(private_key: KalshiPrivateKey, text: str) -> str:
    """Sign the Kalshi pre-sign string and return a base64 signature.

    Ed25519 signs the message bytes directly (RFC 8032). RSA uses
    :func:`sign_pss_text`.
    """
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        signature = private_key.sign(text.encode("utf-8"))
        return base64.b64encode(signature).decode("utf-8")
    return sign_pss_text(private_key, text)


class KalshiAuth(httpx.Auth):
    """Signs each request with the Kalshi API-key headers.

    The signed message is ``timestamp_ms + METHOD + path`` where ``path`` includes the
    ``/trade-api/v2`` prefix but EXCLUDES the query string. Ed25519 signs that
    message directly; RSA uses RSA-PSS with a digest-length salt.
    """

    def __init__(self, private_key: KalshiPrivateKey, api_key: str) -> None:
        self._private_key = private_key
        self._api_key = api_key

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        method = request.method
        # raw_path includes the query string; Kalshi signs the path only.
        path = request.url.raw_path.decode().split("?", 1)[0]
        timestamp = str(int(time.time() * 1000))
        msg_string = timestamp + method + path
        signature = sign_text(self._private_key, msg_string)

        request.headers["KALSHI-ACCESS-KEY"] = self._api_key
        request.headers["KALSHI-ACCESS-SIGNATURE"] = signature
        request.headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp
        yield request


class BaseAPIClient:
    """A minimal async HTTP client for the Kalshi Trade API.

    ``base_url`` must be the fully-qualified API base including the version prefix, e.g.
    ``https://demo-api.kalshi.co/trade-api/v2``. Only the demo and production Kalshi API
    hosts are accepted. Endpoint paths passed to the request helpers are relative to that
    (e.g. ``/portfolio/balance``).

    Authentication is optional: when both ``api_key`` and ``private_key_path`` are
    provided every request is signed with that key (RSA-PSS or Ed25519); otherwise
    requests are sent unsigned, which is fine for public market-data endpoints.
    Authenticated endpoints raise a clear error if creds are missing.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        private_key_path: str | None = None,
        timeout: int = 30,
    ) -> None:
        self._base_url: str = validate_api_base_url(base_url)
        self._timeout: int = timeout
        self._api_key: str | None = api_key
        self._private_key: KalshiPrivateKey | None = (
            load_private_key_from_file(private_key_path) if private_key_path else None
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def has_credentials(self) -> bool:
        return self._api_key is not None and self._private_key is not None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the persistent httpx client (attaching auth when available)."""
        if self._client is None:
            # Inline (rather than self.has_credentials) so the types narrow to non-None.
            auth = (
                KalshiAuth(self._private_key, self._api_key)
                if self._private_key is not None and self._api_key is not None
                else None
            )
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                auth=auth,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _require_auth(self) -> None:
        if not self.has_credentials:
            raise ValueError(
                "This action requires Kalshi credentials. Set KALSHI_API_KEY and "
                "KALSHI_PRIVATE_KEY_PATH in the server environment."
            )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        # Re-check the base (it is operator-configurable) and reject a DNS answer
        # that points at a private, link-local, or metadata address. The lookup
        # is awaited so it does not block the event loop.
        self._base_url = await avalidate_api_base_url(self._base_url)
        client = self._ensure_client()
        url = self._base_url + path
        response = await client.request(method, url, params=params, json=json)
        if response.is_error:
            try:
                body: Any = response.json()
            except Exception:
                body = redact_secrets(response.text)
            raise KalshiAPIError(response.status_code, method, path, body)
        if not response.content:
            return {}
        return response.json()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("DELETE", path, params=params, json=json)

    async def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self._request("PATCH", path, json=json)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def close(self) -> None:
        await self.aclose()

    async def __aenter__(self) -> "BaseAPIClient":
        self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
