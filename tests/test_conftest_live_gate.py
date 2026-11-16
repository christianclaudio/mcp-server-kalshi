"""Authenticated live items skip unless the key id and a private key are set.

Public live items stay ungated: the bundled sweep runs under ``-m live``
without credentials and checks the auth safety gate itself.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    LIVE_REQUIRED_ENV,
    live_skip_reason,
    pytest_collection_modifyitems,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

_OPEN = {
    "KALSHI_API_KEY_ID": "key-id",
    "KALSHI_PRIVATE_KEY_PATH": "/tmp/key.pem",
}


class _Item:
    """Minimal item: only the marker lookup and add_marker the hook uses."""

    def __init__(self, live: bool, *, authenticated: bool = False) -> None:
        self._live = live
        self._authenticated = authenticated
        self.markers: list[Any] = []

    def get_closest_marker(self, name: str) -> Any:
        if self._live and name == "live":
            return _Marker(authenticated=self._authenticated)
        return None

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


class _Marker:
    def __init__(self, *, authenticated: bool) -> None:
        self.kwargs = {"authenticated": True} if authenticated else {}


def _reason(marker: Any) -> str:
    reason = marker.kwargs["reason"]
    assert isinstance(reason, str)
    return reason


def test_required_env_is_the_key_id_and_private_key_names() -> None:
    assert LIVE_REQUIRED_ENV == (
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_PATH",
        "KALSHI_PRIVATE_KEY",
    )


def test_missing_key_id_lists_the_name_only() -> None:
    """A missing key id is listed, and the private key value is not."""
    env = {"KALSHI_PRIVATE_KEY": "super-secret-pem"}
    reason = live_skip_reason(env)
    assert reason is not None
    assert "KALSHI_API_KEY_ID" in reason
    assert "super-secret-pem" not in reason


def test_alias_key_id_satisfies_the_gate() -> None:
    """``KALSHI_API_KEY`` is the alias of the key id."""
    env = {"KALSHI_API_KEY": "key-id", "KALSHI_PRIVATE_KEY_PATH": "/tmp/key.pem"}
    assert live_skip_reason(env) is None


def test_either_private_key_source_is_enough() -> None:
    assert live_skip_reason(_OPEN) is None
    pem = {"KALSHI_API_KEY_ID": "key-id", "KALSHI_PRIVATE_KEY": "pem-text"}
    assert live_skip_reason(pem) is None


def test_missing_private_key_lists_both_names() -> None:
    env = {"KALSHI_API_KEY_ID": "key-id"}
    reason = live_skip_reason(env)
    assert reason is not None
    assert "KALSHI_PRIVATE_KEY_PATH" in reason
    assert "KALSHI_PRIVATE_KEY" in reason
    assert "key-id" not in reason


def test_empty_credential_counts_as_missing_and_is_not_echoed() -> None:
    env = {
        "KALSHI_API_KEY_ID": "   ",
        "KALSHI_PRIVATE_KEY_PATH": "   ",
        "KALSHI_PRIVATE_KEY": "super-secret",
    }
    # Whitespace path is empty, but the PEM is set, so the key is present.
    # The key id is whitespace, so only that name is missing, and neither value echoes.
    reason = live_skip_reason(env)
    assert reason is not None
    assert "KALSHI_API_KEY_ID" in reason
    assert "KALSHI_PRIVATE_KEY_PATH" not in reason
    assert "super-secret" not in reason
    assert "   " not in reason


def test_blank_private_key_sources_are_missing() -> None:
    env = {"KALSHI_API_KEY_ID": "key-id", "KALSHI_PRIVATE_KEY": "  "}
    reason = live_skip_reason(env)
    assert reason is not None
    assert "KALSHI_PRIVATE_KEY" in reason
    assert "key-id" not in reason


def test_default_addopts_deselect_live() -> None:
    """The default run deselects live, and the e2e marker is gone."""
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"][
        "ini_options"
    ]
    assert "-m 'not live'" in ini["addopts"]
    assert "not e2e" not in ini["addopts"]
    markers = list(ini["markers"])
    assert any(str(marker).startswith("live:") for marker in markers)
    assert not any(str(marker).startswith("e2e:") for marker in markers)


def test_hook_skips_authenticated_live_items_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collection hook skips authenticated live items and leaves the rest."""
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "super-secret")
    authenticated = _Item(True, authenticated=True)
    public = _Item(True, authenticated=False)
    other = _Item(False)
    pytest_collection_modifyitems(None, [authenticated, public, other])  # type: ignore[arg-type]
    assert public.markers == []
    assert other.markers == []
    assert len(authenticated.markers) == 1
    reason = _reason(authenticated.markers[0])
    assert "KALSHI_API_KEY_ID" in reason
    assert "super-secret" not in reason


def test_hook_leaves_items_when_the_gate_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pure gate returns None, the hook adds no skip."""
    monkeypatch.setattr("tests.conftest.live_skip_reason", lambda environ=None: None)
    live = _Item(True, authenticated=True)
    pytest_collection_modifyitems(None, [live])  # type: ignore[arg-type]
    assert live.markers == []
