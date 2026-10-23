import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient  # type: ignore
from sqlalchemy import create_engine  # type: ignore
from sqlalchemy.orm import sessionmaker  # type: ignore
from sqlalchemy.pool import StaticPool  # type: ignore

from app.database import Base, get_db
from app.main import app
from app.models.room import Room, RoomState
from app.models.video import Video


# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database session for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def temp_upload_dir(tmp_path: Path):
    """Create a temporary upload directory."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return upload_dir


@pytest.fixture(scope="function")
def client(db_session, temp_upload_dir: Path):
    """Create a test client with database dependency override and patched UPLOAD_DIR."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Patch UPLOAD_DIR to use the temp directory
    with patch("app.config.UPLOAD_DIR", temp_upload_dir):
        with patch("app.routers.stream.UPLOAD_DIR", temp_upload_dir):
            with TestClient(app) as test_client:
                yield test_client

    app.dependency_overrides.clear()
