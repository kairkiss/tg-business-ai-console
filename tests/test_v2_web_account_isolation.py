"""
Tests for v2 Web account isolation read-only views.

These tests verify that:
- /accounts lists all accounts
- /accounts/{id}/conversations only shows that account's conversations
- /accounts/{id}/conversations/{cid} rejects cross-account access
- /conversations/{cid} redirects to account-scoped URL
- connection id is masked in web views
- update_account does not allow latest_business_connection_id
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_db):
    """Create a FastAPI TestClient with temporary database."""
    import db

    # Initialize database
    db.init_db()

    from fastapi.testclient import TestClient
    from app import app

    # Create a test client
    with TestClient(app, raise_server_exceptions=False) as c:
        # Login to get session
        c.post("/login", data={"username": "testadmin", "password": "test-password"})
        yield c


@pytest.fixture
def sample_accounts_and_conversations(tmp_db, monkeypatch):
    """Create sample accounts and conversations for testing."""
    import db

    # Create two accounts
    account_x_id = db.create_account("1001", "账号 X")
    account_y_id = db.create_account("1002", "账号 Y")

    # Enable both accounts
    db.update_account(account_x_id, {"enabled": 1, "default_reply_mode": "auto"})
    db.update_account(account_y_id, {"enabled": 1, "default_reply_mode": "manual"})

    # Same peer under different accounts
    peer_chat_id = 5001
    conv_x = db.create_conversation(account_x_id, peer_chat_id, "private", "用户 A", "userA")
    conv_y = db.create_conversation(account_y_id, peer_chat_id, "private", "用户 A", "userA")

    # Add some messages
    db.add_message_v2(conv_x, account_x_id, 1, "in", "customer", "给 X 的消息", "text")
    db.add_message_v2(conv_x, account_x_id, 2, "out", "assistant_bot", "X 的回复", "text")
    db.add_message_v2(conv_y, account_y_id, 3, "in", "customer", "给 Y 的消息", "text")

    return {
        "account_x_id": account_x_id,
        "account_y_id": account_y_id,
        "conv_x": conv_x,
        "conv_y": conv_y,
        "peer_chat_id": peer_chat_id,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAccountsPage:
    """Tests for /accounts page."""

    def test_accounts_page_lists_accounts(self, client, sample_accounts_and_conversations):
        """Verify /accounts lists all accounts."""
        response = client.get("/accounts")
        assert response.status_code == 200
        # Should contain both account names
        assert "账号 X" in response.text
        assert "账号 Y" in response.text


class TestAccountConversations:
    """Tests for /accounts/{id}/conversations page."""

    def test_account_conversations_only_lists_that_account(
        self, client, sample_accounts_and_conversations
    ):
        """Verify conversations are filtered by account_id."""
        data = sample_accounts_and_conversations

        # Get conversations for account X
        response_x = client.get(f"/accounts/{data['account_x_id']}/conversations")
        assert response_x.status_code == 200
        assert "给 X 的消息" not in response_x.text  # Messages not shown on list page
        assert "userA" in response_x.text  # Peer username should be shown

        # Get conversations for account Y
        response_y = client.get(f"/accounts/{data['account_y_id']}/conversations")
        assert response_y.status_code == 200
        assert "userA" in response_y.text

        # Both should show the same peer (userA) but in separate contexts
        # The key is that they are separate pages, not mixed

    def test_account_conversations_isolation(
        self, client, sample_accounts_and_conversations
    ):
        """Verify same peer appears in both account lists separately."""
        data = sample_accounts_and_conversations

        # Account X should have exactly 1 conversation
        response_x = client.get(f"/accounts/{data['account_x_id']}/conversations")
        assert response_x.status_code == 200
        # Count conversation cards (simple check)
        assert f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}" in response_x.text

        # Account Y should have exactly 1 conversation
        response_y = client.get(f"/accounts/{data['account_y_id']}/conversations")
        assert response_y.status_code == 200
        assert f"/accounts/{data['account_y_id']}/conversations/{data['conv_y']}" in response_y.text


class TestConversationDetail:
    """Tests for /accounts/{id}/conversations/{cid} page."""

    def test_conversation_detail_shows_messages(
        self, client, sample_accounts_and_conversations
    ):
        """Verify conversation detail shows messages_v2."""
        data = sample_accounts_and_conversations

        response = client.get(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}"
        )
        assert response.status_code == 200
        assert "给 X 的消息" in response.text
        assert "X 的回复" in response.text
        assert "customer" in response.text  # actor_type
        assert "assistant_bot" in response.text

    def test_conversation_detail_rejects_cross_account_access(
        self, client, sample_accounts_and_conversations
    ):
        """Verify cross-account access returns 404."""
        data = sample_accounts_and_conversations

        # Try to access account X's conversation via account Y's URL
        response = client.get(
            f"/accounts/{data['account_y_id']}/conversations/{data['conv_x']}"
        )
        assert response.status_code == 404

        # Try to access account Y's conversation via account X's URL
        response = client.get(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_y']}"
        )
        assert response.status_code == 404


class TestConversationShortcut:
    """Tests for /conversations/{cid} shortcut redirect."""

    def test_conversation_shortcut_redirects_to_account_scoped_url(
        self, client, sample_accounts_and_conversations
    ):
        """Verify shortcut redirects to /accounts/{id}/conversations/{cid}."""
        data = sample_accounts_and_conversations

        # Follow redirect=False to check the redirect target
        response = client.get(f"/conversations/{data['conv_x']}", follow_redirects=False)
        assert response.status_code == 302
        assert f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}" in response.headers["location"]

        response = client.get(f"/conversations/{data['conv_y']}", follow_redirects=False)
        assert response.status_code == 302
        assert f"/accounts/{data['account_y_id']}/conversations/{data['conv_y']}" in response.headers["location"]

    def test_conversation_shortcut_404_for_nonexistent(self, client, initialized_db):
        """Verify shortcut returns 404 for nonexistent conversation."""
        response = client.get("/conversations/99999", follow_redirects=False)
        assert response.status_code == 404


class TestAccountCountDisplay:
    """Tests for account count display on /accounts page."""

    def test_accounts_page_shows_v2_and_legacy_counts(self, client, initialized_db):
        """Verify /accounts page shows both v2 conversation count and legacy chat count."""
        import db

        # Create account
        account_id = db.create_account("1001", "测试账号")
        db.update_account(account_id, {"enabled": 1})

        # Create a v2 conversation
        conv_id = db.create_conversation(account_id, 5001, "private", "用户 A")

        # Check the accounts page
        response = client.get("/accounts")
        assert response.status_code == 200
        # Should show v2 conversation count
        assert "v2 对话数：1" in response.text
        # Should show legacy chat count
        assert "Legacy 聊天数：0" in response.text


class TestConnectionIdMasking:
    """Tests for connection ID masking on web pages."""

    def test_connection_id_is_masked_on_accounts_page(self, client, initialized_db):
        """Verify connection ID is masked on /accounts page."""
        import db

        # Create account via proper sync flow
        connection_data = {
            "id": "bc_very_long_connection_id_123456789",
            "user": {"id": 1002, "first_name": "测试"},
            "user_chat_id": 1002,
            "is_enabled": True,
        }
        account_id = db.sync_business_account_from_connection(connection_data)
        db.upsert_business_connection(connection_data)
        db.update_account(account_id, {"enabled": 1})

        response = client.get("/accounts")
        assert response.status_code == 200
        # Should NOT contain full connection ID
        assert "bc_very_long_connection_id_123456789" not in response.text
        # Should contain connection status
        assert "连接" in response.text

    def test_connection_id_is_masked_on_account_detail_page(self, client, initialized_db):
        """Verify connection ID is masked on /accounts/{id} page."""
        import db

        # Create account via proper sync flow
        connection_data = {
            "id": "abcdefghijklmnop",
            "user": {"id": 1003, "first_name": "测试3"},
            "user_chat_id": 1003,
            "is_enabled": True,
        }
        account_id = db.sync_business_account_from_connection(connection_data)
        db.upsert_business_connection(connection_data)
        db.update_account(account_id, {"enabled": 1})

        response = client.get(f"/accounts/{account_id}")
        assert response.status_code == 200
        # Should NOT contain full connection ID
        assert "abcdefghijklmnop" not in response.text
        # Should contain masked form
        assert "abcdef...mnop" in response.text
        # Should show connection status
        assert "已连接" in response.text


class TestAccountUpdateBoundary:
    """Tests for account update field restrictions."""

    def test_update_account_does_not_allow_latest_business_connection_id(self, initialized_db):
        """Verify update_account() rejects latest_business_connection_id field."""
        import db

        # Create account via proper sync flow
        connection_data = {
            "id": "bc_original_connection_id",
            "user": {"id": 1004, "first_name": "测试4"},
            "user_chat_id": 1004,
            "is_enabled": True,
        }
        account_id = db.sync_business_account_from_connection(connection_data)
        db.upsert_business_connection(connection_data)

        # Verify original connection id
        account = db.get_account(account_id)
        assert account["latest_business_connection_id"] == "bc_original_connection_id"

        # Try to override via update_account (should be ignored)
        db.update_account(account_id, {
            "latest_business_connection_id": "bc_fake_override_attempt",
            "enabled": 1,
        })

        # Verify connection id was NOT changed
        account = db.get_account(account_id)
        assert account["latest_business_connection_id"] == "bc_original_connection_id"
        assert account["latest_business_connection_id"] != "bc_fake_override_attempt"
        # But enabled should be updated
        assert account["enabled"] == 1


# ---------------------------------------------------------------------------
# Conversation Settings Save Tests
# ---------------------------------------------------------------------------

class TestConversationSettingsSave:
    """Tests for POST /accounts/{id}/conversations/{cid}/save."""

    def _get_csrf(self, client, account_id, conversation_id):
        """Helper to get CSRF token from the conversation detail page."""
        response = client.get(f"/accounts/{account_id}/conversations/{conversation_id}")
        # Extract CSRF from meta tag
        import re
        match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
        if match:
            return match.group(1)
        # Fallback: get from session
        return client.cookies.get("session", "")

    def test_conversation_settings_save_updates_v2_conversation(
        self, client, sample_accounts_and_conversations
    ):
        """Verify saving conversation settings updates the v2 conversation."""
        import db
        data = sample_accounts_and_conversations
        csrf = self._get_csrf(client, data['account_x_id'], data['conv_x'])

        # Save settings
        response = client.post(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}/save",
            data={
                "csrf": csrf,
                "mode": "auto",
                "prompt_mode": "custom",
                "persona_id": "",
                "custom_prompt": "测试专属提示词",
                "custom_prompt_enabled": "true",
                "takeover_exempt": "false",
            },
            follow_redirects=False,
        )
        # Should redirect back
        assert response.status_code == 303

        # Verify conversation was updated
        conv = db.get_conversation_by_id(data["conv_x"])
        assert conv["mode"] == "auto"
        assert conv["prompt_mode"] == "custom"
        assert conv["custom_prompt"] == "测试专属提示词"
        assert conv["custom_prompt_enabled"] == 1
        assert conv["takeover_exempt"] == 0

    def test_conversation_settings_save_does_not_affect_other_account_same_peer(
        self, client, sample_accounts_and_conversations
    ):
        """Verify saving one account's conversation doesn't affect the other."""
        import db
        data = sample_accounts_and_conversations
        csrf = self._get_csrf(client, data['account_x_id'], data['conv_x'])

        # Save settings for account X's conversation
        client.post(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}/save",
            data={
                "csrf": csrf,
                "mode": "auto",
                "prompt_mode": "account",
                "persona_id": "",
                "custom_prompt": "",
                "custom_prompt_enabled": "false",
                "takeover_exempt": "false",
            },
        )

        # Verify account Y's conversation is unchanged
        conv_y = db.get_conversation_by_id(data["conv_y"])
        assert conv_y["mode"] == "default"  # Should still be default

    def test_conversation_settings_save_rejects_cross_account(
        self, client, sample_accounts_and_conversations
    ):
        """Verify cross-account POST is rejected."""
        import db
        data = sample_accounts_and_conversations
        csrf = self._get_csrf(client, data['account_x_id'], data['conv_x'])

        # Try to save account X's conversation via account Y's URL
        response = client.post(
            f"/accounts/{data['account_y_id']}/conversations/{data['conv_x']}/save",
            data={
                "csrf": csrf,
                "mode": "auto",
                "prompt_mode": "account",
                "persona_id": "",
                "custom_prompt": "",
                "custom_prompt_enabled": "false",
                "takeover_exempt": "false",
            },
        )
        assert response.status_code == 404

        # Verify conversation was NOT modified
        conv_x = db.get_conversation_by_id(data["conv_x"])
        assert conv_x["mode"] == "default"

    def test_conversation_settings_save_rejects_invalid_mode(
        self, client, sample_accounts_and_conversations
    ):
        """Verify invalid mode returns 400."""
        data = sample_accounts_and_conversations
        csrf = self._get_csrf(client, data['account_x_id'], data['conv_x'])

        response = client.post(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}/save",
            data={
                "csrf": csrf,
                "mode": "invalid_mode",
                "prompt_mode": "account",
            },
        )
        assert response.status_code == 400

    def test_conversation_settings_save_rejects_invalid_prompt_mode(
        self, client, sample_accounts_and_conversations
    ):
        """Verify invalid prompt_mode returns 400."""
        data = sample_accounts_and_conversations
        csrf = self._get_csrf(client, data['account_x_id'], data['conv_x'])

        response = client.post(
            f"/accounts/{data['account_x_id']}/conversations/{data['conv_x']}/save",
            data={
                "csrf": csrf,
                "mode": "default",
                "prompt_mode": "invalid_prompt_mode",
            },
        )
        assert response.status_code == 400
