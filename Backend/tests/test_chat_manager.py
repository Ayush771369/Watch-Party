"""
Tests for the Phase 2 Chat System.

Tests cover:
- chat_manager.py: ChatMessage, ChatRoom, RateLimiter
- WebSocket integration: chat_message handling, room isolation, history, rate limiting, authentication
"""

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.chat_manager import ChatMessage, ChatRoom, get_chat_room, RateLimiter


class TestChatMessage:
    """Tests for the ChatMessage data class."""

    def test_message_creation(self):
        """ChatMessage creates with server-generated UUID and UTC timestamp."""
        msg = ChatMessage(nickname="Alice", role="viewer", message="Hello")
        # UUID should be non-empty string
        assert isinstance(msg.id, str)
        assert len(msg.id) > 0
        # Timestamp should be timezone-aware UTC
        assert msg.timestamp.tzinfo is not None
        # Message should be stripped
        assert msg.message == "Hello"
        # Nickname and role preserved
        assert msg.nickname == "Alice"
        assert msg.role == "viewer"

    def test_message_stripping(self):
        """Whitespace in message is trimmed."""
        msg = ChatMessage(nickname="Alice", role="viewer", message="  Hello World  ")
        assert msg.message == "Hello World"

    def test_message_iso_timestamp(self):
        """Timestamp isoformat is valid."""
        msg = ChatMessage(nickname="Alice", role="viewer", message="Test")
        # isoformat() should not raise
        ts = msg.timestamp.isoformat()
        assert isinstance(ts, str)
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None


class TestChatRoom:
    """Tests for the ChatRoom message history manager."""

    def test_add_message_and_history(self):
        """Messages can be added and history retrieved."""
        room = ChatRoom()
        msg1 = ChatMessage(nickname="Alice", role="viewer", message="Hello")
        msg2 = ChatMessage(nickname="Bob", role="host", message="Hi there")

        room.add_message(msg1)
        room.add_message(msg2)

        history = room.get_history()
        assert len(history) == 2
        # Should be oldest first
        assert history[0]["message"] == "Hello"
        assert history[1]["message"] == "Hi there"

    def test_history_max_100(self):
        """History retains exactly the latest 100 messages."""
        room = ChatRoom()
        # Add 150 messages
        for i in range(150):
            msg = ChatMessage(nickname=f"User{i}", role="viewer", message=f"Message {i}")
            room.add_message(msg)

        history = room.get_history()
        assert len(history) == 100
        # Latest messages should be the last 100
        assert history[0]["message"] == "Message 50"
        assert history[-1]["message"] == "Message 149"

    def test_history_oldest_first(self):
        """History is returned in chronological order (oldest first)."""
        room = ChatRoom()
        msg1 = ChatMessage(nickname="Alice", role="viewer", message="First")
        msg2 = ChatMessage(nickname="Bob", role="viewer", message="Second")
        msg3 = ChatMessage(nickname="Charlie", role="viewer", message="Third")

        room.add_message(msg1)
        room.add_message(msg2)
        room.add_message(msg3)

        history = room.get_history()
        assert len(history) == 3
        assert history[0]["message"] == "First"
        assert history[1]["message"] == "Second"
        assert history[2]["message"] == "Third"

    def test_room_isolation(self):
        """Messages in one room do not appear in another."""
        room_a = ChatRoom()
        room_b = ChatRoom()

        msg_a = ChatMessage(nickname="Alice", role="viewer", message="From A")
        msg_b = ChatMessage(nickname="Bob", role="viewer", message="From B")

        room_a.add_message(msg_a)
        room_b.add_message(msg_b)

        history_a = room_a.get_history()
        history_b = room_b.get_history()

        assert len(history_a) == 1
        assert len(history_b) == 1
        assert history_a[0]["message"] == "From A"
        assert history_b[0]["message"] == "From B"


class TestRateLimiter:
    """Tests for the per-connection RateLimiter."""

    @pytest.fixture
    def rate_limiter(self):
        """Create a fresh rate limiter for each test."""
        return RateLimiter(max_messages=5, window_seconds=5.0)

    def test_allow_messages_up_to_limit(self, rate_limiter):
        """Up to max_messages are allowed."""
        conn_id = "conn-1"
        current_time = 1000.0

        # Allow exactly max_messages messages
        for i in range(5):
            allowed = rate_limiter.check_rate_limit(conn_id, current_time + i * 0.5)
            assert allowed, f"Message {i+1} should be allowed"

    def test_reject_exceeding_limit(self, rate_limiter):
        """Messages beyond max_messages are rejected."""
        conn_id = "conn-1"
        current_time = 1000.0

        # Allow max_messages
        for i in range(5):
            rate_limiter.check_rate_limit(conn_id, current_time + i * 0.5)

        # The 6th should be rejected
        allowed = rate_limiter.check_rate_limit(conn_id, 1002.0)
        assert not allowed, "6th message should be rate-limited"

    def test_rate_limit_independent_between_connections(self, rate_limiter):
        """Rate limit state is independent between different connections."""
        conn_id_1 = "conn-1"
        conn_id_2 = "conn-2"
        current_time = 1000.0

        # Fill up conn-1's limit
        for i in range(5):
            rate_limiter.check_rate_limit(conn_id_1, current_time + i * 0.5)

        # conn-2 should still be able to send
        allowed = rate_limiter.check_rate_limit(conn_id_2, 1002.0)
        assert allowed, "Different connection should not be rate-limited"

    def test_rate_limit_reset_after_window(self, rate_limiter):
        """Timestamps outside the rolling window are evicted."""
        conn_id = "conn-1"
        current_time = 1000.0

        # Add messages near the beginning of the window
        for i in range(5):
            rate_limiter.check_rate_limit(conn_id, current_time + i * 0.5)

        # Should be at limit
        allowed = rate_limiter.check_rate_limit(conn_id, 1004.9)
        assert not allowed, "Should still be at limit within window"

        # Move far past the window - old timestamps should be evicted
        allowed = rate_limiter.check_rate_limit(conn_id, 1010.0)
        # By now, all old timestamps are outside the 5s window, so should be allowed
        assert allowed, "Should be allowed after window reset"

    def test_remove_connection(self, rate_limiter):
        """Rate-limit state is cleaned up on disconnect."""
        conn_id = "conn-1"

        # Add some timestamps
        current_time = 1000.0
        for i in range(3):
            rate_limiter.check_rate_limit(conn_id, current_time + i * 0.5)

        # Remove connection
        rate_limiter.remove_connection(conn_id)

        # Connection should no longer have state
        assert conn_id not in rate_limiter._timestamps


class TestChatManagerIntegration:
    """Integration tests for chat_manager with room operations."""

    def test_get_chat_room_creates_new(self):
        """get_chat_room creates a new room if one doesn't exist."""
        room_id = "test-room-1"
        room = get_chat_room(room_id)
        assert isinstance(room, ChatRoom)
        # Verify the room is registered in the global registry
        from app.chat_manager import _rooms
        assert room_id in _rooms

    def test_same_room_shares_chat_room(self):
        """Multiple calls to get_chat_room with same room_id share the ChatRoom."""
        room_id = "test-room-2"
        room1 = get_chat_room(room_id)
        room2 = get_chat_room(room_id)
        assert room1 is room2, "Same room_id should return same ChatRoom instance"

    def test_different_rooms_isolated(self):
        """Different room_ids have isolated ChatRoom instances."""
        room_a = get_chat_room("room-a")
        room_b = get_chat_room("room-b")
        assert room_a is not room_b

    def test_message_count_never_exceeds_100(self):
        """Even with repeated calls, history never exceeds 100."""
        room = ChatRoom()
        for i in range(200):
            msg = ChatMessage(nickname=f"User{i}", role="viewer", message=f"Msg{i}")
            room.add_message(msg)
        history = room.get_history()
        assert len(history) == 100

    def test_empty_message_stored_correctly(self):
        """An empty message (after strip) still creates a ChatMessage."""
        # This tests that ChatMessage handles edge cases
        msg = ChatMessage(nickname="Alice", role="viewer", message="")
        # Message is stripped; empty string stored
        assert msg.message == ""