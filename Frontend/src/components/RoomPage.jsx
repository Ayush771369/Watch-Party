import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import './RoomPage.css';

// API/WebSocket base URL
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

// --- TEMPORARY DIAGNOSTIC LOGGING (Playback timing investigation) ---
const diagLog = (event, details = {}) => {
  const ts = performance.now().toFixed(2);
  console.log(`[PlaybackDiag] +${ts}ms | ${event}`, details);
};

const formatDiagState = (video) => {
  if (!video) {
    return { exists: false };
  }
  return {
    readyState: video.readyState,
    currentTime: Number(video.currentTime?.toFixed?.(3) ?? video.currentTime),
    paused: video.paused,
    seeking: video.seeking,
    networkState: video.networkState,
  };
};
// --------------------------------------------------------------------


// WebSocket reconnect backoff: 1s, 2s, 4s, 8s, then hold at 10s.
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 10000;

// Nickname validation constants
const NICKNAME_MIN = 3;
const NICKNAME_MAX = 20;
const MESSAGE_MAX = 200;

// Maximum chat history to keep in memory
const MAX_CHAT_HISTORY = 100;

// --------------------------------------------------------------------

function RoomPage() {
  const { roomId } = useParams();
  const [room, setRoom] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [userRole, setUserRole] = useState(null); // 'host' or 'viewer'
  const [wsStatus, setWsStatus] = useState('connecting'); // 'connecting' | 'connected' | 'disconnected'
  const [copied, setCopied] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState('');

  const [autoplayBlocked, setAutoplayBlocked] = useState(false);

  // Nickname state
  const [nickname, setNickname] = useState('');
  const [nicknameError, setNicknameError] = useState('');
  const [nicknameSubmitted, setNicknameSubmitted] = useState(false);

  // Chat state
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [showChatTab, setShowChatTab] = useState(true); // true = Chat, false = Participants

  // Participants state
  const [participants, setParticipants] = useState([]);

  // Video refs
  const videoRef = useRef(null);
  const wsRef = useRef(null);
  // Flag to track if change is from WebSocket (not user interaction)
  const isProgrammaticChange = useRef(false);
  // Latest authoritative playback state from the server. Retained even while the
  // <video> element is not mounted, so it can be applied the moment it is.
  const latestStateRef = useRef(null);

  // Apply an authoritative playback state to the mounted <video> element.
  // Returns false when the element does not exist yet — the caller still keeps
  // the state in latestStateRef so it can be applied on mount.
  const applyPlaybackState = useCallback((state) => {
    const video = videoRef.current;
    diagLog('applyPlaybackState() entered', {
      hasVideo: Boolean(video),
      readyState: video?.readyState,
      currentTime: video?.currentTime,
      paused: video?.paused,
      targetPosition: state.playback_position_seconds,
      isPlaying: state.is_playing,
    });
    if (!video) {
      return false;
    }

    // Set flag to prevent event handlers from sending WebSocket message
    isProgrammaticChange.current = true;

    // Apply the change
    const target = state.playback_position_seconds;
    if (typeof target === 'number' && Number.isFinite(target)) {
      video.currentTime = target;
    }

    if (state.is_playing) {
      diagLog('Immediately before video.play()', {
        videoState: formatDiagState(video),
        currentTime: video.currentTime,
        paused: video.paused,
        readyState: video.readyState,
      });
      // play() always returns a promise in modern browsers; a rejection with
      // NotAllowedError means the browser wants a user gesture first.
      const playPromise = video.play();
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise
          .then(() => {
            diagLog('video.play() Promise resolved', {
              videoState: formatDiagState(video),
            });
            setAutoplayBlocked(false);
          })
          .catch((err) => {
            diagLog('video.play() Promise rejected', {
              errorName: err?.name,
              errorMessage: err?.message,
              videoState: formatDiagState(video),
            });
            if (err && err.name === 'NotAllowedError') {
              setAutoplayBlocked(true);
            } else if (err && err.name === 'AbortError') {
              // Superseded by a newer play/pause request; nothing to do.
            } else {
              console.error('Failed to start playback:', err);
            }
          });
      }
    } else {
      video.pause();
      setAutoplayBlocked(false);
    }

    // Reset flag after a short delay to ensure events have fired
    setTimeout(() => {
      isProgrammaticChange.current = false;
    }, 100);

    return true;
  }, []);

  // Apply any state that arrived before the <video> element was rendered.
  // The element only mounts once `room` is loaded, so this runs right after
  // that render commits.
  useEffect(() => {
    if (room && latestStateRef.current) {
      applyPlaybackState(latestStateRef.current);
    }
  }, [room, applyPlaybackState]);

  useEffect(() => {
    let ignore = false;
    let shouldReconnect = true;
    let reconnectAttempts = 0;
    let reconnectTimer = null;

    // Determine user role from localStorage (host secret presence)
    // This establishes identity before WebSocket authentication completes.
    const storedHostSecret = localStorage.getItem(`host_secret_${roomId}`);
    if (storedHostSecret) {
      setUserRole('host');
    } else {
      setUserRole('viewer');
    }

    async function loadRoomDetails() {
      try {
        const response = await fetch(`${API_BASE_URL}/rooms/${roomId}`);
        if (!response.ok) {
          throw new Error('Room not found');
        }
        const data = await response.json();
        if (!ignore) {
          setRoom(data);
          setLoading(false);
        }
      } catch (err) {
        if (!ignore) {
          setError(err.message || 'Failed to load room');
          setLoading(false);
        }
      }
    }

    function scheduleReconnect() {
      if (!shouldReconnect || reconnectTimer) {
        return;
      }
      const delay = Math.min(
        RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempts,
        RECONNECT_MAX_DELAY_MS
      );
      reconnectAttempts += 1;
      console.log(
        `WebSocket closed. Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`
      );
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!shouldReconnect) {
          return;
        }
        connect();
      }, delay);
    }

    function connect() {
      const wsUrl = `${API_BASE_URL.replace('http', 'ws')}/ws/${roomId}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      setWsStatus('connecting');

      ws.onopen = () => {
        if (!shouldReconnect) {
          return;
        }
        console.log('WebSocket connected');
        setWsStatus('connected');
        reconnectAttempts = 0;
        // Step 2: Send authentication message
        const hostSecret = localStorage.getItem(`host_secret_${roomId}`);
        if (hostSecret) {
          ws.send(
            JSON.stringify({
              type: 'auth',
              secret: hostSecret,
            })
          );
        } else {
          ws.send(
            JSON.stringify({
              type: 'auth',
              role: 'viewer',
            })
          );
        }
      };

      ws.onmessage = (event) => {
        if (!shouldReconnect) {
          return;
        }
        try {
          const data = JSON.parse(event.data);
          console.log('WebSocket message:', data);

          if (data.type === 'state_sync' || data.type === 'playback_update') {
            diagLog('WebSocket message received', {
              type: data.type,
              targetPosition: data.playback_position_seconds,
              isPlaying: data.is_playing,
              videoState: formatDiagState(videoRef.current),
            });
            // Retain the latest authoritative state even if the <video>
            // element isn't mounted yet; it is applied as soon as it is.
            latestStateRef.current = {
              playback_position_seconds: data.playback_position_seconds,
              is_playing: Boolean(data.is_playing),
            };
            applyPlaybackState(latestStateRef.current);
          }
          else if (data.type === 'request_nickname') {
            // Server is asking for a nickname - show the nickname prompt
            // This is handled by the UI, not by overriding userRole
            diagLog('Received nickname request from server', data);
          }
          else if (data.type === 'participant_joined') {
            // Add new participant to the list
            setParticipants(prev => {
              // Check if already in list
              if (prev.some(p => p.nickname === data.nickname)) {
                return prev;
              }
              return [...prev, {
                nickname: data.nickname,
                role: data.role,
                connected_at: new Date(),
              }];
            });
            diagLog('Participant joined:', data);
          }
          else if (data.type === 'participant_left') {
            // Remove participant from the list
            setParticipants(prev => prev.filter(p => p.nickname !== data.nickname));
            diagLog('Participant left:', data);
          }
          else if (data.type === 'chat_message') {
            // Add chat message to the list
            setChatMessages(prev => {
              // Prevent duplicates if same message arrives twice
              const last = prev[prev.length - 1];
              if (last && last.nickname === data.nickname && last.text === data.text) {
                return prev;
              }
              return [...prev, {
                nickname: data.nickname,
                text: data.text,
                timestamp: data.timestamp,
              }];
            });
            diagLog('Chat message received:', data);
          }
          else if (data.type === 'participants') {
            // Update participants list from server
            setParticipants(data.participants);
            diagLog('Participants updated:', data);
          }
          else if (data.type === 'chat_history') {
            // Initialize chat history on connect
            setChatMessages(data.chat_history);
            diagLog('Chat history loaded:', data);
          }
          else if (data.type === 'error') {
            setError(data.message || 'Unknown error');
            diagLog('WebSocket error:', data);
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err);
        }
      };

      ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        if (shouldReconnect) {
          setWsStatus('disconnected');
        }
      };

      ws.onclose = (event) => {
        // Ignore closes from sockets that were replaced or cleaned up.
        if (!shouldReconnect || wsRef.current !== ws) {
          return;
        }
        wsRef.current = null;
        console.log('WebSocket closed:', event.code, event.reason);
        setWsStatus('disconnected');
        if (event.code === 4001) {
          shouldReconnect = false;
          setError('Authentication rejected: Invalid host secret');
        } else if (event.code === 4004) {
          shouldReconnect = false;
          setError('Room not found');
        } else {
          // Transient loss: reconnect and let the server's fresh state_sync
          // re-authoritate playback, without requiring a page reload.
          scheduleReconnect();
        }
      };
    }

    loadRoomDetails();
    connect();

    return () => {
      ignore = true;
      shouldReconnect = false;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.close();
      }
    };
  }, [roomId]);

  // Send playback update to backend
  const sendPlaybackUpdate = (action, time) => {
    // Only host can control playback
    if (userRole !== 'host') {
      return;
    }
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'playback_update',
          action,
          playback_position_seconds: time,
        })
      );
    }
  };

  // Handle chat input send
  const handleSendChat = () => {
    const trimmed = chatInput.trim();
    if (!trimmed || userRole === null) {
      return;
    }
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'chat_message',
          nickname: nickname || 'Anonymous',
          text: trimmed,
        })
      );
    }
    setChatInput('');
  };

  // Handle nickname submission
  const handleNicknameSubmit = (e) => {
    e.preventDefault();
    const trimmed = nickname.trim();
    if (!trimmed) {
      setNicknameError('Nickname cannot be empty');
      return;
    }
    if (trimmed.length < NICKNAME_MIN) {
      setNicknameError(`Nickname must be at least ${NICKNAME_MIN} characters`);
      return;
    }
    if (trimmed.length > NICKNAME_MAX) {
      setNicknameError(`Nickname must be at most ${NICKNAME_MAX} characters`);
      return;
    }
    // Send nickname to server
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'nickname',
          nickname: trimmed,
        })
      );
    }
    setNickname(trimmed);
    setNicknameError('');
    setNicknameSubmitted(true);
  };

  // Handle nickname received from server response
  const handleNicknameResponse = (data) => {
    if (data.type === 'error') {
      setNicknameError(data.message);
      setNickname('');
    } else if (data.type === 'nickname_accepted') {
      setNickname(data.nickname);
      setNicknameError('');
      setNicknameSubmitted(true);
    }
  };

  // Clear error after typing
  useEffect(() => {
    if (nickname) {
      setNicknameError('');
    }
  }, [nickname]);

  // Handle playback events from the video element
  // These are called when the user interacts with the video player
  const handlePlay = () => {
    // Any successful play (including the join gesture) clears the prompt.
    setAutoplayBlocked(false);
    if (isProgrammaticChange.current) {
      return;
    }
    if (videoRef.current) {
      sendPlaybackUpdate('play', videoRef.current.currentTime);
    }
  };

  const handlePause = () => {
    if (isProgrammaticChange.current) {
      return;
    }
    if (videoRef.current) {
      sendPlaybackUpdate('pause', videoRef.current.currentTime);
    }
  };

  const handleSeeked = () => {
    if (isProgrammaticChange.current) {
      return;
    }
    if (videoRef.current) {
      sendPlaybackUpdate('seek', videoRef.current.currentTime);
    }
  };

  const isHost = userRole === 'host';

  const shareUrl = typeof window !== 'undefined' ? window.location.href : '';

  return (
    <div className="room-page">
      {/* Background ambient lighting */}
      <div className="ambient-glow ambient-glow-top"></div>
      <div className="ambient-glow ambient-glow-bottom"></div>

      <div className="room-container">
        {/* Navigation / Top Bar */}
        <header className="room-nav">
          <div className="nav-brand-group">
            <Link to="/" className="brand-logo" title="Back to video upload">
              <span className="brand-icon">🎬</span>
              <span className="brand-name">Watch Party</span>
            </Link>
            <span className="nav-divider">/</span>
            <span className="nav-room-chip" title={`Room ID: ${roomId}`}>
              <span className="chip-label">Room</span>
              <code className="chip-code">{roomId ? `${roomId.slice(0, 8)}…` : ''}</code>
            </span>
          </div>

          <div className="nav-status-group">
            {/* Live Connection Status */}
            <div
              className={`status-pill ${wsStatus}`}
              title={
                wsStatus === 'connected'
                  ? 'Connected and synchronized via WebSocket'
                  : wsStatus === 'connecting'
                    ? 'Connecting to room...'
                    : 'Disconnected from playback sync'
              }
            >
              <span className="status-dot"></span>
              <span className="status-label">
                {wsStatus === 'connected'
                  ? 'Live & Synced'
                  : wsStatus === 'connecting'
                    ? 'Connecting…'
                    : 'Disconnected'}
              </span>
            </div>

            {/* User Role Badge */}
            <div
              className={`role-pill ${userRole || 'pending'}`}
              title={
                isHost
                  ? 'You are the Host: You have full playback controls.'
                  : 'You are a Viewer: Video playback is controlled by the host.'
              }
            >
              <span className="role-icon">{isHost ? '👑' : '🍿'}</span>
              <span className="role-text">{isHost ? 'Host' : 'Viewer'}</span>
            </div>

            {/* Back to upload action */}
            <Link to="/" className="nav-action-link" title="Upload another video or create new room">
              <span>+ New Party</span>
            </Link>
          </div>
        </header>

        {/* Main Content Area */}
        <main className="room-main-content">
          {/* Cinema Stage (Video Player Area) */}
          <section className="cinema-stage">
            <div className="video-viewport">
              {room && (
                <video
                  ref={videoRef}
                  controls={isHost}
                  className="video-player"
                  src={`${API_BASE_URL}/rooms/${roomId}/stream`}
                  onPlay={handlePlay}
                  onPause={handlePause}
                  onSeeked={handleSeeked}
                  onLoadedMetadata={(e) => {
                    diagLog('Video emitted loadedmetadata', {
                      videoState: formatDiagState(e.currentTarget),
                      duration: e.currentTarget.duration,
                    });
                  }}
                  onCanPlay={(e) => {
                    diagLog('Video emitted canplay', {
                      videoState: formatDiagState(e.currentTarget),
                    });
                  }}
                  onPlaying={(e) => {
                    diagLog('Video emitted playing', {
                      videoState: formatDiagState(e.currentTarget),
                    });
                  }}
                  onWaiting={(e) => {
                    diagLog('Video emitted waiting', {
                      videoState: formatDiagState(e.currentTarget),
                    });
                  }}
                  playsInline
                >
                  Your browser does not support the video tag.
                </video>
              )}

              {autoplayBlocked && (
                <div className="autoplay-gate" role="alertdialog" aria-live="polite">
                  <span className="autoplay-gate-icon">🔊</span>
                  <h3 className="autoplay-gate-title">Your browser paused the sound</h3>
                  <p className="autoplay-gate-text">
                    Browsers only allow audio to start after you interact with the
                    page. Click below to join playback where the host is right now.
                  </p>
                  <button
                    type="button"
                    className="autoplay-gate-button"
                    onClick={handleViewerJoinPlayback}
                  >
                    ▶ Click to join playback
                  </button>
                  <p className="autoplay-gate-hint">
                    This only starts playback on your device — the host stays in control.
                  </p>
                </div>
              )}
            </div>

            {/* Playback Control Notice Bar */}
            <div className={`player-notice-bar ${isHost ? 'host-notice' : 'viewer-notice'}`}>
              <span className="notice-icon">{isHost ? '🎮' : '✨'}</span>
              <span className="notice-text">
                {isHost ? (
                  <>
                    <strong>You have the remote.</strong> Play, pause, or scrub to sync all viewers in real time.
                  </>
                ) : (
                  <>
                    <strong>Watching together.</strong> Playback is automatically synchronized by the host.
                  </>
                )}
              </span>
            </div>
          </section>

          {/* Social Room Sidebar / Lounge Information */}
          <aside className="room-lounge">
            {/* Tabbed sidebar: Chat and Participants */}
            <div className="sidebar-tabs">
              <button
                className={`sidebar-tab ${showChatTab ? 'active' : ''}`}
                onClick={() => setShowChatTab(true)}
                aria-label="Show Chat tab"
              >
                Chat <span className="tab-count">{chatMessages.length}</span>
              </button>
              <button
                className={`sidebar-tab ${!showChatTab ? 'active' : ''}`}
                onClick={() => setShowChatTab(false)}
                aria-label="Show Participants tab"
              >
                Participants <span className="tab-count">{participants.length}</span>
              </button>
            </div>

            {/* Chat Tab */}
            {showChatTab && (
              <div className="sidebar-tab-content chat-tab">
                {/* Chat messages */}
                <div className="chat-messages">
                  {chatMessages.map((msg, idx) => (
                    <div
                      key={idx}
                      className={`chat-message ${msg.nickname === nickname ? 'my-message' : 'other-message'}`}
                    >
                      <span className="message-nickname">{msg.nickname}:</span>
                      <span className="message-text">{msg.text}</span>
                      <span className="message-time">
                        {new Date(msg.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                  ))}
                  {chatMessages.length === 0 && (
                    <p className="empty-chat">No messages yet. Start the conversation!</p>
                  )}
                </div>

                {/* Chat input */}
                <div className="chat-input-area">
                  <div className="input-wrapper">
                    <input
                      type="text"
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          handleSendChat();
                        }
                      }}
                      placeholder="Type a message..."
                      maxLength={200}
                      disabled={userRole !== 'host' && userRole !== 'viewer'}
                      className="chat-input"
                      aria-label="Chat input"
                    />
                    <button
                      type="button"
                      onClick={handleSendChat}
                      className="send-button"
                      disabled={userRole === null}
                      aria-label="Send message"
                    >
                      ▶
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Participants Tab */}
            {!showChatTab && (
              <div className="sidebar-tab-content participants-tab">
                {participants.length === 0 && (
                  <p className="empty-participants">No participants yet.</p>
                )}
                <ul className="participants-list">
                  {participants.map((p, idx) => (
                    <li
                      key={idx}
                      className={`participant-item ${p.role === 'host' ? 'host' : 'viewer'}`}
                    >
                      <span className="participant-nickname">{p.nickname}</span>
                      <span className="participant-role {p.role}">
                        {p.role === 'host' ? '👑 Host' : '🍿 Viewer'}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </aside>
        </main>
      </div>
    </div>
  );
}

export default RoomPage;