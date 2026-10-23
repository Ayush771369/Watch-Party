import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.database import SessionLocal
from app.models.room import Room, RoomState
from app.schemas import PlaybackUpdate

router = APIRouter()

# Active WebSocket connections grouped by room_id
_connections: dict[str, list[WebSocket]] = {}


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
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            update = PlaybackUpdate(**data)

            # --- update room state in database ---
            state = db.query(RoomState).filter(RoomState.room_id == room_id).first()

            if update.action == "play":
                state.is_playing = True
                state.playback_position_seconds = update.playback_position_seconds
            elif update.action == "pause":
                state.is_playing = False
                state.playback_position_seconds = update.playback_position_seconds
            elif update.action == "seek":
                state.playback_position_seconds = update.playback_position_seconds

            state.last_updated_at = datetime.utcnow()
            db.commit()

            # --- broadcast to all other clients in the room ---
            message = {
                "type": "playback_update",
                "action": update.action,
                "playback_position_seconds": update.playback_position_seconds,
                "is_playing": state.is_playing,
            }
            for conn in _connections.get(room_id, []):
                if conn != websocket:
                    try:
                        await conn.send_json(message)
                    except Exception:
                        pass

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        # --- cleanup on disconnect ---
        _connections.get(room_id, []).remove(websocket) if websocket in _connections.get(room_id, []) else None
        if not _connections.get(room_id):
            _connections.pop(room_id, None)
        db.close()
