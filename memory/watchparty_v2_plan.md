# Watch Party V2 Implementation Plan

## Current State (V1)
- Frontend: `RoomPage.jsx` - video player, host/auth via localStorage host_secret, playback controls
- Backend: `ws.py` - WebSocket at `/ws/{room_id}`, host secret auth, `state_sync` + `playback_update` messages
- Models: `Room`, `RoomState` in SQLite; host_secret stored as bcrypt hash
- No nicknames, no chat, no participant presence

## V2 Requirements Summary
1. nickname entry before joining
2. preserve host-secret auth + playback permissions
3. realtime room chat over WebSocket
4. live participant presence (join/leave)
5. one room sidebar with Chat + Participants tabs
6. chat in memory only (no persistent DB yet)
7. mobile-responsive, consistent design
8. nickname/message length limits, payload validation, duplicate nickname handling
9. preserve playback sync + reconnect
10. no paid services or unnecessary deps

## Protocol Changes

### New WebSocket Message Types
- `nickname`: Client sends `{type: "nickname", nickname: "Alice"}` after connection
- `chat_message`: `{type: "chat_message", nickname: "Alice", text: "Hello!"}`
- `participants`: Server broadcasts `{type: "participants", participants: [...]}`
- `participant_joined`: Server broadcasts `{type: "participant_joined", nickname: "Alice"}`
- `participant_left`: Server broadcasts `{type: "participant_left", nickname: "Alice"}`

### Backend State Per Room
- `nicknames: dict[str, WebSocket]` - nickname → WebSocket mapping (enforces uniqueness)
- `participants: list[dict]` - `[{nickname, role, connected_at}]`
- `chat_history: list[dict]` - `[{nickname, text, timestamp}]` (bounded, e.g., last 50)

### Authentication Preservation
- Host secret still works as before (role = "host")
- Viewers connect without secret (role = "viewer")
- Nickname is separate from role; a viewer can set a nickname

## Implementation Phases

### Phase 1: Backend - WebSocket nickname + chat + presence
- Modify `ws.py` to:
  - Accept nickname after connection (`nickname` message type)
  - Enforce unique nicknames per room
  - Broadcast `participant_joined` to all clients
  - Broadcast `participant_left` on disconnect
  - Broadcast `chat_message` to all clients
  - Store in-memory room state (nicknames, participants, chat_history)
  - Validate nickname length (e.g., 3-20 chars), message length (e.g., 1-200 chars)
  - Send initial `participants` list on new client connect

### Phase 2: Frontend - nickname input
- Before WebSocket connect, show nickname prompt
- Store nickname in localStorage (persist across reconnects)
- Send `nickname` message over WebSocket after connect
- Handle duplicate nickname error

### Phase 3: Frontend - Chat/Participants sidebar
- Add tabbed sidebar component (Chat + Participants)
- Chat tab: message input + scrollable message list
- Participants tab: list of current nicknames with roles
- Limit chat input to prevent empty messages

### Phase 4: Verification
- Run existing tests
- Manual testing: two clients in same room, verify chat, presence, playback

## Edge Cases
- Duplicate nickname: reject with error message, keep existing nickname
- Nickname on reconnect: restore from localStorage, if nickname taken, prompt user
- Disconnect/cleanup: remove nickname from room state, broadcast `participant_left`
- Chat history: send to new clients on connect (bounded history)
- Empty nickname/message: reject with error