from datetime import datetime

from pydantic import BaseModel  # type: ignore


# --- Video Schemas ---

class UploadResponse(BaseModel):
    video_id: str
    filename: str


# --- Room Schemas ---

class RoomCreateRequest(BaseModel):
    video_id: str
    owner_id: str
    host_secret: str  # plain secret; backend stores only the bcrypt hash


class RoomStateResponse(BaseModel):
    playback_position_seconds: float
    is_playing: bool
    last_updated_at: datetime

    class Config:
        from_attributes = True


class RoomResponse(BaseModel):
    room_id: str
    video_id: str
    owner_id: str
    created_at: datetime
    room_link: str
    state: RoomStateResponse

    class Config:
        from_attributes = True


# --- WebSocket Schemas ---

class PlaybackUpdate(BaseModel):
    """Message sent over WebSocket to sync playback state."""
    action: str  # "play", "pause", "seek"
    playback_position_seconds: float
