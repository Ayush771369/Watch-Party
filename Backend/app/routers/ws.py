import json
from datetime import datetime

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

    # --- send current state to the newly connected client ---
    state = db.query(RoomState).filter(RoomState.room_id == room_id).first()
    await websocket.send_json({
        "type": "state_sync",
        "playback_position_seconds": state.playback_position_seconds,
        "is_playing": state.is_playing,
    })

    try:
        # --- WebSocket authentication ---
        role: Optional[str] = None
        is_authenticated = False

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

        # --- process playback update messages ---
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                break

            if not isinstance(data, dict):
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

                    # Broadcast updated state to all other clients in this room
                    broadcast = {
                        "type": "state_sync",
                        "playback_position_seconds": state.playback_position_seconds,
                        "is_playing": state.is_playing,
                    }
                    for ws in _connections.get(room_id, []):
                        if ws != websocket:
                            try:
                                await ws.send_json(broadcast)
                            except Exception:
                                pass

    # ... rest continues
    finally:
        # --- cleanup on disconnect ---
        _connections.get(room_id, []).remove(websocket) if websocket in _connections.get(room_id, []) else None
        if not _connections.get(room_id):
            _connections.pop(room_id, None)
            _initialized_rooms.discard(room_id)
        db.close()
