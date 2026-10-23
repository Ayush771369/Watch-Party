import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from app.config import ALLOWED_CONTENT_TYPES, UPLOAD_DIR
from app.database import get_db
from app.schemas import UploadResponse
from app.storage import generate_video_id, save_video_record

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(file: UploadFile, db: Session = Depends(get_db)):
    # --- validate that a file was actually sent ---
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided.",
        )

    # --- validate MIME type ---
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content type '{file.content_type}' is not allowed. "
                   f"Accepted types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    # --- generate id & build destination path ---
    video_id = generate_video_id()
    extension = Path(file.filename).suffix  # e.g. ".mp4"
    dest_path = UPLOAD_DIR / f"{video_id}{extension}"

    # --- stream file to disk ---
    try:
        with dest_path.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save video: {exc}",
        )
    finally:
        await file.close()

    # --- get file size and reject zero-length files ---
    file_size = os.path.getsize(dest_path)
    if file_size == 0:
        # Clean up the empty file
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zero-length files are not allowed.",
        )

    # --- persist the record to database ---
    save_video_record(db, video_id, file.filename, dest_path, file_size)

    return UploadResponse(video_id=video_id, filename=file.filename)
