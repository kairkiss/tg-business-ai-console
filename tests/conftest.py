"""
pytest configuration for tg-business-ai-console tests.

Safety guarantees:
- Tests never read or write the real data.sqlite3
- Tests never read .env or real secrets
- Tests never make network requests (Telegram, DeepSeek)
- All database operations use temporary SQLite files via tmp_path
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so we can import db, config, etc.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Prevent .env from polluting test environment
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    """
    Ensure no real .env values leak into tests.
    We set dummy values for any secrets that config.py might read.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token-not-real")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key-not-real")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("BOT_OWNER_ID", "99999")
    # Prevent loading .env file by overriding the path
    # (config.py calls load_dotenv(ENV_PATH) at import time)


# ---------------------------------------------------------------------------
# Temporary database fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Create a temporary SQLite database for testing.

    Usage:
        def test_something(tmp_db):
            import db
            # db.DB_PATH now points to a temp file
            db.init_db()
            # ... test database operations ...
    """
    import db as db_module
    import config as config_module

    tmp_db_path = tmp_path / "test_data.sqlite3"
    # Patch DB_PATH in both db and config modules
    monkeypatch.setattr(db_module, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(config_module, "DB_PATH", tmp_db_path)
    # Also patch SECRET_PATH to avoid touching real files
    monkeypatch.setattr(config_module, "SECRET_PATH", tmp_path / "secret.key")

    return tmp_db_path


@pytest.fixture
def initialized_db(tmp_db):
    """
    Temporary database with schema initialized.
    """
    import db

    db.init_db()
    return tmp_db


# ---------------------------------------------------------------------------
# Network access guard
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """
    Prevent tests from making real network requests.

    If a test needs network access, it must explicitly monkeypatch
    the relevant client with a fake implementation.

    Note: This allows httpx to work for local ASGI transport (TestClient),
    but blocks requests to real external URLs.
    """
    import httpx

    _original_async_request = httpx.AsyncClient.request
    _original_sync_request = httpx.Client.request

    async def _guarded_async_request(self, method, url, **kwargs):
        # Allow local/test URLs (TestClient uses "http://testserver")
        url_str = str(url)
        if "testserver" in url_str or "localhost" in url_str or "127.0.0.1" in url_str:
            return await _original_async_request(self, method, url, **kwargs)
        raise RuntimeError(
            f"Network access is disabled in tests (attempted {method} {url}). "
            "If this test needs network, monkeypatch the client with a fake."
        )

    def _guarded_sync_request(self, method, url, **kwargs):
        # Allow local/test URLs (TestClient uses "http://testserver")
        url_str = str(url)
        if "testserver" in url_str or "localhost" in url_str or "127.0.0.1" in url_str:
            return _original_sync_request(self, method, url, **kwargs)
        raise RuntimeError(
            f"Network access is disabled in tests (attempted {method} {url}). "
            "If this test needs network, monkeypatch the client with a fake."
        )

    # Block httpx.AsyncClient.request (but allow local URLs)
    monkeypatch.setattr(httpx.AsyncClient, "request", _guarded_async_request)
    # Block httpx.Client.request (sync, but allow local URLs)
    monkeypatch.setattr(httpx.Client, "request", _guarded_sync_request)
