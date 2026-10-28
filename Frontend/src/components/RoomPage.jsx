import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import './RoomPage.css';

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

function RoomPage() {
  const { roomId } = useParams();
  const [room, setRoom] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [userRole, setUserRole] = useState(null); // 'host' or 'viewer'
  const [wsStatus, setWsStatus] = useState('connecting'); // 'connecting' | 'connected' | 'disconnected'
  const [copied, setCopied] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState('');
  // Set when the browser refuses audible autoplay; cleared once playback starts.
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);

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

          // Note: userRole is determined from localStorage on mount (host secret presence)
          // and must not be overridden by any WebSocket broadcast messages.
          // Deliberately NOT setting userRole from state_sync/playback_update to prevent
          // role swapping bugs (Bug 1): broadcast role fields can incorrectly overwrite
          // client identity, and the guard `userRole === null` only prevents multiple sets,
          // allowing the first (potentially wrong) set to stick.

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
  }, [roomId, applyPlaybackState]);

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

  // Viewer-only recovery for a blocked audible autoplay. This runs inside a
  // real user gesture and only drives the local element: it never changes the
  // room's playback state or sends a host command.
  const handleViewerJoinPlayback = () => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const latest = latestStateRef.current;
    isProgrammaticChange.current = true;

    // Re-apply the most recent position so the viewer joins where the host is.
    if (latest && Number.isFinite(latest.playback_position_seconds)) {
      try {
        video.currentTime = latest.playback_position_seconds;
      } catch (err) {
        console.warn('Could not seek before joining playback:', err);
      }
    }

    diagLog('Immediately before viewer-join video.play()', {
      videoState: formatDiagState(video),
      currentTime: video.currentTime,
      paused: video.paused,
      readyState: video.readyState,
    });
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise
        .then(() => {
          diagLog('viewer-join video.play() Promise resolved', {
            videoState: formatDiagState(video),
          });
          setAutoplayBlocked(false);
        })
        .catch((err) => {
          diagLog('viewer-join video.play() Promise rejected', {
            errorName: err?.name,
            errorMessage: err?.message,
            videoState: formatDiagState(video),
          });
          if (err && err.name === 'NotAllowedError') {
            setAutoplayBlocked(true);
          } else if (!err || err.name !== 'AbortError') {
            console.error('Failed to start playback:', err);
          }
        })
        .finally(() => {
          setTimeout(() => {
            isProgrammaticChange.current = false;
          }, 100);
        });
    } else {
      setTimeout(() => {
        isProgrammaticChange.current = false;
      }, 100);
    }
  };

  const handlePlay = () => {
    // Any successful play (including the join gesture) clears the prompt.
    setAutoplayBlocked(false);
    // Note: sendPlaybackUpdate always sends to the backend regardless of
    // isProgrammaticChange — that flag only affects applyPlaybackState,
    // not outbound playback-control messages.
    if (videoRef.current) {
      sendPlaybackUpdate('play', videoRef.current.currentTime);
    }
  };

  const handlePause = () => {
    if (videoRef.current) {
      sendPlaybackUpdate('pause', videoRef.current.currentTime);
    }
  };

  const handleSeeked = () => {
    // Note: sendPlaybackUpdate always sends to the backend regardless of
    // isProgrammaticChange — that flag only affects applyPlaybackState,
    // not outbound playback-control messages.
    if (videoRef.current) {
      sendPlaybackUpdate('seek', videoRef.current.currentTime);
    }
  };

  const handleCopyInviteLink = async () => {
    const shareUrl = window.location.href;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        // Fallback for non-HTTPS or older browsers
        const textArea = document.createElement('textarea');
        textArea.value = shareUrl;
        textArea.style.position = 'fixed';
        textArea.style.left = '-9999px';
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
      }
      setCopied(true);
      setCopyFeedback('Link copied to clipboard!');
      setTimeout(() => {
        setCopied(false);
        setCopyFeedback('');
      }, 3000);
    } catch (err) {
      console.error('Failed to copy room link:', err);
      setCopyFeedback('Could not copy automatically. Please copy the URL manually.');
      setTimeout(() => setCopyFeedback(''), 4000);
    }
  };

  if (loading) {
    return (
      <div className="room-page">
        <div className="state-card loading-card">
          <div className="popcorn-loader">
            <span className="popcorn-icon">🍿</span>
            <div className="glow-ring"></div>
          </div>
          <h2>Setting up movie night...</h2>
          <p className="state-subtitle">Connecting to your room and synchronizing stream</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="room-page">
        <div className="state-card error-card">
          <div className="state-icon error-bubble">⚠️</div>
          <h2>Unable to Join Room</h2>
          <p className="state-subtitle">{error}</p>
          <Link to="/" className="action-button primary-btn">
            ← Back to Upload
          </Link>
        </div>
      </div>
    );
  }

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
            {/* Card 1: Room Headline & Vibe */}
            <div className="lounge-card room-summary-card">
              <div className="card-header">
                <div className="badge-wrapper">
                  <span className="party-badge">🍿 Movie Night</span>
                </div>
                <span className="room-tagline">
                  {isHost ? "You're hosting movie night" : "You're watching together"}
                </span>
              </div>

              <div className="room-meta-row">
                <span className="meta-label">Room ID</span>
                <code className="meta-code" title={roomId}>
                  {roomId}
                </code>
              </div>

              <div className="room-meta-row">
                <span className="meta-label">Your Role</span>
                <span className={`meta-role-pill ${userRole || 'viewer'}`}>
                  {isHost ? '👑 Room Host (Controller)' : '👀 Viewer (Listener)'}
                </span>
              </div>
            </div>

            {/* Card 2: Invite Friends & Share Link */}
            <div className="lounge-card invite-card">
              <div className="invite-header">
                <span className="invite-title-icon">💌</span>
                <div>
                  <h2 className="invite-title">Invite your crew</h2>
                  <p className="invite-subtitle">Send this link to friends so they can watch along</p>
                </div>
              </div>

              <div className="invite-box">
                <input
                  type="text"
                  readOnly
                  value={shareUrl}
                  className="invite-url-input"
                  aria-label="Room invitation link"
                  onFocus={(e) => e.target.select()}
                />
                <button
                  type="button"
                  onClick={handleCopyInviteLink}
                  className={`copy-button ${copied ? 'copied' : ''}`}
                  aria-label="Copy invitation link"
                >
                  <span className="button-icon">{copied ? '✓' : '📋'}</span>
                  <span>{copied ? 'Copied!' : 'Copy Link'}</span>
                </button>
              </div>

              {copyFeedback && (
                <p className={`copy-feedback ${copied ? 'success' : 'error'}`} role="status">
                  {copyFeedback}
                </p>
              )}
            </div>

            {/* Card 3: Cozy Watch Tips */}
            <div className="lounge-card tips-card">
              <h3 className="tips-title">
                <span className="tips-icon">💡</span> Cozy Watch Tips
              </h3>
              <ul className="tips-list">
                <li>
                  <span className="bullet-dot"></span>
                  <span>Audio volume and fullscreen are always in your personal control.</span>
                </li>
                <li>
                  <span className="bullet-dot"></span>
                  <span>Stream is synced with sub-second precision over WebSockets.</span>
                </li>
                {isHost ? (
                  <li>
                    <span className="bullet-dot"></span>
                    <span>Keep this tab open as the host to keep party controls active.</span>
                  </li>
                ) : (
                  <li>
                    <span className="bullet-dot"></span>
                    <span>If your video gets paused by the host, it will resume automatically.</span>
                  </li>
                )}
              </ul>
            </div>
          </aside>
        </main>
      </div>
    </div>
  );
}

export default RoomPage;
