from functools import lru_cache
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .kalshi_client.base import (
    BOTH_PRIVATE_KEY_SOURCES_ERROR,
    normalize_private_key_pem,
)
from .ssrf import validate_api_base_url

# Kalshi Trade API base URLs. See https://docs.kalshi.com/getting_started/api_environments
ENV_REST_BASE = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
}
# Kalshi's dedicated production REST host (changelog 2026-05-07,
# https://docs.kalshi.com/changelog). Opt in with BASE_URL; the default stays on the
# shared host above, which Kalshi says remains supported.
PROD_DEDICATED_REST_BASE = "https://external-api.kalshi.com/trade-api/v2"
# Kalshi's dedicated demo REST host, from the same changelog entry. Opt in with BASE_URL;
# the demo default stays on the shared demo host. Its URL contains "demo", so
# is_production is False and get_environment reports DEMO.
DEMO_DEDICATED_REST_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"
ENV_WS_BASE = {
    "prod": "wss://api.elections.kalshi.com/trade-api/ws/v2",
    "demo": "wss://demo-api.kalshi.co/trade-api/ws/v2",
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables and/or a .env file.

    Safety-by-default: the server targets Kalshi's DEMO (sandbox) environment unless
    ``KALSHI_ENV=prod`` is set explicitly. Credentials are optional so that public
    market/rules tools work with no keys configured; authenticated tools fail with a
    clear error when creds are absent.

    The private key is either a PEM file (``KALSHI_PRIVATE_KEY_PATH``) or the PEM
    text itself (``KALSHI_PRIVATE_KEY``). A blank PEM counts as unset. Setting
    both is a configuration error. The PEM text is a ``SecretStr``: it is left
    out of ``repr`` and masked in ``model_dump``.
    """

    KALSHI_ENV: Literal["demo", "prod"] = Field(
        default="demo",
        description="Which Kalshi environment to target. 'demo' (sandbox, default) or 'prod' (real money).",
    )
    # Optional explicit override of the REST base URL. When unset it is derived from KALSHI_ENV.
    # Only the demo and production Kalshi API hosts are accepted (see ssrf.KALSHI_API_HOSTS).
    BASE_URL: str | None = Field(
        default=None,
        description=(
            "Explicit REST base URL override. Only "
            "https://demo-api.kalshi.co/trade-api/v2, "
            "https://api.elections.kalshi.com/trade-api/v2, or Kalshi's dedicated "
            "hosts https://external-api.kalshi.com/trade-api/v2 (production) and "
            "https://external-api.demo.kalshi.co/trade-api/v2 (demo). "
            "Derived from KALSHI_ENV when unset."
        ),
    )
    KALSHI_API_KEY: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KALSHI_API_KEY", "KALSHI_API_KEY_ID"),
        description="Kalshi API key ID (required only for authenticated tools).",
    )
    KALSHI_PRIVATE_KEY_PATH: str | None = Field(
        default=None,
        description=(
            "Path to the Kalshi RSA or Ed25519 private key PEM file "
            "(required only for authenticated tools). "
            "Alternative: KALSHI_PRIVATE_KEY. Do not set both."
        ),
    )
    KALSHI_PRIVATE_KEY: SecretStr | None = Field(
        default=None,
        repr=False,
        description=(
            "PEM text of the Kalshi RSA or Ed25519 private key, instead of "
            "KALSHI_PRIVATE_KEY_PATH. Real newlines and the literal \\n and "
            "\\r\\n escapes are accepted. A blank value counts as unset. "
            "Do not set both. Prefer a secret store."
        ),
    )
    KALSHI_READONLY: bool = Field(
        default=False,
        description="When true, restricts server startup strictly to read-only tools.",
    )
    KALSHI_MCP_STATELESS_HTTP: bool = Field(
        default=False,
        description="Enable stateless Streamable HTTP transport mode (Spec 2026-07-28 / SEP-1049).",
    )
    KALSHI_MCP_JSON_RESPONSE: bool = Field(
        default=False,
        description="Return direct application/json responses instead of SSE text/event-stream.",
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("KALSHI_PRIVATE_KEY", mode="before")
    @classmethod
    def _normalize_private_key(cls, value: object) -> object:
        # Never raise here: a ValidationError would keep the raw PEM as input.
        if not isinstance(value, str):
            return value
        text = normalize_private_key_pem(value)
        if not text:
            return None
        return text

    @field_serializer("KALSHI_PRIVATE_KEY")
    def _serialize_private_key(self, value: SecretStr | None) -> str | None:
        if value is None:
            return None
        return "**********"

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self._reject_both_private_key_sources()

    def _reject_both_private_key_sources(self) -> None:
        path = self.KALSHI_PRIVATE_KEY_PATH
        if (
            self.KALSHI_PRIVATE_KEY is not None
            and path is not None
            and path.strip() != ""
        ):
            raise ValueError(BOTH_PRIVATE_KEY_SOURCES_ERROR)

    @field_validator("BASE_URL")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        # Structural check only. Request time resolves the host and rejects private answers.
        return validate_api_base_url(stripped)

    @property
    def rest_base_url(self) -> str:
        """Resolved REST base URL (explicit override wins, else derived from env)."""
        if self.BASE_URL:
            return self.BASE_URL
        return validate_api_base_url(ENV_REST_BASE[self.KALSHI_ENV])

    @property
    def ws_base_url(self) -> str:
        return ENV_WS_BASE[self.KALSHI_ENV]

    @property
    def has_credentials(self) -> bool:
        return self.KALSHI_API_KEY is not None and (
            self.KALSHI_PRIVATE_KEY_PATH is not None
            or self.KALSHI_PRIVATE_KEY is not None
        )

    def private_key_pem(self) -> str | None:
        """PEM text when ``KALSHI_PRIVATE_KEY`` is set, otherwise ``None``.

        The return value is the key. Do not log it, format it into an error, or
        include it in a ``repr``.
        """
        if self.KALSHI_PRIVATE_KEY is None:
            return None
        return self.KALSHI_PRIVATE_KEY.get_secret_value()

    def client_private_key(self) -> tuple[str | None, str | None]:
        """``(private_key_path, private_key_pem)`` for the API client.

        At most one side is set. A PEM wins over a blank path. Both a PEM and a
        non-blank path raise :data:`BOTH_PRIVATE_KEY_SOURCES_ERROR`.
        """
        self._reject_both_private_key_sources()
        pem = self.private_key_pem()
        if pem is not None:
            return None, pem
        return self.KALSHI_PRIVATE_KEY_PATH, None

    @property
    def is_production(self) -> bool:
        # Treat an explicit prod override URL as production too.
        if self.BASE_URL:
            return "demo" not in self.BASE_URL.lower()
        return self.KALSHI_ENV == "prod"

    @property
    def env_label(self) -> str:
        return "PROD (real money)" if self.is_production else "DEMO (sandbox)"

    def api_key_value(self) -> str | None:
        return self.KALSHI_API_KEY.get_secret_value() if self.KALSHI_API_KEY else None


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (constructed lazily on first use)."""
    return Settings()
