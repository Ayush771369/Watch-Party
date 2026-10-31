import json
from datetime import datetime
from typing import Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.database import SessionLocal
from app.models.room import Room, RoomState
from app.schemas import PlaybackUpdate
from app.auth import verify_secret
from app.chat_manager import (
    ChatMessage,
    ChatRoom,
    get_chat_room,
    broadcast_chat_message,
    get_rate_limiter,
    RateLimiter,
)


router = APIRouter()

# Active WebSocket connections grouped by room_id
_connections: dict[str, set[WebSocket]] = {}

# Track rooms that have been initialized (have at least one connected client)
_initialized_rooms: set[str] = set()

# In-memory room state: nicknames, participants, chat history
_room_data: dict[str, dict] = {}


def _get_room_data(room_id: str) -> dict:
    """Get or initialize in-memory room data."""
    if room_id not in _room_data:
        _room_data[room_id] = {
            "nicknames": {},          # nickname (lowercase) -> websocket
            "participants": [],       # list[{"nickname": str, "role": str, "connected_at": datetime}]
            "chat_history": [],       # list[{"nickname": str, "text": str, "timestamp": datetime}]
        }
    return _room_data[room_id]


def _normalize_nickname(nickname: str) -> str:
    """Normalize nickname: trim whitespace and lowercase for comparison."""
    return nickname.strip().lower()


async def _broadcast_json(room_id: str, message: dict, exclude_ws: Optional[WebSocket] = None):
    """Broadcast a JSON message to all WebSocket connections in a room."""
    payload = json.dumps(message)
    for ws in _connections.get(room_id, set()):
        if ws != exclude_ws:
            try:
                await ws.send_text(payload)
            except Exception:
                pass


@router.websocket("/ws/{room_id}")
async def room_websocket(websocket: WebSocket, room_id: str):
    db: Session = SessionLocal()

    # --- verify room exists before accepting ---
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        await websocket.close(code=4004, reason="Room not found")
        db.close()
        return

    await websocket.accept()

    # --- register this connection ---
    if room_id not in _connections:
        _connections[room_id] = set()
    _connections[room_id].add(websocket)

    # --- initialize room data if needed ---
    room_data = _get_room_data(room_id)

    # --- send current state to the newly connected client ---
    state = db.query(RoomState).filter(RoomState.room_id == room_id).first()
    await websocket.send_json({
        "type": "state_sync",
        "playback_position_seconds": state.playback_position_seconds,
        "is_playing": state.is_playing,
    })

    # Send initial participants list and chat history via chat_manager
    chat_room = get_chat_room(room_id)
    await websocket.send_json({
        "type": "chat_history",
        "messages": chat_room.get_history(),
    })

    try:
        # --- WebSocket authentication ---
        role: Optional[str] = None
        is_authenticated = False
        nickname: Optional[str] = None

        while not is_authenticated:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid JSON format"})
                continue

            if not isinstance(data, dict):
                await websocket.send_json({"type": "error", "message": "Expected JSON object"})
                continue

            # Extract host secret if provided
            secret = data.get("secret")
            if secret is None:
                secret = data.get("host_secret")

            if secret is not None and isinstance(secret, str) and secret.strip():
                # Verify host secret against stored hash
                if room.host_secret_hash and verify_secret(secret.strip(), room.host_secret_hash):
                    # Valid host secret → authenticate as host.
                    role = "host"
                    is_authenticated = True
                else:
                    # Invalid host secret → reject authentication.
                    await websocket.close(code=4001, reason="Invalid host secret")
                    db.close()
                    return
            else:
                # No host secret → allow viewer access, with no playback-control privileges.
                role = "viewer"
                is_authenticated = True

        # --- send authenticated role to client ---
        await websocket.send_json({
            "type": "auth_response",
            "role": role,
        })

        # --- Nickname setup ---
        max_retries = 5
        retry_count = 0

        while nickname is None and retry_count < max_retries:
            # Request nickname from client
            await websocket.send_json({
                "type": "request_nickname",
                "message": "Please enter a nickname to join the room.",
            })
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                retry_count += 1
                continue

            if not isinstance(data, dict):
                retry_count += 1
                continue

            nickname = data.get("nickname", "")

            # Normalize nickname: trim whitespace
            nickname = nickname.strip() if nickname else ""

            # Validate nickname length (V2 Phase 1: min 1, max 24)
            if not nickname:
                await websocket.send_json({
                    "type": "error",
                    "message": "Nickname cannot be empty.",
                })
                nickname = None
                retry_count += 1
                continue

            if len(nickname) > 24:
                await websocket.send_json({
                    "type": "error",
                    "message": "Nickname must be at most 24 characters.",
                })
                nickname = None
                retry_count += 1
                continue

            # Check for duplicate nickname (case-insensitive via normalized form)
            normalized = _normalize_nickname(nickname)
            if normalized in room_data["nicknames"]:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Nickname '{nickname}' is already taken. Please choose another.",
                })
                nickname = None
                retry_count += 1
                continue

            # Accept the nickname (store normalized form for lookup)
            room_data["nicknames"][normalized] = websocket
            room_data["participants"].append({
                "nickname": nickname,  # Store original trimmed nickname for display
                "role": role,
                "connected_at": datetime.utcnow(),
            })

            # Broadcast participant joined to all other clients
            await _broadcast_json(room_id, {
                "type": "participant_joined",
                "nickname": nickname,
                "role": role,
            })

            # Broadcast updated participants list to all clients (including sender)
            await websocket.send_json({
                "type": "participants",
                "participants": room_data["participants"],
            })

            # Also send the new participant their own nickname confirmation
            await websocket.send_json({
                "type": "nickname_accepted",
                "nickname": nickname,
            })

        # --- process playback update and chat messages ---
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                break

            if not isinstance(data, dict):
                continue

            # Handle chat messages
            if data.get("type") == "chat_message":
                text = data.get("message", "").strip()

                # Rate limiting: max 5 messages per 5-second window per connection
                conn_id = id(websocket)
                current_time = datetime.utcnow().timestamp()
                rate_limiter = get_rate_limiter()
                if not rate_limiter.check_rate_limit(conn_id, current_time):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Rate limit exceeded. Maximum 5 messages per 5 seconds.",
                    })
                    continue

                # Validate message is not empty after trimming
                if not text:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Message cannot be empty.",
                    })
                    continue

                # Validate message length (max 500 characters)
                if len(text) > 500:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Message must be at most 500 characters.",
                    })
                    continue

                # Use the authenticated nickname and role from the session
                # Nickname is guaranteed to be set at this point (completed nickname setup above)
                effective_nickname = nickname
                effective_role = role

                # Create chat message with server-generated ID and timestamp
                chat_msg = ChatMessage(nickname=effective_nickname, role=effective_role, message=text)

                # Add to chat room history
                room_chat = get_chat_room(room_id)
                room_chat.add_message(chat_msg)

                # Broadcast to all clients in the room (including sender)
                await broadcast_chat_message(room_id, {
                    "type": "chat_message",
                    "id": chat_msg.id,
                    "nickname": chat_msg.nickname,
                    "role": chat_msg.role,
                    "message": chat_msg.message,
                    "timestamp": chat_msg.timestamp.isoformat(),
                }, exclude_ws=None)  # Include sender

                continue

            # Handle playback_update messages
            if data.get("type") == "playback_update":
                action = data.get("action")
                position = data.get("playback_position_seconds")

                # Only host can control playback - verify role
                if role != "host":
                    # Send error to client, but don't break the connection
                    await websocket.send_json({
                        "type": "error",
                        "message": "Only the host can control playback.",
                    })
                    continue

                if action and position is not None and state is not None:
                    if action == "play":
                        state.is_playing = True
                        state.playback_position_seconds = float(position)
                    elif action == "pause":
                        state.is_playing = False
                        state.playback_position_seconds = float(position)
                    elif action == "seek":
                        state.playback_position_seconds = float(position)
                    state.last_updated_at = datetime.utcnow()
                    db.commit()

                    # Broadcast updated state to all other clients
                    broadcast = {
                        "type": "state_sync",
                        "playback_position_seconds": state.playback_position_seconds,
                        "is_playing": state.is_playing,
                    }
                    await _broadcast_json(room_id, broadcast, exclude_ws=websocket)

    # ... rest continues
    finally:
        # --- cleanup on disconnect ---
        _connections.get(room_id, set()).discard(websocket)

        # Remove nickname and participant on disconnect
        if nickname:
            normalized = _normalize_nickname(nickname)
            if normalized in room_data.get("nicknames", {}):
                del room_data["nicknames"][normalized]

        room_data["participants"] = [
            p for p in room_data["participants"] if p["nickname"] != nickname
        ]

        # Broadcast participant left to all clients
        if nickname:
            await _broadcast_json(room_id, {
                "type": "participant_left",
                "nickname": nickname,
            })

        # Broadcast updated participants list
        if nickname:
            await websocket.send_json({
                "type": "participants",
                "participants": room_data["participants"],
            })

        # Only remove room data if no connections remain
        if not _connections.get(room_id):
            _connections.pop(room_id, None)
            _initialized_rooms.discard(room_id)
            _room_data.pop(room_id, None)
            # Clean up chat room from chat_manager registry
            from app.chat_manager import cleanup_room
            cleanup_room(room_id)

        # Clean up rate-limit state for this disconnected connection
        conn_id = id(websocket)
        rate_limiter = get_rate_limiter()
        rate_limiter.remove_connection(conn_id)

        db.close()