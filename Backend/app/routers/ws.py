import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.database import SessionLocal
from app.models.room import Room, RoomState
from app.schemas import PlaybackUpdate
from app.auth import verify_secret

router = APIRouter()

# Active WebSocket connections grouped by room_id
_connections: dict[str, list[WebSocket]] = {}

# Track rooms that have been initialized (have at least one connected client)
_initialized_rooms: set[str] = set()

# In-memory room state: nicknames, participants, chat history
_room_data: dict[str, dict] = {}


def _get_room_data(room_id: str) -> dict:
    """Get or initialize in-memory room data."""
    if room_id not in _room_data:
        _room_data[room_id] = {
            "nicknames": {},          # nickname -> websocket
            "participants": [],       # list[{"nickname": str, "role": str, "connected_at": datetime}]
            "chat_history": [],       # list[{"nickname": str, "text": str, "timestamp": datetime}]
        }
    return _room_data[room_id]


async def _broadcast_json(room_id: str, message: dict, exclude_ws: Optional[WebSocket] = None):
    """Broadcast a JSON message to all WebSocket connections in a room."""
    payload = json.dumps(message)
    for ws in _connections.get(room_id, []):
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
        _connections[room_id] = []
    _connections[room_id].append(websocket)

    # --- initialize room data if needed ---
    room_data = _get_room_data(room_id)

    # --- send current state to the newly connected client ---
    state = db.query(RoomState).filter(RoomState.room_id == room_id).first()
    await websocket.send_json({
        "type": "state_sync",
        "playback_position_seconds": state.playback_position_seconds,
        "is_playing": state.is_playing,
    })

    # Send initial participants list and chat history
    await websocket.send_json({
        "type": "participants",
        "participants": room_data["participants"],
    })

    await websocket.send_json({
        "type": "chat_history",
        "chat_history": room_data["chat_history"][-50:]  # last 50 messages
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
            "type": "state_sync",
            "playback_position_seconds": state.playback_position_seconds,
            "is_playing": state.is_playing,
        })

        # --- Nickname setup ---
        max_retries = 3
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

            nickname = data.get("nickname", "").strip()

            # Validate nickname
            if not nickname:
                await websocket.send_json({
                    "type": "error",
                    "message": "Nickname cannot be empty.",
                })
                nickname = None
                retry_count += 1
                continue

            if len(nickname) < 3:
                await websocket.send_json({
                    "type": "error",
                    "message": "Nickname must be at least 3 characters.",
                })
                nickname = None
                retry_count += 1
                continue

            if len(nickname) > 20:
                await websocket.send_json({
                    "type": "error",
                    "message": "Nickname must be at most 20 characters.",
                })
                nickname = None
                retry_count += 1
                continue

            # Check for duplicate nickname
            if nickname in room_data["nicknames"]:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Nickname '{nickname}' is already taken. Please choose another.",
                })
                nickname = None
                retry_count += 1
                continue

            # Accept the nickname
            room_data["nicknames"][nickname] = websocket
            room_data["participants"].append({
                "nickname": nickname,
                "role": role,
                "connected_at": datetime.utcnow(),
            })

            # Broadcast participant joined to all other clients
            await _broadcast_json(room_id, {
                "type": "participant_joined",
                "nickname": nickname,
                "role": role,
            })

            # Broadcast updated participants list to all clients
            await websocket.send_json({
                "type": "participants",
                "participants": room_data["participants"],
            })

        # --- process playback update messages ---
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
                text = data.get("text", "").strip()
                if not text:
                    continue

                # Validate message length
                if len(text) > 200:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Message must be at most 200 characters.",
                    })
                    continue

                # Add to chat history
                room_data["chat_history"].append({
                    "nickname": nickname,
                    "text": text,
                    "timestamp": datetime.utcnow(),
                })
                # Keep chat history bounded (last 100 messages)
                if len(room_data["chat_history"]) > 100:
                    room_data["chat_history"] = room_data["chat_history"][-100:]

                # Broadcast chat message to all other clients
                chat_msg = {
                    "type": "chat_message",
                    "nickname": nickname,
                    "text": text,
                    "timestamp": room_data["chat_history"][-1]["timestamp"],
                }
                await _broadcast_json(room_id, chat_msg)
                continue

            # Handle playback_update messages
            if data.get("type") == "playback_update":
                action = data.get("action")
                position = data.get("playback_position_seconds")

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
        _connections.get(room_id, []).remove(websocket) if websocket in _connections.get(room_id, []) else None

        # Remove nickname and participant on disconnect
        if nickname and nickname in room_data.get("nicknames", {}):
            del room_data["nicknames"][nickname]

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
        await websocket.send_json({
            "type": "participants",
            "participants": room_data["participants"],
        })

        if not _connections.get(room_id):
            _connections.pop(room_id, None)
            _initialized_rooms.discard(room_id)
            _room_data.pop(room_id, None)

        db.close()