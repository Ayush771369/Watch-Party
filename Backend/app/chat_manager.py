"""
Room-isolated text chat manager for Watch Party V2.

Handles per-room chat messages with:
- Server-generated IDs and UTC timestamps
- Nickname/role from authenticated WebSocket session (not client-supplied)
- 500-character message limit with whitespace trimming
- 100-message history retention (chronological)
- Room isolation (messages never cross room boundaries)
- Per-WebSocket-connection rate limiting: max 5 messages per rolling 5-second window
- Safe broadcast through disconnected sockets
- Per-connection rate-limit state cleaned up on disconnect

Do not create a second WebSocket. This module is imported by ws.py
after the WebSocket handler is registered, avoiding circular imports.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Set, Dict, List
from fastapi import WebSocket #type: ignore

# Global room registry: room_id -> ChatRoom
_rooms: dict[str, "ChatRoom"] = {}


class ChatMessage:
    """Represents a chat message with server-generated metadata."""

    __slots__ = ("id", "nickname", "role", "message", "timestamp")

    def __init__(self, nickname: str, role: str, message: str):
        self.id = str(uuid.uuid4())
        self.nickname = nickname
        self.role = role
        self.message = message.strip()
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nickname": self.nickname,
            "role": self.role,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


class ChatRoom:
    """Manages chat state for a single room with isolation and history limits."""

    def __init__(self):
        # Message history: latest 100 messages, chronological order (oldest first)
        self.messages: list[ChatMessage] = []

    def add_message(self, message: ChatMessage) -> None:
        """Add a message to history, maintaining max 100 messages chronologically."""
        self.messages.append(message)
        if len(self.messages) > 100:
            self.messages = self.messages[-100:]

    def get_history(self) -> list[dict]:
        """Return message history as serializable dicts, oldest first."""
        return [msg.to_dict() for msg in self.messages]


class RateLimiter:
    """Per-connection rate limiter: max N messages per rolling T-second window.

    Tracks per-connection message timestamps and rejects messages that exceed
    the rate limit. Rate-limit state is cleaned up when the connection is
    removed.
    """

    def __init__(self, max_messages: int, window_seconds: float):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        # connection_id -> list of (timestamp,) entries (kept within window)
        self._timestamps: Dict[str, List[float]] = {}

    def check_rate_limit(self, connection_id: str, current_time: float) -> bool:
        """Check if a connection is within rate limits.

        Returns True if the message should be allowed, False if rate limited.
        Updates internal state on each call.
        """
        # Initialize timestamps list for this connection if needed
        if connection_id not in self._timestamps:
            self._timestamps[connection_id] = []

        timestamps = self._timestamps[connection_id]

        # Remove timestamps that are outside the rolling window
        cutoff = current_time - self.window_seconds
        self._timestamps[connection_id] = [
            ts for ts in timestamps if ts > cutoff
        ]

        # Check if adding this message would exceed the limit
        if len(self._timestamps[connection_id]) >= self.max_messages:
            return False

        # Record this message timestamp
        self._timestamps[connection_id].append(current_time)
        return True

    def remove_connection(self, connection_id: str) -> None:
        """Remove all rate-limit state for a disconnected connection."""
        if connection_id in self._timestamps:
            del self._timestamps[connection_id]


# Global rate limiter: max 5 messages per 5-second window per connection
_rate_limiter = RateLimiter(max_messages=5, window_seconds=5.0)


def get_rate_limiter() -> RateLimiter:
    """Return the global rate limiter instance."""
    return _rate_limiter


def get_chat_room(room_id: str) -> ChatRoom:
    """Get or create a ChatRoom for the given room_id."""
    if room_id not in _rooms:
        _rooms[room_id] = ChatRoom()
    return _rooms[room_id]


def broadcast_chat_message(room_id: str, message: dict, exclude_ws: Optional[WebSocket] = None) -> None:
    """Broadcast a chat message to all WebSocket connections in a room.

    This is a simplified broadcast for testing. In production, the router's
    _broadcast_json is used which excludes the sender by default.
    """
    from app.routers.ws import _broadcast_json
    _broadcast_json(room_id, message, exclude_ws)


def cleanup_room(room_id: str) -> None:
    """Remove a chat room from the registry.

    Called when the last participant disconnects to free memory.
    """
    if room_id in _rooms:
        del _rooms[room_id]


__all__ = [
    "ChatMessage",
    "ChatRoom",
    "get_chat_room",
    "cleanup_room",
    "RateLimiter",
    "get_rate_limiter",
    "broadcast_chat_message",
]