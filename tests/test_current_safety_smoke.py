"""
Smoke tests for current codebase safety.

These tests MUST pass with the existing code.
They verify that our test infrastructure works and that
basic functions behave correctly without touching real data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# output_filter tests
# ---------------------------------------------------------------------------
class TestOutputFilter:
    """Test output_filter.filter_reply() basic behavior."""

    def test_empty_string_returns_fallback(self):
        from output_filter import filter_reply

        result, triggered = filter_reply("", "安全回复")
        assert result == "安全回复"
        assert triggered is True

    def test_none_returns_fallback(self):
        from output_filter import filter_reply

        result, triggered = filter_reply(None, "安全回复")
        assert result == "安全回复"
        assert triggered is True

    def test_normal_text_passes_through(self):
        from output_filter import filter_reply

        result, triggered = filter_reply("你好，有什么可以帮你的吗？", "安全回复")
        assert result == "你好，有什么可以帮你的吗？"
        assert triggered is False

    def test_action_only_text_gets_filtered(self):
        from output_filter import filter_reply

        # 纯动作文本应该被过滤
        result, triggered = filter_reply("（叹气）", "安全回复")
        assert result == "安全回复"
        assert triggered is True

    def test_action_prefix_removed(self):
        from output_filter import filter_reply

        result, triggered = filter_reply("（看着你）你好啊", "安全回复")
        assert "你好啊" in result
        # triggered 取决于是否包含动作词，这里可能为 True 或 False


# ---------------------------------------------------------------------------
# db.conversation_key tests
# ---------------------------------------------------------------------------
class TestConversationKey:
    """Test db.conversation_key() format."""

    def test_with_both_params(self):
        from db import conversation_key

        result = conversation_key("bc_123", 456)
        assert result == "bc_123:456"

    def test_with_none_bc_id(self):
        from db import conversation_key

        result = conversation_key(None, 456)
        assert result == "unknown:456"

    def test_with_string_chat_id(self):
        from db import conversation_key

        result = conversation_key("bc_abc", "789")
        assert result == "bc_abc:789"


# ---------------------------------------------------------------------------
# db.init_db tests (using temporary database)
# ---------------------------------------------------------------------------
class TestInitDb:
    """Test that db.init_db() creates tables correctly in a temp database."""

    def test_init_db_creates_tables(self, tmp_db):
        import db

        db.init_db()

        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected_tables = {
            "settings",
            "business_connections",
            "chats",
            "messages",
            "logs",
            "prompt_personas",
            "business_accounts",
        }
        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

    def test_init_db_sets_user_version(self, tmp_db):
        import db
        import sqlite3

        db.init_db()

        conn = sqlite3.connect(str(tmp_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        assert version == 5  # Current version

    def test_init_db_inserts_default_settings(self, tmp_db):
        import db

        db.init_db()

        settings = db.get_settings()
        assert "global_enabled" in settings
        assert "default_reply_mode" in settings
        assert "deepseek_model" in settings

    def test_init_db_inserts_builtin_personas(self, tmp_db):
        import db

        db.init_db()

        personas = db.list_personas(include_disabled=False)
        assert len(personas) >= 1  # At least the default persona
        names = [p["name"] for p in personas]
        assert "默认稳妥助手" in names


# ---------------------------------------------------------------------------
# db.log tests (using temporary database)
# ---------------------------------------------------------------------------
class TestDbLog:
    """Test that db.log() writes to temp database without touching real one."""

    def test_log_writes_entry(self, initialized_db):
        import db

        db.log("INFO", "test", "测试日志消息")

        logs = db.get_logs(limit=10)
        assert len(logs) >= 1
        assert logs[0]["message"] == "测试日志消息"
        assert logs[0]["level"] == "INFO"
        assert logs[0]["source"] == "test"

    def test_log_redacts_secrets(self, initialized_db):
        import db

        # Temporarily set a fake token to test redaction
        original_token = db.Config.telegram_bot_token
        db.Config.telegram_bot_token = "super-secret-token-12345"

        db.log("INFO", "test", "使用 token super-secret-token-12345 连接")

        logs = db.get_logs(limit=1)
        assert "super-secret-token-12345" not in logs[0]["message"]
        assert "[redacted]" in logs[0]["message"]

        # Restore
        db.Config.telegram_bot_token = original_token


# ---------------------------------------------------------------------------
# db.settings tests (using temporary database)
# ---------------------------------------------------------------------------
class TestDbSettings:
    """Test settings operations on temporary database."""

    def test_get_settings_returns_defaults(self, initialized_db):
        import db

        settings = db.get_settings()
        assert settings["global_enabled"] == "true"
        assert settings["deepseek_model"] == "deepseek-chat"

    def test_set_settings_persists(self, initialized_db):
        import db

        db.set_settings({"global_enabled": "false"})
        settings = db.get_settings()
        assert settings["global_enabled"] == "false"

    def test_set_settings_unknown_key_ignored(self, initialized_db):
        import db

        db.set_settings({"nonexistent_key": "value"})
        settings = db.get_settings()
        assert "nonexistent_key" not in settings


# ---------------------------------------------------------------------------
# db.now_iso tests
# ---------------------------------------------------------------------------
class TestNowIso:
    """Test db.now_iso() returns valid ISO format."""

    def test_returns_iso_string(self):
        from db import now_iso

        result = now_iso()
        assert isinstance(result, str)
        assert "T" in result
        assert result.endswith("+00:00") or "+" in result or "Z" in result
