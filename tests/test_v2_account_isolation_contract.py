"""
v2 Account Isolation Contract Tests.

These tests define the behavioral contract for the v2 multi-account isolation feature.
They are expected to FAIL (xfail) until the v2 implementation is complete.

Each test corresponds to a requirement from the v2 design document:
- docs/v2-account-isolation-design.md

When implementing v2, change xfail markers to regular tests as each feature is completed.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Conversation Isolation Tests
# ---------------------------------------------------------------------------


class TestConversationIsolation:
    """Tests for conversation-level isolation between accounts."""

    def test_same_peer_chat_id_under_two_accounts_creates_two_conversations(
        self, initialized_db
    ):
        """
        Requirement: UNIQUE(business_account_id, peer_chat_id)

        The same peer_chat_id (e.g., user A) chatting with two different
        Business accounts must produce two separate conversation records.
        """
        import db

        # Create two accounts
        account_x_id = db.create_account("1001", "账号 X")
        account_y_id = db.create_account("1002", "账号 Y")

        peer_chat_id = 5001

        # Create conversations with same peer under different accounts
        conv_x = db.create_conversation(account_x_id, peer_chat_id, "private", "用户 A")
        conv_y = db.create_conversation(account_y_id, peer_chat_id, "private", "用户 A")

        # Must be different conversation IDs
        assert conv_x != conv_y

        # Verify isolation
        fetched_x = db.get_conversation(account_x_id, peer_chat_id)
        fetched_y = db.get_conversation(account_y_id, peer_chat_id)
        assert fetched_x["id"] != fetched_y["id"]
        assert fetched_x["business_account_id"] == account_x_id
        assert fetched_y["business_account_id"] == account_y_id

    def test_account_a_context_does_not_leak_to_account_b(self, initialized_db):
        """
        Requirement: Message must be scoped to conversation_id.

        Messages recorded under account A's conversation must NOT appear
        in account B's conversation context, even if the peer_chat_id is the same.
        """
        import db

        account_x_id = db.create_account("1001", "账号 X")
        account_y_id = db.create_account("1002", "账号 Y")
        peer_chat_id = 5001

        conv_x = db.create_conversation(account_x_id, peer_chat_id, "private", "用户 A")
        conv_y = db.create_conversation(account_y_id, peer_chat_id, "private", "用户 A")

        # Add messages to account X's conversation
        db.add_message_v2(conv_x, account_x_id, 1, "in", "customer", "你好 X", "text")
        # Add messages to account Y's conversation
        db.add_message_v2(conv_y, account_y_id, 2, "in", "customer", "你好 Y", "text")

        # Context for X should only contain X's messages
        ctx_x = db.recent_context_v2(conv_x, 10)
        assert len(ctx_x) == 1
        assert ctx_x[0]["content"] == "你好 X"

        # Context for Y should only contain Y's messages
        ctx_y = db.recent_context_v2(conv_y, 10)
        assert len(ctx_y) == 1
        assert ctx_y[0]["content"] == "你好 Y"


# ---------------------------------------------------------------------------
# Actor Classification Tests
# ---------------------------------------------------------------------------


class TestActorClassification:
    """Tests for message actor classification."""

    def test_business_self_message_does_not_trigger_ai(self, initialized_db):
        """
        Requirement: actor_type == business_self must NOT trigger AI.

        Messages sent by the Business account owner (via Telegram client)
        must be recorded but never trigger auto-reply.
        """
        from bot import BotRunner

        runner = BotRunner()

        # Simulate a message from the business account owner
        msg = {
            "from": {"id": 1001, "first_name": "Kai"},
            "sender_business_bot": True,
            "text": "我手动发的消息",
            "chat": {"id": 5001, "type": "private"},
            "message_id": 42,
            "business_connection_id": "bc_test",
        }

        actor_type = runner.classify_actor(msg)
        assert actor_type == "business_self"

    def test_assistant_bot_message_does_not_trigger_ai(self, initialized_db):
        """
        Requirement: actor_type == assistant_bot must NOT trigger AI.

        Messages sent by the bot itself must be recorded but never trigger auto-reply.
        """
        from bot import BotRunner

        runner = BotRunner()
        runner.bot_id = 12345  # Simulate bot ID

        msg = {
            "from": {"id": 12345},
            "text": "Bot 自动回复的消息",
            "chat": {"id": 5001, "type": "private"},
            "message_id": 43,
            "business_connection_id": "bc_test",
        }

        actor_type = runner.classify_actor(msg)
        assert actor_type == "assistant_bot"

    def test_owner_operator_message_does_not_trigger_ai(self, initialized_db):
        """
        Requirement: actor_type == owner_operator must NOT trigger AI.

        Messages sent by the owner (Kai) directly must be recorded
        but never trigger auto-reply.
        """
        from config import Config
        from bot import BotRunner

        runner = BotRunner()
        runner.bot_id = 12345

        # Owner ID is set in conftest.py as 99999
        msg = {
            "from": {"id": 99999},
            "text": "Owner 发的消息",
            "chat": {"id": 5001, "type": "private"},
            "message_id": 44,
            "business_connection_id": "bc_test",
        }

        actor_type = runner.classify_actor(msg)
        assert actor_type == "owner_operator"

    def test_customer_message_classified_correctly(self, initialized_db):
        """
        Requirement: Regular customer messages must be classified as 'customer'.
        """
        from bot import BotRunner

        runner = BotRunner()
        runner.bot_id = 12345

        msg = {
            "from": {"id": 77777},  # Not bot, not owner
            "text": "客户发的消息",
            "chat": {"id": 5001, "type": "private"},
            "message_id": 45,
            "business_connection_id": "bc_test",
        }

        actor_type = runner.classify_actor(msg)
        assert actor_type == "customer"


# ---------------------------------------------------------------------------
# Reply Decision Tests
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="v2 account isolation not implemented yet: v2 decide_reply not implemented")
class TestReplyDecision:
    """Tests for reply decision logic with account isolation."""

    def test_customer_auto_mode_can_trigger_ai(self, initialized_db):
        """
        Requirement: customer + auto mode → should reply.

        Only customer messages in auto mode conversations should trigger AI.
        """
        import db
        from bot import BotRunner

        runner = BotRunner()

        account_id = db.create_account("1001", "账号 X")
        db.update_account(account_id, {"enabled": 1, "default_reply_mode": "auto"})

        conv_id = db.create_conversation(account_id, 5001, "private", "用户 A")
        db.update_conversation(conv_id, {"mode": "auto"})

        conv = db.get_conversation_by_id(conv_id)
        account = db.get_account(account_id)
        settings = db.get_settings()

        should_reply, reason = runner.decide_reply(conv, account, settings)
        assert should_reply is True
        assert "auto" in reason.lower()

    def test_manual_off_default_auto_decision_contract(self, initialized_db):
        """
        Requirement: Mode decision table.

        - auto → reply
        - manual → don't reply
        - off → don't reply
        - default → follow account default
        """
        import db
        from bot import BotRunner

        runner = BotRunner()

        account_id = db.create_account("1001", "账号 X")
        db.update_account(account_id, {"enabled": 1, "default_reply_mode": "manual"})
        account = db.get_account(account_id)
        settings = db.get_settings()

        # auto → reply
        conv_id = db.create_conversation(account_id, 5001, "private", "A")
        db.update_conversation(conv_id, {"mode": "auto"})
        conv = db.get_conversation_by_id(conv_id)
        should_reply, _ = runner.decide_reply(conv, account, settings)
        assert should_reply is True

        # manual → don't reply
        db.update_conversation(conv_id, {"mode": "manual"})
        conv = db.get_conversation_by_id(conv_id)
        should_reply, _ = runner.decide_reply(conv, account, settings)
        assert should_reply is False

        # off → don't reply
        db.update_conversation(conv_id, {"mode": "off"})
        conv = db.get_conversation_by_id(conv_id)
        should_reply, _ = runner.decide_reply(conv, account, settings)
        assert should_reply is False

        # default → follow account default (manual)
        db.update_conversation(conv_id, {"mode": "default"})
        conv = db.get_conversation_by_id(conv_id)
        should_reply, reason = runner.decide_reply(conv, account, settings)
        assert should_reply is False
        assert "manual" in reason.lower()

    def test_account_default_mode_does_not_affect_other_accounts(self, initialized_db):
        """
        Requirement: Account A's default mode must not affect account B.

        If account X defaults to auto and account Y defaults to manual,
        a conversation under X should reply, while under Y should not.
        """
        import db
        from bot import BotRunner

        runner = BotRunner()

        account_x = db.create_account("1001", "账号 X")
        db.update_account(account_x, {"enabled": 1, "default_reply_mode": "auto"})

        account_y = db.create_account("1002", "账号 Y")
        db.update_account(account_y, {"enabled": 1, "default_reply_mode": "manual"})

        settings = db.get_settings()

        # Conversation under account X (auto)
        conv_x = db.create_conversation(account_x, 5001, "private", "A")
        db.update_conversation(conv_x, {"mode": "default"})
        conv_x_data = db.get_conversation_by_id(conv_x)
        account_x_data = db.get_account(account_x)
        should_reply_x, _ = runner.decide_reply(conv_x_data, account_x_data, settings)
        assert should_reply_x is True

        # Conversation under account Y (manual)
        conv_y = db.create_conversation(account_y, 5001, "private", "A")
        db.update_conversation(conv_y, {"mode": "default"})
        conv_y_data = db.get_conversation_by_id(conv_y)
        account_y_data = db.get_account(account_y)
        should_reply_y, _ = runner.decide_reply(conv_y_data, account_y_data, settings)
        assert should_reply_y is False


# ---------------------------------------------------------------------------
# Context Management Tests
# ---------------------------------------------------------------------------


class TestContextManagement:
    """Tests for conversation context management."""

    def test_forget_context_only_affects_current_conversation(self, initialized_db):
        """
        Requirement: Clearing context must only affect the target conversation.

        Clearing account X's conversation must not affect account Y's
        conversation with the same peer.
        """
        import db

        account_x = db.create_account("1001", "账号 X")
        account_y = db.create_account("1002", "账号 Y")
        peer_chat_id = 5001

        conv_x = db.create_conversation(account_x, peer_chat_id, "private", "A")
        conv_y = db.create_conversation(account_y, peer_chat_id, "private", "A")

        # Add messages to both
        db.add_message_v2(conv_x, account_x, 1, "in", "customer", "消息 X", "text")
        db.add_message_v2(conv_y, account_y, 2, "in", "customer", "消息 Y", "text")

        # Clear only account X's context
        db.forget_conversation(conv_x)

        # Account X should be empty
        ctx_x = db.recent_context_v2(conv_x, 10)
        assert len(ctx_x) == 0

        # Account Y should be unaffected
        ctx_y = db.recent_context_v2(conv_y, 10)
        assert len(ctx_y) == 1
        assert ctx_y[0]["content"] == "消息 Y"


# ---------------------------------------------------------------------------
# Web Isolation Tests
# ---------------------------------------------------------------------------


class TestWebIsolation:
    """Tests for Web console account isolation."""

    def test_web_conversation_lookup_must_not_cross_accounts(self, initialized_db):
        """
        Requirement: Web queries must not return cross-account data.

        Listing conversations for account X must not include
        conversations from account Y.
        """
        import db

        account_x = db.create_account("1001", "账号 X")
        account_y = db.create_account("1002", "账号 Y")

        # Create conversations under each account
        conv_x = db.create_conversation(account_x, 5001, "private", "A")
        conv_y = db.create_conversation(account_y, 5001, "private", "A")

        # List conversations for account X
        convs_x = db.list_conversations(account_x)
        assert len(convs_x) == 1
        assert convs_x[0]["id"] == conv_x

        # List conversations for account Y
        convs_y = db.list_conversations(account_y)
        assert len(convs_y) == 1
        assert convs_y[0]["id"] == conv_y

        # Verify no cross-contamination
        conv_ids_x = {c["id"] for c in convs_x}
        conv_ids_y = {c["id"] for c in convs_y}
        assert conv_ids_x.isdisjoint(conv_ids_y)


# ---------------------------------------------------------------------------
# Account Resolution Tests
# ---------------------------------------------------------------------------


class TestAccountResolution:
    """Tests for account resolution helpers."""

    def test_get_account_by_business_connection_id_returns_account(self, initialized_db):
        """
        Requirement: get_account_by_business_connection_id() must find
        account by its latest_business_connection_id.
        """
        import db

        # Create an account via sync_business_account_from_connection
        # which properly sets latest_business_connection_id
        data = {
            "id": "bc_test_123",
            "user": {"id": 1001, "first_name": "Test"},
            "user_chat_id": 1001,
            "is_enabled": True,
        }
        account_id = db.sync_business_account_from_connection(data)
        assert account_id is not None

        # Test the new helper
        result = db.get_account_by_business_connection_id("bc_test_123")
        assert result is not None
        assert result["id"] == account_id

        # Test with None
        result2 = db.get_account_by_business_connection_id(None)
        assert result2 is None

    def test_get_latest_connection_for_account_returns_connection(self, initialized_db):
        """
        Requirement: get_latest_connection_for_account() must return
        the latest business_connection for an account.
        """
        import db

        # Create account via sync_business_account_from_connection
        data = {
            "id": "bc_conn_456",
            "user": {"id": 1002, "first_name": "Test2"},
            "user_chat_id": 1002,
            "is_enabled": True,
        }
        account_id = db.sync_business_account_from_connection(data)
        assert account_id is not None

        # Also create the business_connection record (sync_business_account_from_connection
        # only creates the account, not the connection record)
        db.upsert_business_connection(data)

        # Now test get_latest_connection_for_account
        result = db.get_latest_connection_for_account(account_id)
        assert result is not None
        assert result["business_connection_id"] == "bc_conn_456"
        assert result["user_id"] == 1002

        # Test with non-existent account
        result2 = db.get_latest_connection_for_account(99999)
        assert result2 is None

    def test_resolve_account_for_business_connection(self, initialized_db):
        """
        Requirement: resolve_account_for_business_connection() must create or
        update account from a Telegram business_connection update object.
        """
        import db

        # Simulate a Telegram business_connection update
        data = {
            "id": "bc_new_789",
            "user": {
                "id": 55555,
                "first_name": "Test",
                "last_name": "User",
                "username": "testuser"
            },
            "user_chat_id": 55555,
            "is_enabled": True,
            "can_reply": True,
        }

        # This should create or update the account
        result = db.resolve_account_for_business_connection(data)
        assert result is not None
        assert result["business_user_id"] == "55555"
        assert result["latest_business_connection_id"] == "bc_new_789"

        # Verify the account can be found by connection_id
        found = db.get_account_by_business_connection_id("bc_new_789")
        assert found is not None
        assert found["id"] == result["id"]
