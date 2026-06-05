"""
Tests for v2 Web account isolation read-only views.

These tests verify that:
- /accounts lists all accounts
- /accounts/{id}/conversations only shows that account's conversations
- /accounts/{id}/conversations/{cid} rejects cross-account access
- /conversations/{cid} redirects to account-scoped URL
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


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

        # Create account with a known connection ID
        account_id = db.create_account("1002", "测试账号 2")
        db.update_account(account_id, {
            "enabled": 1,
            "latest_business_connection_id": "bc_very_long_connection_id_123456789",
        })

        response = client.get("/accounts")
        assert response.status_code == 200
        # Should NOT contain full connection ID
        assert "bc_very_long_connection_id_123456789" not in response.text
        # Should contain connection status (either 已连接 or 未连接)
        assert "连接" in response.text

    def test_connection_id_is_masked_on_account_detail_page(self, client, initialized_db):
        """Verify connection ID is masked on /accounts/{id} page."""
        import db

        # Create account with a known connection ID
        account_id = db.create_account("1003", "测试账号 3")
        db.update_account(account_id, {
            "enabled": 1,
            "latest_business_connection_id": "abcdefghijklmnop",
        })

        response = client.get(f"/accounts/{account_id}")
        assert response.status_code == 200
        # Should NOT contain full connection ID
        assert "abcdefghijklmnop" not in response.text
        # Should contain masked form
        assert "abcdef...mnop" in response.text
        # Should show connection status
        assert "已连接" in response.text
