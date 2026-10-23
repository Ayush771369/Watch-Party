import os
from pathlib import Path

import pytest #type: ignore
from fastapi.testclient import TestClient  # type: ignore

from app.models.room import Room, RoomState
from app.models.video import Video


class TestStreamEndpoint:
    """Tests for the video streaming endpoint."""

    def test_stream_without_range_header(self, client, db_session, temp_upload_dir):
        """Test normal streaming request without Range header returns 200 OK."""
        # Create video file
        video_path = temp_upload_dir / "video1.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid1",
            filename="video1.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room1", video_id="vid1", owner_id="owner1")
        room_state = RoomState(room_id="room1")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request without Range header
        response = client.get("/rooms/room1/stream")

        assert response.status_code == 200
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == str(len(video_content))
        assert response.content == video_content

    def test_stream_with_valid_range(self, client, db_session, temp_upload_dir):
        """Test valid range request returns 206 Partial Content."""
        # Create video file
        video_path = temp_upload_dir / "video2.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid2",
            filename="video2.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room2", video_id="vid2", owner_id="owner1")
        room_state = RoomState(room_id="room2")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request with Range header
        response = client.get("/rooms/room2/stream", headers={"Range": "bytes=0-99"})

        assert response.status_code == 206
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == "100"
        assert response.headers["content-range"] == f"bytes 0-99/{len(video_content)}"
        assert response.content == video_content[0:100]

    def test_stream_open_ended_range(self, client, db_session, temp_upload_dir):
        """Test open-ended range request (bytes=start-)."""
        # Create video file
        video_path = temp_upload_dir / "video3.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid3",
            filename="video3.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room3", video_id="vid3", owner_id="owner1")
        room_state = RoomState(room_id="room3")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request with open-ended range
        response = client.get("/rooms/room3/stream", headers={"Range": "bytes=950-"})

        assert response.status_code == 206
        assert response.headers["content-length"] == "50"
        assert response.headers["content-range"] == f"bytes 950-999/{len(video_content)}"
        assert response.content == video_content[950:]

    def test_stream_suffix_range(self, client, db_session, temp_upload_dir):
        """Test suffix range request (bytes=-N means last N bytes)."""
        # Create video file
        video_path = temp_upload_dir / "video4.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid4",
            filename="video4.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room4", video_id="vid4", owner_id="owner1")
        room_state = RoomState(room_id="room4")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request last 100 bytes
        response = client.get("/rooms/room4/stream", headers={"Range": "bytes=-100"})

        assert response.status_code == 206
        assert response.headers["content-length"] == "100"
        assert response.headers["content-range"] == f"bytes 900-999/{len(video_content)}"
        assert response.content == video_content[-100:]

    def test_unsatisfiable_range(self, client, db_session, temp_upload_dir):
        """Test unsatisfiable range returns 416."""
        # Create video file
        video_path = temp_upload_dir / "video5.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid5",
            filename="video5.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room5", video_id="vid5", owner_id="owner1")
        room_state = RoomState(room_id="room5")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request range beyond file size
        response = client.get("/rooms/room5/stream", headers={"Range": "bytes=2000-3000"})

        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(video_content)}"

    def test_nonexistent_room(self, client):
        """Test request for nonexistent room returns 404."""
        response = client.get("/rooms/nonexistent_room/stream")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_room_with_missing_video_file(self, client, db_session, temp_upload_dir):
        """Test room whose video file is missing returns 404."""
        # Create video record pointing to non-existent file
        video = Video(
            video_id="vid6",
            filename="missing.mp4",
            file_path=str(temp_upload_dir / "nonexistent.mp4"),
            file_size_bytes=1000,
        )
        room = Room(room_id="room6", video_id="vid6", owner_id="owner1")
        room_state = RoomState(room_id="room6")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        response = client.get("/rooms/room6/stream")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_zero_length_file(self, client, db_session, temp_upload_dir):
        """Test zero-length file returns appropriate error."""
        # Create zero-length video file
        video_path = temp_upload_dir / "empty.mp4"
        video_path.write_bytes(b"")

        # Create video and room records
        video = Video(
            video_id="vid_empty",
            filename="empty.mp4",
            file_path=str(video_path),
            file_size_bytes=0,
        )
        room = Room(room_id="room_empty", video_id="vid_empty", owner_id="owner1")
        room_state = RoomState(room_id="room_empty")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        response = client.get("/rooms/room_empty/stream")
        assert response.status_code == 204

    def test_content_type_inference(self, client, db_session, temp_upload_dir):
        """Test that content type is correctly inferred from extension."""
        # Test different extensions
        test_cases = [
            ("video.webm", "video/webm"),
            ("video.mkv", "video/x-matroska"),
            ("video.mov", "video/quicktime"),
            ("video.avi", "video/x-msvideo"),
        ]

        for filename, expected_mime in test_cases:
            video_path = temp_upload_dir / filename
            video_content = b"test content"
            video_path.write_bytes(video_content)

            video_id = f"vid_{filename.replace('.', '_')}"
            room_id = f"room_{filename.replace('.', '_')}"

            video = Video(
                video_id=video_id,
                filename=filename,
                file_path=str(video_path),
                file_size_bytes=len(video_content),
            )
            room = Room(room_id=room_id, video_id=video_id, owner_id="owner1")
            room_state = RoomState(room_id=room_id)
            db_session.add_all([video, room, room_state])
            db_session.commit()

            response = client.get(f"/rooms/{room_id}/stream")
            assert response.status_code == 200
            assert response.headers["content-type"] == expected_mime

    def test_range_beyond_file_end(self, client, db_session, temp_upload_dir):
        """Test range that extends beyond file end is clamped."""
        # Create video file
        video_path = temp_upload_dir / "video_clamp.mp4"
        video_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
        video_path.write_bytes(video_content)

        # Create video and room records
        video = Video(
            video_id="vid_clamp",
            filename="video_clamp.mp4",
            file_path=str(video_path),
            file_size_bytes=len(video_content),
        )
        room = Room(room_id="room_clamp", video_id="vid_clamp", owner_id="owner1")
        room_state = RoomState(room_id="room_clamp")
        db_session.add_all([video, room, room_state])
        db_session.commit()

        # Request range that goes beyond file size
        response = client.get(
            "/rooms/room_clamp/stream", headers={"Range": "bytes=950-2000"}
        )

        assert response.status_code == 206
        assert response.headers["content-length"] == "50"
        assert response.headers["content-range"] == f"bytes 950-999/{len(video_content)}"
