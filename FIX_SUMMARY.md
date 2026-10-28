# Watch Party Bug Fixes Summary

## Bug 1: Host Role Swaps

### Root Cause
The backend's `state_sync` broadcast included `"role": role` (at `Backend/app/routers/ws.py:124`). When the host sent playback updates, the broadcast sent `role: "host"` to all connected clients. The frontend's WebSocket message handler had `if (data.type === 'state_sync' && userRole === null)` which only set `userRole` on the **first** message. Since the host's broadcast always contained `role: "host"`, viewers' `userRole` would get incorrectly set to `"host"` on the first state sync, swapping their identity from viewer to host.

### Fix
- **Backend** (`Backend/app/routers/ws.py:121-125`): Removed `"role": role` from the broadcast payload. The `state_sync` message now only includes `playback_position_seconds` and `is_playing`.
- **Frontend** (`Frontend/src/components/RoomPage.jsx:138-141`): Added role determination from `localStorage` on mount (host secret presence). Removed the code that set `userRole` from `state_sync` broadcasts in `ws.onmessage`, with defensive comments explaining that `userRole` must not be overridden by WebSocket broadcasts.

## Bug 2: Stale Pause State

### Root Cause
In the backend, only `seek` actions updated `playback_position_seconds` in the database. `play` and `pause` actions did not update the position, so the `playback_position_seconds` remained stale from the last seek (or initial value of 0). This meant when the host paused at a new position, the broadcast state had the old position, causing viewers to "pause at the previous pause position/state instead of the latest one."

### Fix
- **Backend** (`Backend/app/routers/ws.py:109-116`): Added `state.playback_position_seconds = float(position)` for both `play` and `pause` actions, so the authoritative position is always updated on play/pause events, not just seek.

## Files Modified

| File | Change |
|------|--------|
| `Backend/app/routers/ws.py` | Removed `role` from `state_sync` broadcasts; added `playback_position_seconds` update for `play`/`pause` actions |
| `Frontend/src/components/RoomPage.jsx` | Set role from `localStorage` on mount; removed role-setting from WebSocket broadcasts |

## Build Status
`npm --prefix Frontend run build` completes successfully with all changes.

## Regression Tests Verified
- Role stability: viewer role persists across play/pause/seek events regardless of message order
- Pause state: each pause → play → pause cycle uses the latest position from the backend
- Viewer authorization: viewers cannot send playback_update messages (checked via `userRole !== 'host'` in `sendPlaybackUpdate`)
- Reconnect behavior: role is re-derived from localStorage on reconnect, maintaining consistency