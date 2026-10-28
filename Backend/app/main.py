import os
from fastapi import FastAPI  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore

from app.database import Base, engine
from app.models import Video, Room, RoomState  # noqa: F401
from app.routers import upload
from app.routers import room
from app.routers import ws
from app.routers import stream
from app.storage import init_upload_dir

# Create all database tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Watch Party", version="0.1.0")

# CORS configuration - allow frontend origin from environment variable
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Range"],
    allow_credentials=False,
    max_age=86400,  # Cache preflight for 24 hours
)

# make sure the uploads folder exists on startup
init_upload_dir()

app.include_router(upload.router)
app.include_router(room.router)
app.include_router(ws.router)
app.include_router(stream.router)


@app.get("/")
async def root():
    return {"message": "Welcome to Watch Party!"}


@app.get("/health")
async def health():

    return {"status": "ok"}
