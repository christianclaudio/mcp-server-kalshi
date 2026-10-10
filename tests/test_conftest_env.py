"""The shared conftest keeps shell Kalshi settings out of the offline suite."""

import os

from conftest import KALSHI_SETTINGS_ENV, _live_run_requested

from mcp_server_kalshi.auth import ALLOW_UNAUTHENTICATED_BIND_ENV, AUTH_TOKEN_ENV
from mcp_server_kalshi.config import Settings


def test_kalshi_settings_env_is_cleared():
    leaked = [name for name in KALSHI_SETTINGS_ENV if name in os.environ]
    assert leaked == []
    assert AUTH_TOKEN_ENV not in os.environ
    assert ALLOW_UNAUTHENTICATED_BIND_ENV not in os.environ
    settings = Settings(_env_file=None)
    assert settings.KALSHI_API_KEY is None
    assert settings.KALSHI_PRIVATE_KEY_PATH is None
    assert settings.KALSHI_ENV == "demo"


def test_clear_list_covers_every_settings_field():
    names = set(KALSHI_SETTINGS_ENV)
    assert set(Settings.model_fields) <= names
    assert "KALSHI_API_KEY_ID" in names


def test_live_run_detection():
    assert _live_run_requested(["pytest", "-m", "e2e"])
    assert _live_run_requested(["pytest", "-me2e"])
    assert not _live_run_requested(["pytest", "-m", "not e2e"])
    assert not _live_run_requested(["pytest", "tests/test_config.py"])
    assert not _live_run_requested(["pytest", "-m"])


def test_live_run_detection_requires_exact_e2e():
    assert _live_run_requested(["pytest", "-m=e2e"])
    assert _live_run_requested(["pytest", "-m", " e2e "])
    assert not _live_run_requested(["pytest", "-m", "not (e2e)"])
    assert not _live_run_requested(["pytest", "-m", "not  e2e"])
    assert not _live_run_requested(["pytest", "-m=not (e2e)"])
    assert not _live_run_requested(["pytest", "-m", "e2e or slow"])


def test_dotenv_in_cwd_does_not_reach_settings(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "KALSHI_API_KEY=from-dotenv\nKALSHI_ENV=prod\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.KALSHI_API_KEY is None
    assert settings.KALSHI_ENV == "demo"
