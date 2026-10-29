V2 Implementation Complete - Ready for Review

Changes summary:
- Backend: ws.py - nickname auth, chat messages, participant presence, bounded in-memory state
- Frontend: RoomPage.jsx - nickname prompt, chat input/display, tabbed Chat/Participants sidebar
- All V1 features preserved (host-secret auth, playback sync, reconnect, error codes)
- Backward-compatible: V1 clients can still join as viewers

Next: run backend tests, then manual two-client verification. Do not merge/deploy without approval.