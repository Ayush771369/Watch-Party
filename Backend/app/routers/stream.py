import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status  # type: ignore
from fastapi.responses import StreamingResponse  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.config import UPLOAD_DIR
from app.database import get_db
from app.models.room import Room
from app.models.video import Video

router = APIRouter(tags=["streaming"])

CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Map file extensions to MIME types based on ALLOWED_CONTENT_TYPES
EXTENSION_TO_MIME = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}


def get_content_type(file_path: Path) -> str:
    """Infer content type from file extension."""
    ext = file_path.suffix.lower()
    return EXTENSION_TO_MIME.get(ext, "application/octet-stream")


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """
    Parse a Range header and return (start, end) byte positions.

    Supports:
    - bytes=start-end (both specified)
    - bytes=start- (open-ended)
    - bytes=-suffix (suffix length)

    Raises ValueError for invalid or unsatisfiable ranges.
    """
    if not range_header.startswith("bytes="):
        raise ValueError("Invalid range unit")

    range_spec = range_header[6:]  # Remove "bytes="

    if range_spec.startswith("-"):
        # Suffix range: bytes=-500 means last 500 bytes
        suffix_length = int(range_spec[1:])
        if suffix_length <= 0:
            raise ValueError("Invalid suffix length")
        start = max(0, file_size - suffix_length)
        end = file_size - 1
        return start, end

    if "-" not in range_spec:
        raise ValueError("Invalid range format")

    start_str, end_str = range_spec.split("-", 1)

    start = int(start_str)
    end = int(end_str) if end_str else file_size - 1

    # Validate range
    if start < 0 or start >= file_size:
        raise ValueError("Range not satisfiable")

    if end >= file_size:
        end = file_size - 1

    if start > end:
        raise ValueError("Invalid range")

    return start, end


async def file_sender(file_path: Path, start: int, end: int):
    """Async generator that yields file chunks from start to end (inclusive)."""
    bytes_remaining = end - start + 1

    with open(file_path, "rb") as f:
        f.seek(start)
        while bytes_remaining > 0:
            chunk_size = min(CHUNK_SIZE, bytes_remaining)
            data = f.read(chunk_size)
            if not data:
                break
            bytes_remaining -= len(data)
            yield data


@router.get("/rooms/{room_id}/stream")
async def stream_video(
    room_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    # --- lookup room ---
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Room '{room_id}' not found.",
        )

    # --- lookup video ---
    video = db.query(Video).filter(Video.video_id == room.video_id).first()
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video '{room.video_id}' not found.",
        )

    # --- resolve file path ---
    file_path = Path(video.file_path)

    # Security: ensure file_path is within UPLOAD_DIR (prevent path traversal)
    try:
        file_path = file_path.resolve()
        upload_dir_resolved = UPLOAD_DIR.resolve()
        if not str(file_path).startswith(str(upload_dir_resolved)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied.",
            )
    except (OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    # --- check file exists ---
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video file not found.",
        )

    file_size = file_path.stat().st_size

    # --- handle zero-length file ---
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail="Video file is empty.",
        )

    content_type = get_content_type(file_path)

    # --- check for Range header ---
    range_header = request.headers.get("range")

    if not range_header:
        # No range requested - return full file
        return StreamingResponse(
            file_sender(file_path, 0, file_size - 1),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # --- parse range header ---
    try:
        start, end = parse_range_header(range_header, file_size)
    except ValueError:
        # Unsatisfiable range
        return StreamingResponse(
            iter([b""]),
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes */{file_size}",
            },
        )

    # --- return partial content ---
    content_length = end - start + 1

    return StreamingResponse(
        file_sender(file_path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Range": f"bytes {start}-{end}/{file_size}",
        },
    )
