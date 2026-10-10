"""HTTP bearer auth and the non-localhost bind policy (ported from template v1.6.0)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import mcp_server_kalshi.server as srv
from mcp_server_kalshi.auth import (
    ALLOW_UNAUTHENTICATED_BIND_ENV,
    AUTH_TOKEN_ENV,
    SharedTokenVerifier,
    allow_unauthenticated_bind,
    is_localhost,
    read_auth_token,
)

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "correct-horse-battery-staple-69"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}
PUBLIC_HTTP = (
    "--transport",
    "streamable-http",
    "--host",
    "0.0.0.0",
    "--allowed-host",
    "mcp.internal",
)


def _clean_env(**extra: str) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("KALSHI", "SNOWFLAKE"))
    }
    env.update({"KALSHI_ENV": "demo", **extra})
    return env


def _fresh_auth_type(**extra: str) -> str:
    """Import the server in a fresh interpreter and report the type of ``mcp.auth``."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mcp_server_kalshi.server as s; print(type(s.mcp.auth).__name__)",
        ],
        env=_clean_env(**extra),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        cwd=str(ROOT),
    )
    return out.stdout.split()[-1]


@pytest.fixture
def run_args(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub both serve paths so ``main()`` records what it would have served."""
    captured: dict[str, Any] = {}

    async def _http(**kwargs: Any) -> None:
        captured.update(kwargs, transport="streamable-http")

    async def _stdio() -> None:
        captured["transport"] = "stdio"

    monkeypatch.setattr(srv, "run_streamable_http", _http)
    monkeypatch.setattr(srv, "run", _stdio)
    return captured


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["mcp-server-kalshi", *argv])
    srv.main()


# ── blank token is unset; verifier refuses blank ──────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_whitespace_token_is_unset(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, value)
    assert read_auth_token() == ""
    with caplog.at_level(logging.WARNING, logger="mcp_server_kalshi.server"):
        _main(monkeypatch, "--transport", "streamable-http")
    assert run_args["transport"] == "streamable-http"
    assert srv.mcp.auth is None
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


def test_token_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, f"  {TOKEN}\n")
    assert read_auth_token() == TOKEN


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_verifier_refuses_blank_expected_token(value: str) -> None:
    with pytest.raises(ValueError, match=AUTH_TOKEN_ENV):
        SharedTokenVerifier(value)


async def test_verifier_contract() -> None:
    verifier = SharedTokenVerifier(f" {TOKEN} ")
    assert TOKEN not in repr(verifier)
    assert "REDACTED" in repr(verifier)
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token("   ") is None
    assert await verifier.verify_token("wrong") is None
    ok = await verifier.verify_token(TOKEN)
    assert ok is not None and ok.client_id == "mcp-server-kalshi-shared-token"


@pytest.mark.parametrize("value", ["   ", "\t\n"])
def test_build_with_blank_token_has_no_auth(value: str) -> None:
    """Revert proof for the strip: a blank token must not reach SharedTokenVerifier.

    Without the strip, import raises ``ValueError`` and the subprocess fails.
    """
    assert _fresh_auth_type(**{AUTH_TOKEN_ENV: value}) == "NoneType"


def test_build_attaches_token_from_env() -> None:
    assert _fresh_auth_type(**{AUTH_TOKEN_ENV: f"  {TOKEN}\n"}) == "SharedTokenVerifier"


@pytest.mark.parametrize("value", ["off", "y", "enabled", "2"])
def test_non_strict_opt_in_value_still_refuses_public_bind(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], value: str
) -> None:
    """Revert proof for the strict opt-in: only 1/true/yes/on opt in."""
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, *PUBLIC_HTTP)
    assert exc.value.code == 2
    assert run_args == {}


def test_readme_docker_example_keeps_tokens_off_the_command_line() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"-e {AUTH_TOKEN_ENV}=" not in readme
    assert re.search(r"-e\"?,? \"?KALSHI_API_KEY=", readme) is None
    assert f"-e {AUTH_TOKEN_ENV}" in readme
    assert "--env-file" in readme
    assert "--allowed-host mcp.example.com" in readme


# ── bind policy ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST"]
)
def test_is_localhost_true(host: str) -> None:
    assert is_localhost(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "10.0.0.5", "mcp.internal", "127.0.0.2"]
)
def test_is_localhost_false(host: str) -> None:
    assert not is_localhost(host)


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_allow_unauthenticated_bind_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert allow_unauthenticated_bind()


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "2", "off", "y", "enabled", "yes please", "truee"],
)
def test_allow_unauthenticated_bind_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert not allow_unauthenticated_bind()


@pytest.mark.parametrize(
    "argv",
    [
        PUBLIC_HTTP,
        ("--transport", "streamable-http", "--host", "10.0.0.5"),
        ("--transport", "streamable-http", "--host", "mcp.internal"),
    ],
)
def test_non_localhost_without_token_refuses(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, "   ")
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, *argv)
    assert exc.value.code == 2
    assert run_args == {}
    err = capsys.readouterr().err
    assert "refusing to serve" in err
    assert AUTH_TOKEN_ENV in err and ALLOW_UNAUTHENTICATED_BIND_ENV in err


def test_refusal_message_has_no_secret(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "0")
    monkeypatch.setenv("KALSHI_API_KEY", "upstream-secret-69")
    with pytest.raises(SystemExit):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert "upstream-secret-69" not in capsys.readouterr().err


def test_non_localhost_with_token_starts_with_auth(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with caplog.at_level(logging.INFO, logger="mcp_server_kalshi.server"):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert run_args["host"] == "0.0.0.0"
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)
    assert "authentication is on" in caplog.text
    assert TOKEN not in caplog.text


def test_existing_verifier_is_kept(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    existing = SharedTokenVerifier(TOKEN)
    monkeypatch.setattr(srv.mcp, "auth", existing)
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    _main(monkeypatch, *PUBLIC_HTTP)
    assert srv.mcp.auth is existing


def test_non_localhost_with_opt_in_starts_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "1")
    with caplog.at_level(logging.WARNING, logger="mcp_server_kalshi.server"):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert run_args["transport"] == "streamable-http"
    assert srv.mcp.auth is None
    assert f"{ALLOW_UNAUTHENTICATED_BIND_ENV} is set" in caplog.text
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_localhost_without_token_allowed(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], host: str
) -> None:
    _main(monkeypatch, "--transport", "streamable-http", "--host", host)
    assert run_args["transport"] == "streamable-http"
    assert srv.mcp.auth is None


def test_stdio_ignores_bind_policy(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    _main(monkeypatch, "--transport", "stdio", "--host", "0.0.0.0")
    assert run_args == {"transport": "stdio"}
    assert srv.mcp.auth is None


def _dockerfile_default_argv() -> list[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    last_stage = re.split(r"^FROM\s", text, flags=re.MULTILINE)[-1]
    found: dict[str, list[str]] = {}
    for kind in ("ENTRYPOINT", "CMD"):
        lines = re.findall(rf"^{kind}\s+(\[.*\])\s*$", last_stage, flags=re.MULTILINE)
        if lines:
            found[kind] = json.loads(lines[-1])
    argv = found.get("ENTRYPOINT", []) + found.get("CMD", [])
    assert argv, "Dockerfile runtime stage has no exec-form ENTRYPOINT/CMD"
    return argv


def test_image_default_command_is_stdio(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    argv = _dockerfile_default_argv()
    assert argv[0] == "mcp-server-kalshi"
    monkeypatch.setattr("sys.argv", argv)
    srv.main()
    assert run_args == {"transport": "stdio"}


def test_readme_image_http_command_attaches_token(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--host 0.0.0.0" in readme
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    _main(
        monkeypatch,
        "--transport",
        "streamable-http",
        "--host",
        "0.0.0.0",
        "--allowed-host",
        "mcp.example.com",
    )
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)


def test_wildcard_with_token_still_needs_allowed_host(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "--transport", "streamable-http", "--host", "0.0.0.0")
    assert exc.value.code == 2
    assert run_args == {}
    assert "--allowed-host is required" in capsys.readouterr().err


def test_serve_time_attach_when_token_set_after_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert srv.mcp.auth is None
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    srv._apply_http_auth(argparse.ArgumentParser(), "streamable-http", "0.0.0.0")
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)


# ── HTTP 401 / 401 / 200 on every entry point ────────────────────────────────


async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as c:
            yield c


async def test_open_endpoint_without_token() -> None:
    """With no token the endpoint answers (the behavior ``main`` refuses for public binds)."""
    app = srv.mcp.streamable_http_app(stateless_http=True, json_response=True)
    async for client in _client(app):
        res = await client.post("/mcp", json=INITIALIZE, headers=HEADERS)
        assert res.status_code == 200


@pytest.mark.parametrize(
    ("auth_header", "expected"),
    [
        (None, 401),
        ("Bearer wrong-token", 401),
        ("Bearer    ", 401),
        (f"Bearer {TOKEN}", 200),
    ],
)
@pytest.mark.parametrize("factory", ["streamable_http_app", "http_app"])
async def test_http_apps_enforce_token(
    monkeypatch: pytest.MonkeyPatch,
    auth_header: str | None,
    expected: int,
    factory: str,
) -> None:
    monkeypatch.setattr(srv.mcp, "auth", SharedTokenVerifier(TOKEN))
    app = getattr(srv.mcp, factory)(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=INITIALIZE, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text
        if expected == 200:
            assert res.json()["result"]["serverInfo"]["name"] == "kalshi-server"
        else:
            assert res.headers["www-authenticate"].lower().startswith("bearer")


def test_fastmcp_run_entry_point_enforces_env_token() -> None:
    """``fastmcp run src/mcp_server_kalshi/server.py:mcp``: a fresh import serving ``mcp``."""
    code = (
        "import asyncio, httpx, json, sys\n"
        "import mcp_server_kalshi.server as s\n"
        "app = s.mcp.http_app(transport='http', stateless_http=True, json_response=True)\n"
        "async def go():\n"
        "    async with app.router.lifespan_context(app):\n"
        "        t = httpx.ASGITransport(app=app)\n"
        "        async with httpx.AsyncClient(transport=t, base_url='http://127.0.0.1') as c:\n"
        "            h = json.loads(sys.argv[2])\n"
        "            body = json.loads(sys.argv[1])\n"
        "            a = await c.post('/mcp', json=body, headers=h)\n"
        "            h['Authorization'] = 'Bearer ' + sys.argv[3]\n"
        "            b = await c.post('/mcp', json=body, headers=h)\n"
        "            print(int(a.status_code), int(b.status_code))\n"
        "asyncio.run(go())\n"
    )
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            json.dumps(INITIALIZE),
            json.dumps(HEADERS),
            TOKEN,
        ],
        env=_clean_env(**{AUTH_TOKEN_ENV: TOKEN}),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        cwd=str(ROOT),
    )
    assert out.stdout.split()[-2:] == ["401", "200"]


import socket  # noqa: E402

# conftest patches ``socket.getaddrinfo`` (via ``mcp_server_kalshi.ssrf.socket``) to a public
# address for every test; the real resolver is kept here so the live probe reaches 127.0.0.1.
_REAL_GETADDRINFO = socket.getaddrinfo


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve_and_probe(
    monkeypatch: pytest.MonkeyPatch, target: list[str]
) -> tuple[int, int]:
    """Start ``fastmcp run <target>`` over HTTP and POST initialize without/with the token."""
    import time

    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    port = _free_port()
    fastmcp_bin = str(Path(sys.executable).parent / "fastmcp")
    cmd = [fastmcp_bin, "run", *target, "--transport", "http"]
    cmd += ["--host", "127.0.0.1", "--port", str(port)]
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=_clean_env(**{AUTH_TOKEN_ENV: TOKEN}),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None, (
                proc.stderr.read().decode() if proc.stderr else ""
            )
            try:
                bare = httpx.post(url, json=INITIALIZE, headers=HEADERS, timeout=2)
                break
            except httpx.TransportError:
                assert time.monotonic() < deadline, "fastmcp run did not start in 30s"
                time.sleep(0.2)
        auth = {**HEADERS, "Authorization": f"Bearer {TOKEN}"}
        ok = httpx.post(url, json=INITIALIZE, headers=auth, timeout=5)
        assert TOKEN not in bare.text + ok.text
        return bare.status_code, ok.status_code
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
            proc.wait()


def test_fastmcp_run_file_entry_point_starts_and_enforces_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented ``fastmcp run src/mcp_server_kalshi/server.py:mcp`` really starts."""
    assert _serve_and_probe(monkeypatch, ["src/mcp_server_kalshi/server.py:mcp"]) == (
        401,
        200,
    )


def test_fastmcp_json_resolves_and_enforces_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fastmcp.json`` points at a real file and object, and ``fastmcp run`` serves it."""
    cfg = json.loads((ROOT / "fastmcp.json").read_text(encoding="utf-8"))
    assert (ROOT / cfg["source"]["path"]).is_file()
    assert cfg["source"]["entrypoint"] == "mcp"
    target = ["fastmcp.json", "--skip-env"]
    assert _serve_and_probe(monkeypatch, target) == (401, 200)
