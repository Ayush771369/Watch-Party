from datetime import datetime

from sqlalchemy import Column, String, DateTime, Float, Boolean, ForeignKey #type: ignore
from sqlalchemy.orm import relationship #type: ignore

from app.database import Base


class Room(Base):
    __tablename__ = "rooms"

    room_id = Column(String, primary_key=True, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    owner_id = Column(String, nullable=False)
    host_secret_hash = Column(String, nullable=True)  # bcrypt hash of host secret; stored as '' when not set
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    # Relationship to room state
    state = relationship("RoomState", back_populates="room", uselist=False, cascade="all, delete-orphan")


class RoomState(Base):
    __tablename__ = "room_state"

    room_id = Column(String, ForeignKey("rooms.room_id"), primary_key=True)
    playback_position_seconds = Column(Float, default=0.0, nullable=False)
    is_playing = Column(Boolean, default=False, nullable=False)
    last_updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship back to room
    room = relationship("Room", back_populates="state")
