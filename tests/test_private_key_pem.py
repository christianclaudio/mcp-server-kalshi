"""KALSHI_PRIVATE_KEY PEM text: an alternative to KALSHI_PRIVATE_KEY_PATH.

Keys are generated in the test. Assertions that check for leaks use a base64
body line from that key and never print the PEM on success.
"""

from __future__ import annotations

import logging
import traceback

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

import mcp_server_kalshi.server as server_mod
from mcp_server_kalshi.config import Settings
from mcp_server_kalshi.errors import tool_error
from mcp_server_kalshi.kalshi_client.base import (
    BOTH_PRIVATE_KEY_SOURCES_ERROR,
    INVALID_PRIVATE_KEY_PEM_ERROR,
    MISSING_CREDENTIALS_ERROR,
    KalshiAuth,
    load_private_key_from_file,
    load_private_key_from_pem,
)
from mcp_server_kalshi.kalshi_client.client import KalshiAPIClient
from mcp_server_kalshi.server import KalshiFastMCPTool, ToolRegistry, handle_call_tool

DEMO = "https://demo-api.kalshi.co/trade-api/v2"


def _rsa_pem() -> tuple[rsa.RSAPrivateKey, str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return key, pem, _body_line(pem)


def _corrupt_pem(pem: str) -> str:
    """Break the first body line so the PEM cannot parse. Later lines stay."""
    lines = pem.splitlines()
    for index, line in enumerate(lines):
        if line and not line.startswith("-----"):
            lines[index] = "XXXX"
            break
    return "\n".join(lines)


def _body_line(pem: str) -> str:
    lines = [line for line in pem.splitlines() if line and not line.startswith("-----")]
    assert lines
    return lines[len(lines) // 2]


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    chunks = [caplog.text]
    for record in caplog.records:
        chunks.append(str(record.msg))
        chunks.append(repr(record.args))
        chunks.append(record.getMessage())
    return "\n".join(chunks)


def _exc_text(exc: BaseException) -> str:
    parts = [str(exc), repr(exc), "".join(traceback.format_exception(exc))]
    if exc.__cause__ is not None:
        parts.append(str(exc.__cause__))
        parts.append(repr(exc.__cause__))
    if exc.__context__ is not None:
        parts.append(str(exc.__context__))
        parts.append(repr(exc.__context__))
    return "\n".join(parts)


def _assert_hidden(marker: str, *texts: str) -> None:
    for text in texts:
        assert marker not in text


def _settings_text(settings: Settings) -> str:
    return "\n".join(
        [
            repr(settings),
            str(settings),
            repr(settings.__dict__),
            str(settings.model_dump()),
            settings.model_dump_json(),
            repr(settings.KALSHI_PRIVATE_KEY),
        ]
    )


def _client_text(client: KalshiAPIClient) -> str:
    return "\n".join([repr(client), str(client), repr(client.__dict__)])


def _same_rsa(loaded: rsa.RSAPrivateKey, key: rsa.RSAPrivateKey) -> None:
    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()


@pytest.fixture
def rsa_material() -> tuple[rsa.RSAPrivateKey, str, str]:
    return _rsa_pem()


@pytest.mark.parametrize(
    "style",
    ["newlines", "escaped-n", "escaped-crlf", "crlf"],
)
def test_pem_only_accepts_real_newlines_and_escapes(
    style: str,
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PEM-only settings load. Real newlines, ``\\n``, ``\\r\\n``, and CRLF."""
    key, pem, marker = rsa_material
    raw = {
        "newlines": pem,
        "escaped-n": pem.replace("\n", "\\n"),
        "escaped-crlf": pem.replace("\n", "\\r\\n"),
        "crlf": pem.replace("\n", "\r\n"),
    }[style]
    caplog.set_level(logging.DEBUG)
    settings = Settings(_env_file=None, KALSHI_API_KEY="key-id", KALSHI_PRIVATE_KEY=raw)
    assert settings.has_credentials is True
    assert settings.KALSHI_PRIVATE_KEY_PATH is None
    path, loaded_pem = settings.client_private_key()
    assert path is None
    assert loaded_pem is not None
    loaded = load_private_key_from_pem(loaded_pem)
    assert isinstance(loaded, rsa.RSAPrivateKey)
    _same_rsa(loaded, key)
    client = KalshiAPIClient(base_url=DEMO, api_key="key-id", private_key_pem=raw)
    assert client.has_credentials is True
    _assert_hidden(
        marker, _settings_text(settings), _client_text(client), _logged(caplog)
    )
    assert settings.model_dump()["KALSHI_PRIVATE_KEY"] == "**********"


def test_pem_from_environment_and_dotenv(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Environment and a ``.env`` file both supply the PEM and do not log it."""
    key, pem, marker = rsa_material
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem.replace("\n", "\\n"))
    caplog.set_level(logging.DEBUG)
    from_env = Settings(_env_file=None, KALSHI_API_KEY="key-id")
    env_pem = from_env.private_key_pem()
    assert env_pem is not None
    _same_rsa(load_private_key_from_pem(env_pem), key)
    _assert_hidden(marker, _settings_text(from_env), _logged(caplog))

    env_file = tmp_path / ".env"
    env_file.write_text(
        "KALSHI_API_KEY=from-file\nKALSHI_PRIVATE_KEY="
        + '"'
        + pem.replace("\n", "\\n")
        + '"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    from_file = Settings(_env_file=env_file)
    file_pem = from_file.private_key_pem()
    assert file_pem is not None
    _same_rsa(load_private_key_from_pem(file_pem), key)
    assert from_file.api_key_value() == "from-file"
    _assert_hidden(marker, _settings_text(from_file), _logged(caplog))


def test_ed25519_pem_loads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An Ed25519 PEM is accepted and is absent from repr and logs."""
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    marker = _body_line(pem)
    caplog.set_level(logging.DEBUG)
    loaded = load_private_key_from_pem(pem)
    assert isinstance(loaded, ed25519.Ed25519PrivateKey)
    raw = serialization.Encoding.Raw
    public = serialization.PublicFormat.Raw
    assert loaded.public_key().public_bytes(
        raw, public
    ) == key.public_key().public_bytes(raw, public)
    _assert_hidden(marker, repr(loaded), _logged(caplog))


def test_path_only_is_unchanged(
    tmp_path,
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A path and no PEM still loads the file and does not log the body."""
    key, pem, marker = rsa_material
    path = tmp_path / "rsa.pem"
    path.write_text(pem, encoding="ascii")
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        _env_file=None, KALSHI_API_KEY="key-id", KALSHI_PRIVATE_KEY_PATH=str(path)
    )
    assert settings.KALSHI_PRIVATE_KEY is None
    assert settings.has_credentials is True
    key_path, key_pem = settings.client_private_key()
    assert key_pem is None
    assert key_path == str(path)
    loaded = load_private_key_from_file(key_path)
    assert isinstance(loaded, rsa.RSAPrivateKey)
    _same_rsa(loaded, key)
    client = KalshiAPIClient(
        base_url=DEMO, api_key="key-id", private_key_path=str(path)
    )
    assert client.has_credentials is True
    _assert_hidden(
        marker, _settings_text(settings), _client_text(client), _logged(caplog)
    )


def test_blank_pem_counts_as_unset() -> None:
    """A blank PEM is unset, including when it is only ``\\n`` escapes."""
    blank = Settings(_env_file=None, KALSHI_PRIVATE_KEY="  \n  ")
    assert blank.KALSHI_PRIVATE_KEY is None
    assert blank.private_key_pem() is None
    assert blank.model_dump()["KALSHI_PRIVATE_KEY"] is None
    escaped = Settings(
        _env_file=None,
        KALSHI_API_KEY="key-id",
        KALSHI_PRIVATE_KEY="\\n\\r\\n",
        KALSHI_PRIVATE_KEY_PATH="/tmp/k.pem",
    )
    assert escaped.KALSHI_PRIVATE_KEY is None
    assert escaped.has_credentials is True
    assert escaped.client_private_key() == ("/tmp/k.pem", None)


def test_whitespace_path_with_pem_uses_the_pem(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A whitespace path is not a second source, so the PEM is used."""
    key, pem, marker = rsa_material
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        _env_file=None,
        KALSHI_API_KEY="key-id",
        KALSHI_PRIVATE_KEY=pem,
        KALSHI_PRIVATE_KEY_PATH="   ",
    )
    path, loaded_pem = settings.client_private_key()
    assert path is None
    assert loaded_pem is not None
    _same_rsa(load_private_key_from_pem(loaded_pem), key)
    _assert_hidden(marker, _settings_text(settings), _logged(caplog))


def test_both_sources_are_a_configuration_error(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kwargs, the environment, and a later assignment all name both variables."""
    _key, pem, marker = rsa_material
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError) as kwargs_exc:
        Settings(
            _env_file=None,
            KALSHI_PRIVATE_KEY=pem,
            KALSHI_PRIVATE_KEY_PATH="/tmp/k.pem",
        )
    assert str(kwargs_exc.value) == BOTH_PRIVATE_KEY_SOURCES_ERROR
    assert kwargs_exc.value.__cause__ is None
    _assert_hidden(marker, _exc_text(kwargs_exc.value), _logged(caplog))

    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/k.pem")
    with pytest.raises(ValueError) as env_exc:
        Settings(_env_file=None)
    assert str(env_exc.value) == (
        "Set only one of KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY, not both."
    )
    _assert_hidden(marker, _exc_text(env_exc.value), _logged(caplog))

    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    settings = Settings(_env_file=None, KALSHI_PRIVATE_KEY=pem)
    settings.KALSHI_PRIVATE_KEY_PATH = "/tmp/k.pem"
    with pytest.raises(ValueError) as later_exc:
        settings.client_private_key()
    assert str(later_exc.value) == BOTH_PRIVATE_KEY_SOURCES_ERROR
    _assert_hidden(marker, _exc_text(later_exc.value), _settings_text(settings))


def test_client_rejects_both_key_arguments(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The API client refuses a path and PEM together and does not echo the PEM."""
    _key, pem, marker = rsa_material
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError) as exc_info:
        KalshiAPIClient(
            base_url=DEMO, private_key_path="/tmp/k.pem", private_key_pem=pem
        )
    assert str(exc_info.value) == BOTH_PRIVATE_KEY_SOURCES_ERROR
    assert exc_info.value.__cause__ is None
    _assert_hidden(marker, _exc_text(exc_info.value), _logged(caplog))


def test_invalid_pem_error_omits_the_body(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed PEM raises the fixed error, with no chain and no body."""
    _key, pem, marker = rsa_material
    bad = _corrupt_pem(pem)
    assert marker in bad
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError) as exc_info:
        load_private_key_from_pem(bad)
    assert str(exc_info.value) == INVALID_PRIVATE_KEY_PEM_ERROR
    assert str(exc_info.value) == "KALSHI_PRIVATE_KEY is not a valid PEM private key"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    with pytest.raises(ValueError) as client_exc:
        KalshiAPIClient(base_url=DEMO, api_key="key-id", private_key_pem=bad)
    failure = tool_error(exc_info.value)
    _assert_hidden(
        marker,
        _exc_text(exc_info.value),
        _exc_text(client_exc.value),
        str(failure),
        repr(failure),
        _logged(caplog),
    )


def test_unsupported_pem_type_omits_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An EC PEM is rejected with the same fixed error and no body."""
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    marker = _body_line(pem)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError) as exc_info:
        load_private_key_from_pem(pem)
    assert str(exc_info.value) == INVALID_PRIVATE_KEY_PEM_ERROR
    assert exc_info.value.__cause__ is None
    _assert_hidden(marker, _exc_text(exc_info.value), _logged(caplog))


async def test_neither_source_where_required_names_both_options(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Authenticated calls with no key still fail, and the message names both options.

    Public construction is unchanged: a client with neither source builds and
    reports no credentials.
    """
    caplog.set_level(logging.DEBUG)
    client = KalshiAPIClient(base_url=DEMO)
    assert client.has_credentials is False
    with pytest.raises(ValueError) as exc_info:
        await client.get_balance()
    assert str(exc_info.value) == MISSING_CREDENTIALS_ERROR
    assert "KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY" in str(exc_info.value)
    _assert_hidden("-----BEGIN", _exc_text(exc_info.value), _logged(caplog))

    result = await handle_call_tool("get_balance", {})
    text = result[0].text
    assert text.startswith("Error in get_balance:")
    assert MISSING_CREDENTIALS_ERROR in text
    assert "-----BEGIN" not in text
    _assert_hidden("-----BEGIN", text, _logged(caplog))


async def test_tool_error_paths_omit_the_pem_body(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """stdio and FastMCP tool errors from a bad PEM do not contain the body."""
    _key, pem, marker = rsa_material
    bad = _corrupt_pem(pem)
    assert marker in bad

    async def _boom(*_args: object, **_kwargs: object) -> None:
        load_private_key_from_pem(bad)

    monkeypatch.setattr(server_mod.kalshi_client, "get_balance", _boom)
    caplog.set_level(logging.DEBUG)
    result = await handle_call_tool("get_balance", {})
    text = result[0].text
    assert INVALID_PRIVATE_KEY_PEM_ERROR in text
    assert text.startswith("Error in get_balance:")

    registered, handler = ToolRegistry._tools["get_balance"]
    tool = KalshiFastMCPTool(
        name="get_balance",
        description=registered.description or "",
        parameters=registered.input_schema,
        annotations=registered.annotations,
        handler=handler,
    )
    fastmcp_result = await tool.run({})
    assert fastmcp_result.is_error is True
    block = fastmcp_result.content[0]
    fastmcp_text = getattr(block, "text", "")
    assert INVALID_PRIVATE_KEY_PEM_ERROR in fastmcp_text
    _assert_hidden(marker, text, fastmcp_text, _logged(caplog))


def test_loaded_pem_signs_without_logging_the_body(
    rsa_material: tuple[rsa.RSAPrivateKey, str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A key loaded from PEM text still signs, and the body stays out of logs."""
    key, pem, marker = rsa_material
    caplog.set_level(logging.DEBUG)
    loaded = load_private_key_from_pem(pem.replace("\n", "\\n"))
    assert isinstance(loaded, rsa.RSAPrivateKey)
    _same_rsa(loaded, key)
    auth = KalshiAuth(loaded, "key-id")
    request = httpx.Request("GET", DEMO + "/portfolio/balance?limit=1")
    signed = next(auth.auth_flow(request))
    assert signed.headers["KALSHI-ACCESS-KEY"] == "key-id"
    _assert_hidden(
        marker,
        repr(loaded),
        repr(auth),
        repr(dict(signed.headers)),
        _logged(caplog),
    )
