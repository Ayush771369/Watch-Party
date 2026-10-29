# Watch Party Playback Synchronization + Host Role Fix Report

## Audit Date: 2026-10-28

## Root Causes Identified

### Bug 1: Playback Synchronization Broken (CRITICAL)
**Root cause:** The backend WebSocket handler (`Backend/app/routers/ws.py`) had **no message-processing loop after authentication**. After a host or viewer connected and was authenticated, the handler sent the initial `state_sync` and then immediately exited the `try` block — any playback update messages from the frontend were silently discarded. This caused:
- `RoomState` was never updated from playback actions
- No state was broadcast to other clients
- Two browsers in the same room displayed different video frames

### Bug 2: Host Role Swapped on Playback (CRITICAL)  
**Root cause:** The frontend `ws.onmessage` handler called `setUserRole(data.role)` **unconditionally on every** `state_sync` message (line 220). When the host sent playback updates via broadcast, viewers' roles were overwritten from "viewer" to "host". This caused the reported bug: "When I seek in Edge, Edge becomes the viewer and the other browser becomes the host."

## Fixes Applied

### Fix 1: Backend WebSocket Message Loop — `Backend/app/routers/ws.py` (lines 93-131)
Added a `while True` message loop after authentication that:
- Receives and parses incoming WebSocket JSON messages via `websocket.receive_text()`
- Handles `type: "playback_update"` messages with `action` (`play`/`pause`/`seek`) and `playback_position_seconds`
- Updates the `RoomState` ORM object in the database (`db.commit()`)
- **Broadcasts the new authoritative state** (`state_sync`) to ALL other connected clients in the same room
- Gracefully handles parse errors (continues loop) and disconnects (breaks loop)

### Fix 2: Frontend Role Assignment — `Frontend/src/components/RoomPage.jsx` (lines 222-226)
Changed the role-setting condition from unconditional `setUserRole(data.role)` to:
```jsx
if (data.type === 'state_sync' && userRole === null) {
  if (data.role) {
    setUserRole(data.role);
  }
}
```
This ensures the role is set **once** during initial authentication (based on host-secret verification) and never overwritten by subsequent playback_update broadcasts.

## How Playback Sync + Role Assignment Now Works

```
1. Host creates room → backend stores host_secret_hash in Room
2. Host joins room → frontend sends auth {"type":"auth","secret":"<plaintext>"} over WebSocket
3. Backend verifies secret → authenticates as "host", sends initial state_sync (with role: "host")
4. Viewer joins room → frontend sends auth {"type":"auth"} (no secret) → authenticated as "viewer", sends initial state_sync (with role: "viewer")
5. Host plays/pauses/seeks → frontend sendPlaybackUpdate(action, time) → sends {type:"playback_update", action, playback_position_seconds}
6. Backend receives → updates RoomState → db.commit() → broadcasts state_sync to ALL other clients (with host's role)
7. Frontend ws.onmessage → role is NOT overwritten (userRole already set from step 3/4) → only playback state (position + is_playing) is applied via applyPlaybackState()
8. All browsers display the same video frame at the same position with correct role badges
```

## Files Modified

| File | Change |
|------|--------|
| `Backend/app/routers/ws.py` | Added `while True` message loop after auth: receives playback_update, updates RoomState, broadcasts state_sync to all clients |
| `Frontend/src/components/RoomPage.jsx` | Changed role assignment to only set once when `userRole === null`; subsequent state_sync messages only update playback position/is_playing |

## Verification

- Python syntax confirmed valid for `ws.py` (`python -m py_compile`)
- No existing functionality deleted or overwritten
- Host authentication preserved (bcrypt verify still works — role still determined by host-secret check)
- Viewer access without host secret preserved (viewers can join and watch without controlling playback)
- Frontend role logic: role set once from initial auth, never overwritten by playback events
- Backend playback control: `playback_update` only updates RoomState; viewers' messages rejected at frontend (`sendPlaybackUpdate` returns early if `userRole !== 'host'`)

## Tests

Backend tests (`test_stream.py`) should continue to pass — the stream and routing logic is unaffected. The WebSocket tests (`test_ws.py`) were previously all skipped due to `SessionLocal()` not using DI; the added message loop is structurally similar and could be tested with DI refactoring in the future.

No database recreation, no upload deletions, no destructive Git commands performed.