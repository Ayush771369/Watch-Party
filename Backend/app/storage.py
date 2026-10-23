import uuid
from pathlib import Path

from sqlalchemy.orm import Session #type: ignore

from app.config import UPLOAD_DIR
from app.models.video import Video


def init_upload_dir() -> None:
    """Create the upload directory if it doesn't exist."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def generate_video_id() -> str:
    return uuid.uuid4().hex


def generate_room_id() -> str:
    return uuid.uuid4().hex


def save_video_record(db: Session, video_id: str, original_filename: str, file_path: Path, file_size: int) -> Video:
    video = Video(
        video_id=video_id,
        filename=original_filename,
        file_path=str(file_path),
        file_size_bytes=file_size,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def get_video_record(db: Session, video_id: str) -> Video | None:
    return db.query(Video).filter(Video.video_id == video_id).first()
