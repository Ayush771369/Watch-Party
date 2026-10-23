from pathlib import Path

# Base directory for all uploaded videos
UPLOAD_DIR = Path("uploads")

# Allowed video MIME types
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/x-matroska",   # .mkv
    "video/webm",
    "video/quicktime",    # .mov
    "video/x-msvideo",    # .avi
}

# 2 GB upload limit
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024 * 1024
