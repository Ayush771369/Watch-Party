# Watch Party Backend

A FastAPI-based backend for Watch Party — a web application where users can upload videos and create synchronized watch party rooms.

## Features

- ✅ Video upload with unique ID generation
- ✅ Room creation for watch parties
- ✅ Real-time playback synchronization via WebSockets
- ✅ SQLite database for persistence across restarts
- ✅ Multiple rooms can share the same video

## Project Structure

```
Backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Configuration (upload dir, allowed types, limits)
│   ├── database.py          # SQLAlchemy setup
│   ├── storage.py           # ID generation and storage utilities
│   ├── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── video.py         # Video database model
│   │   └── room.py          # Room and RoomState models
│   └── routers/
│       ├── __init__.py
│       ├── upload.py        # POST /videos/upload
│       ├── room.py          # Room creation and retrieval
│       └── ws.py            # WebSocket endpoint for real-time sync
├── uploads/                 # Created at startup, stores uploaded videos
├── requirements.txt
├── test_setup.py           # Database verification script
└── watch_party.db          # SQLite database (created on first run)
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`

## API Endpoints

### 1. Upload Video

**POST** `/videos/upload`

Upload a video file to create a new video resource.

**Request:**
- Content-Type: `multipart/form-data`
- Body: `file` (UploadFile)

**Response:** `201 Created`
```json
{
  "video_id": "a1b2c3d4e5f6...",
  "filename": "movie.mp4"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/videos/upload \
  -F "file=@movie.mp4"
```

**Supported formats:**
- video/mp4
- video/x-matroska (.mkv)
- video/webm
- video/quicktime (.mov)
- video/x-msvideo (.avi)

---

### 2. Create Room

**POST** `/rooms`

Create a new watch party room for a video.

**Request:**
```json
{
  "video_id": "a1b2c3d4e5f6...",
  "owner_id": "client-generated-uuid"
}
```

**Response:** `201 Created`
```json
{
  "room_id": "x9y8z7w6v5...",
  "video_id": "a1b2c3d4e5f6...",
  "owner_id": "client-generated-uuid",
  "created_at": "2026-10-22T13:00:00",
  "room_link": "/watch/x9y8z7w6v5...",
  "state": {
    "playback_position_seconds": 0.0,
    "is_playing": false,
    "last_updated_at": "2026-10-22T13:00:00"
  }
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/rooms \
  -H "Content-Type: application/json" \
  -d '{"video_id": "abc123", "owner_id": "user-uuid-123"}'
```

---

### 3. Get Room

**GET** `/rooms/{room_id}`

Retrieve room details and current playback state.

**Response:** `200 OK`
```json
{
  "room_id": "x9y8z7w6v5...",
  "video_id": "a1b2c3d4e5f6...",
  "owner_id": "client-generated-uuid",
  "created_at": "2026-10-22T13:00:00",
  "room_link": "/watch/x9y8z7w6v5...",
  "state": {
    "playback_position_seconds": 45.2,
    "is_playing": true,
    "last_updated_at": "2026-10-22T13:05:30"
  }
}
```

---

### 4. WebSocket — Real-time Sync

**WebSocket** `/ws/{room_id}`

Connect to a room for real-time playback synchronization.

**Connection:**
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/x9y8z7w6v5...');
```

**On Connect:**
Server sends current state:
```json
{
  "type": "state_sync",
  "playback_position_seconds": 45.2,
  "is_playing": true
}
```

**Client → Server (Playback Control):**
```json
{
  "action": "play",  // or "pause", "seek"
  "playback_position_seconds": 45.2
}
```

**Server → Clients (Broadcast):**
```json
{
  "type": "playback_update",
  "action": "play",
  "playback_position_seconds": 45.2,
  "is_playing": true
}
```

**Actions:**
- `play` — Start playback from position
- `pause` — Pause at position
- `seek` — Jump to position

---

## Database Schema

### `videos`
| Column | Type | Description |
|--------|------|-------------|
| video_id | String (PK) | Unique video identifier |
| filename | String | Original filename |
| file_path | String | Local disk path |
| uploaded_at | DateTime | Upload timestamp |
| file_size_bytes | Integer | File size |

### `rooms`
| Column | Type | Description |
|--------|------|-------------|
| room_id | String (PK) | Unique room identifier |
| video_id | String (FK) | References videos.video_id |
| owner_id | String | Client-generated UUID |
| created_at | DateTime | Creation timestamp |
| expires_at | DateTime (nullable) | Future expiration (not implemented) |

### `room_state`
| Column | Type | Description |
|--------|------|-------------|
| room_id | String (PK, FK) | References rooms.room_id |
| playback_position_seconds | Float | Current video timestamp |
| is_playing | Boolean | Play/pause state |
| last_updated_at | DateTime | Last state change |

**Relationships:**
- One video → many rooms
- One room → one room_state (1:1)

---

## Testing

Run the database setup test:
```bash
python test_setup.py
```

Check API health:
```bash
curl http://localhost:8000/health
```

---

## Configuration

Edit `app/config.py` to customize:

```python
UPLOAD_DIR = Path("uploads")
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
}
```

---

## Architecture Decisions

### Client-Generated Owner ID
No authentication system yet. The room creator generates a UUID in their browser and sends it with the room creation request. This unblocks the prototype while keeping the door open for real auth later.

### SQLite for Persistence
All room state and video metadata persists to `watch_party.db`. Backend restarts don't lose room state — clients can reconnect and resume watching.

### Separate `room_state` Table
Hot data (updated every few seconds) is separated from cold data (written once at room creation). Makes it easier to add fields like viewer count or last activity without bloating the rooms table.

### WebSocket State Sync
Real-time playback control. When one viewer plays/pauses/seeks, all other viewers in the room receive the update instantly. State is written to the database on every change, so it survives restarts.

---

## Future Enhancements

- [ ] Room expiration mechanism (expires_at logic)
- [ ] User authentication system
- [ ] Video streaming endpoint
- [ ] Viewer count tracking
- [ ] Chat functionality
- [ ] Room access control (public/private)

---

## Development

Interactive API docs: http://localhost:8000/docs

Watch for file changes:
```bash
uvicorn app.main:app --reload
```
