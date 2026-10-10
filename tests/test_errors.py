"""Tests for credential scrubbing and KalshiAPIError sanitization."""

from typing import Any
from unittest.mock import patch

import pytest

from mcp_server_kalshi import server
from mcp_server_kalshi.errors import KalshiAPIError, redact_secrets


def test_redact_secrets_empty_or_none():
    assert redact_secrets("") == ""
    assert redact_secrets(None) == ""  # type: ignore[arg-type]


def test_redact_secrets_scrubs_keys_and_tokens():
    text_with_rsa = (
        "Error in request: -----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y8\n"
        "-----END RSA PRIVATE KEY-----\n"
        "failed."
    )
    scrubbed = redact_secrets(text_with_rsa)
    assert "-----BEGIN RSA PRIVATE KEY-----" not in scrubbed
    assert "MIIEowIBAAKCAQEA0Y8" not in scrubbed
    assert scrubbed == "Error in request: [REDACTED]\nfailed."

    text_with_bearer = "Authorization: Bearer my_secret_token_12345"
    assert "my_secret_token" not in redact_secrets(text_with_bearer)
    assert "Bearer [REDACTED]" in redact_secrets(text_with_bearer)

    text_with_headers = "KALSHI-ACCESS-KEY: secret_api_key_abc"
    assert "secret_api_key_abc" not in redact_secrets(text_with_headers)

    text_with_api_key = '{"api_key": "supersecretkey123"}'
    assert "supersecretkey123" not in redact_secrets(text_with_api_key)


def test_kalshi_api_error_sanitization():
    err = KalshiAPIError(
        status_code=401,
        method="POST",
        path="/portfolio/orders",
        body="Bearer secret_token_abc is invalid",
    )
    assert err.status_code == 401
    assert err.method == "POST"
    assert err.path == "/portfolio/orders"
    assert "secret_token_abc" not in str(err)
    assert "Bearer [REDACTED]" in str(err)

    dict_err = KalshiAPIError(
        status_code=400,
        method="GET",
        path="/test",
        body={"error": "invalid parameter"},
    )
    assert "invalid parameter" in str(dict_err)


def test_redact_secrets_token_forms():
    """api/access/refresh tokens and a bare token= query parameter are redacted in every form."""
    cases = {
        "api_token=SECRET1": "api_token=[REDACTED]",
        "api-token: SECRET1": "api-token: [REDACTED]",
        "access_token: SECRET2": "access_token: [REDACTED]",
        "refresh_token=SECRET4": "refresh_token=[REDACTED]",
        '{"refresh_token": "SECRET4"}': '{"refresh_token": "[REDACTED]"}',
        "{'access_token': 'SECRET2'}": "{'access_token': '[REDACTED]'}",
        '{"m": "{\\"access_token\\": \\"SECRET5\\"}"}': (
            '{"m": "{\\"access_token\\": \\"[REDACTED]\\"}"}'
        ),
        "GET https://api.example.com/x?token=SECRET3&page=2": (
            "GET https://api.example.com/x?token=[REDACTED]&page=2"
        ),
        "url=/x?a=1&TOKEN=SECRET6": "url=/x?a=1&TOKEN=[REDACTED]",
        "refresh_token=a.b-c_d/e+f==": "refresh_token=[REDACTED]",
    }
    for raw, expected in cases.items():
        assert redact_secrets(raw) == expected, raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("auth_token=SECRET7", "auth_token=[REDACTED]", id="auth_token"),
        pytest.param(
            '{"id_token": "SECRET8"}', '{"id_token": "[REDACTED]"}', id="id_token"
        ),
        pytest.param(
            "session-token: SECRET9&x=1",
            "session-token: [REDACTED]&x=1",
            id="session_token",
        ),
        pytest.param(
            "X-Auth-Token: SECRET10\nAccept: */*",
            "X-Auth-Token: [REDACTED]\nAccept: */*",
            id="x_auth_token_header",
        ),
        pytest.param(
            "Authorization: Token SECRET11 rejected",
            "Authorization: Token [REDACTED] rejected",
            id="authorization_token",
        ),
        pytest.param(
            "{'Authorization': 'Token SECRET12'}",
            "{'Authorization': 'Token [REDACTED]'}",
            id="authorization_token_dict",
        ),
        pytest.param(
            '{"token": "SECRET13"}', '{"token": "[REDACTED]"}', id="json_token"
        ),
        pytest.param(
            '{"m": "{\\"token\\": \\"SECRET14\\"}"}',
            '{"m": "{\\"token\\": \\"[REDACTED]\\"}"}',
            id="json_token_escaped",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET15%26x%3D1%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%26x%3D1%23frag",
            id="url_encoded_access_token",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET21%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%23frag",
            id="url_encoded_access_token_fragment",
        ),
        pytest.param(
            "q=api_token%3DS16%26refresh_token%3DS17%26auth_token%3DS18"
            "%26id_token%3DS19%26session_token%3DS20",
            "q=api_token%3D[REDACTED]%26refresh_token%3D[REDACTED]"
            "%26auth_token%3D[REDACTED]%26id_token%3D[REDACTED]"
            "%26session_token%3D[REDACTED]",
            id="url_encoded_other_keys",
        ),
    ],
)
def test_redact_secrets_more_token_forms(raw: str, expected: str) -> None:
    """auth/id/session tokens, X-Auth-Token, Authorization: Token, JSON "token" and %3D."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("token: SECRET22", "token: [REDACTED]", id="token_colon_space"),
        pytest.param("token:SECRET23", "token:[REDACTED]", id="token_colon"),
        pytest.param(
            "token = SECRET24", "token = [REDACTED]", id="token_spaced_equals"
        ),
        pytest.param(
            "Token: abcdefgh12345", "Token: [REDACTED]", id="token_capitalized"
        ),
        pytest.param(
            'token: "SECRET25"', 'token: "[REDACTED]"', id="token_colon_double_quote"
        ),
        pytest.param(
            "token='SECRET26'", "token='[REDACTED]'", id="token_equals_single_quote"
        ),
    ],
)
def test_redact_secrets_bare_token_colon_and_spaced(raw: str, expected: str) -> None:
    """A bare token key takes ``:`` or ``=``, optional spaces and a quote; all are kept."""
    assert redact_secrets(raw) == expected


def test_redact_secrets_bearer_base64_tail() -> None:
    """A bearer value with ``~``, ``/``, ``+`` and ``=`` padding is redacted with no tail left."""
    assert redact_secrets("Bearer abc.def~ghi/jk+l==") == "Bearer [REDACTED]"


def test_redact_secrets_token_query_stops_at_fragment() -> None:
    """A bare ``token=`` query value stops at a literal ``#``, so the fragment survives."""
    assert redact_secrets("/x?token=SECRET#frag") == "/x?token=[REDACTED]#frag"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            '{"password": "fakepw99"}',
            '{"password": "[REDACTED]"}',
            id="json_password",
        ),
        pytest.param(
            '{"api_key": "FAKEKEY12345"}',
            '{"api_key": "[REDACTED]"}',
            id="json_api_key",
        ),
        pytest.param(
            '{"client_secret": "FAKESEC12345"}',
            '{"client_secret": "[REDACTED]"}',
            id="json_client_secret",
        ),
        pytest.param(
            '{"private_key": "FAKEPRIVKEY123"}',
            '{"private_key": "[REDACTED]"}',
            id="json_private_key",
        ),
    ],
)
def test_redact_secrets_generic_keeps_json_key(raw: str, expected: str) -> None:
    """The generic secret pattern keeps the key, its quotes and the closing brace."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected", "secret"),
    [
        pytest.param(
            "password=pw12", "password=[REDACTED]", "pw12", id="house4_password_short"
        ),
        pytest.param(
            'password: "Fake!pw#9"',
            'password: "[REDACTED]"',
            "Fake!pw#9",
            id="house4_password_quoted",
        ),
        pytest.param(
            "api_key FAKEKEY12345",
            "api_key [REDACTED]",
            "FAKEKEY12345",
            id="house2_api_key_space",
        ),
        pytest.param(
            "client_secret FAKESEC12345",
            "client_secret [REDACTED]",
            "FAKESEC12345",
            id="house3_client_secret_space",
        ),
    ],
)
def test_redact_secrets_house_key_patterns(
    raw: str, expected: str, secret: str
) -> None:
    """House patterns 2-4 redact forms the generic secret pattern does not reach."""
    scrubbed = redact_secrets(raw)
    assert scrubbed == expected
    assert secret not in scrubbed


def test_redact_secrets_leaves_token_words_alone():
    """Ordinary words and pagination fields that contain "token" are not redacted."""
    for text in (
        "tokenizer failed on input",
        "next_page_token_count=5",
        "page_token=abc123 is a pagination cursor",
        "next_token=abc123&x=1",
        "csrf_token=abc123#frag",
        "refresh_token_expires_in=3600",
        "the token expired",
        "max_tokens=1024",
        '{"page_token": "x", "next_token": "x", "csrf_token": "x", "max_tokens": 5}',
        "X-Auth-Token-Expires: 2026-10-09T00:00:00Z",
        "session_token_ttl=3600",
        "id_token_hint_count=2",
        "Token x is invalid",
        "Authorization failed: token expired",
        "max_tokens: 5",
        "next_token: abc",
        "page_token: abc",
        "X-Auth-Token-Expires: 5",
        '{"token": null}',
    ):
        assert redact_secrets(text) == text, text


_TOKEN_SECRETS = (
    "SECRET1",
    "SECRET2",
    "SECRET3",
    "SECRET4",
    "SECRET10",
    "SECRET11",
    "SECRET15",
)


def _token_error() -> KalshiAPIError:
    """A 401 whose JSON body echoes the request URL and tokens (dict bodies are not pre-redacted)."""
    return KalshiAPIError(
        status_code=401,
        method="GET",
        path="/portfolio/balance",
        body={
            "error": "GET https://api.example.com/x?token=SECRET3 rejected: api_token=SECRET1",
            "access_token": "SECRET2",
            "detail": '{"refresh_token": "SECRET4"}',
            "headers": "X-Auth-Token: SECRET10 Authorization: Token SECRET11",
            "callback": "https%3A%2F%2Fh%2Fcb%3Fsession_token%3DSECRET15%26x%3D1",
        },
    )


async def test_tool_error_path_redacts_token_forms():
    """A tool failure carrying every token form reaches the client redacted (isError true)."""
    tool = await server.mcp.get_tool("get_balance")
    assert tool is not None

    async def failing(_: dict[str, Any]) -> Any:
        raise _token_error()

    with patch.object(server.ToolRegistry, "get_handler", return_value=failing):
        res = await tool.run({})
        wire = await server._req_call_tool(
            None, server.types.CallToolRequestParams(name="get_balance", arguments={})
        )
    for text in (res.content[0].text, wire.content[0].text):
        assert text.startswith("Error in get_balance: Kalshi API 401")
        assert "?token=[REDACTED]" in text
        assert "api_token=[REDACTED]" in text
        assert "'access_token': '[REDACTED]'" in text
        assert '"refresh_token": "[REDACTED]"' in text
        assert "X-Auth-Token: [REDACTED] Authorization: Token [REDACTED]" in text
        assert "session_token%3D[REDACTED]%26x%3D1" in text
        for secret in _TOKEN_SECRETS:
            assert secret not in text
    assert res.is_error is True
    assert wire.is_error is True
