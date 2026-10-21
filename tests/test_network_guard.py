"""The autouse guard in ``conftest.py`` refuses real network connects in offline tests.

The targets are TEST-NET-1 (RFC 5737) addresses, and the guard raises before any dial.
"""

import socket

import httpx
import pytest

# pytest loads conftest.py as its own module, so match the message, not the class.
GUARD = "offline test tried a real network connect"


def test_guard_refuses_a_sync_connect() -> None:
    with pytest.raises(AssertionError, match=GUARD):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    with socket.socket() as sock, pytest.raises(AssertionError, match=GUARD):
        sock.connect(("192.0.2.1", 443))
    with socket.socket() as sock, pytest.raises(AssertionError, match=GUARD):
        sock.connect_ex(("192.0.2.1", 443))


async def test_guard_refuses_an_async_httpx_connect() -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(Exception) as excinfo:
            await client.get("https://192.0.2.1/")
    # anyio's connect runs in a task group, so the refusal can arrive in a group.
    assert GUARD in repr(excinfo.value)


def test_guard_refuses_a_hostname_and_allows_loopback() -> None:
    with pytest.raises(AssertionError, match=GUARD):
        socket.create_connection(("example.com", 443), timeout=0.1)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as sock:
            sock.connect(server.getsockname())
