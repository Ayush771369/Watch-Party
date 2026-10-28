from fastapi import APIRouter, Depends, HTTPException, status  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.database import get_db
from app.models.room import Room, RoomState
from app.models.video import Video
from app.schemas import RoomCreateRequest, RoomResponse, RoomStateResponse
from app.storage import generate_room_id
from app.auth import hash_secret

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(body: RoomCreateRequest, db: Session = Depends(get_db)):
    # --- verify the video exists ---
    video = db.query(Video).filter(Video.video_id == body.video_id).first()
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video '{body.video_id}' not found.",
        )

    # --- create room + initial state ---
    room_id = generate_room_id()

    # Hash the host secret and store the hash on the room
    host_secret_hash = hash_secret(body.host_secret)

    room = Room(
        room_id=room_id,
        video_id=body.video_id,
        owner_id=body.owner_id,
        host_secret_hash=host_secret_hash,
    )
    room_state = RoomState(room_id=room_id)

    db.add(room)
    db.add(room_state)
    db.commit()
    db.refresh(room)

    return _room_to_response(room)


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: str, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Room '{room_id}' not found.",
        )
    return _room_to_response(room)


def _room_to_response(room: Room) -> RoomResponse:
    """Convert a Room ORM object to a RoomResponse, building the shareable link."""
    return RoomResponse(
        room_id=room.room_id,
        video_id=room.video_id,
        owner_id=room.owner_id,
        created_at=room.created_at,
        room_link=f"/watch/{room.room_id}",
        state=RoomStateResponse.model_validate(room.state),
    )
