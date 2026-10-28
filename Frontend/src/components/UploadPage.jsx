import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import './UploadPage.css';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const FRONTEND_BASE_URL = import.meta.env.VITE_FRONTEND_BASE_URL || 'http://localhost:5173';

function UploadPage() {
  const navigate = useNavigate();
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFileName, setSelectedFileName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadResult, setUploadResult] = useState(null);
  const [creatingRoom, setCreatingRoom] = useState(false);
  const [roomResult, setRoomResult] = useState(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);
  const dragDepthRef = useRef(0);

  const ALLOWED_TYPES = [
    'video/mp4',
    'video/webm',
    'video/x-matroska', // .mkv
    'video/quicktime',  // .mov
    'video/x-msvideo',  // .avi
  ];

  const validateAndSetFile = (file) => {
    setError('');
    setUploadResult(null);
    setRoomResult(null);
    setCopied(false);

    if (!file) {
      return;
    }

    if (!ALLOWED_TYPES.includes(file.type)) {
      setError(`Invalid file type: ${file.type || 'unknown'}. Allowed types: MP4, WebM, MKV, MOV, AVI`);
      return;
    }

    // The backend rejects zero-length files; catch it before the upload starts.
    if (file.size === 0) {
      setError('This file is empty. Zero-length files cannot be used.');
      return;
    }

    setSelectedFile(file);
    setSelectedFileName(file.name);
  };

  const handleFileSelect = (e) => {
    validateAndSetFile(e.target.files[0]);
  };

  const handleDragEnter = (e) => {
    e.preventDefault();
    if (uploading || creatingRoom) {
      return;
    }
    dragDepthRef.current += 1;
    setIsDragging(true);
  };

  const handleDragOver = (e) => {
    // Prevent default so the browser allows the drop.
    e.preventDefault();
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    if (uploading || creatingRoom) {
      return;
    }
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragging(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    if (uploading || creatingRoom) {
      return;
    }
    dragDepthRef.current = 0;
    setIsDragging(false);
    validateAndSetFile(e.dataTransfer.files[0]);
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      setError('Please select a file first');
      return;
    }

    setUploading(true);
    setError('');
    setUploadResult(null);
    setRoomResult(null);
    setCopied(false);
    setUploadProgress(0);

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const xhr = new XMLHttpRequest();

      // Track upload progress
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percentComplete = Math.round((e.loaded / e.total) * 100);
          setUploadProgress(percentComplete);
        }
      });

      // Create promise for XHR
      const uploadPromise = new Promise((resolve, reject) => {
        xhr.addEventListener('load', () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const response = JSON.parse(xhr.responseText);
              resolve(response);
            } catch {
              reject(new Error('Failed to parse server response'));
            }
          } else {
            try {
              const errorResponse = JSON.parse(xhr.responseText);
              reject(new Error(errorResponse.detail || `Upload failed with status ${xhr.status}`));
            } catch {
              reject(new Error(`Upload failed with status ${xhr.status}`));
            }
          }
        });

        xhr.addEventListener('error', () => {
          reject(new Error('Network error. Please check if the server is running.'));
        });

        xhr.addEventListener('abort', () => {
          reject(new Error('Upload was cancelled'));
        });
      });

      xhr.open('POST', `${API_BASE_URL}/videos/upload`);
      xhr.send(formData);

      const result = await uploadPromise;
      setUploadResult(result);
      setSelectedFile(null);
      setSelectedFileName('');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } catch (err) {
      setError(err.message || 'An unexpected error occurred');
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleCreateRoom = async () => {
    if (!uploadResult?.video_id) {
      setError('No video uploaded. Please upload a video first.');
      return;
    }

    setCreatingRoom(true);
    setError('');
    setCopied(false);

    try {
      // Generate a one-time host secret (backend stores only a bcrypt hash).
      // Keep the plaintext in localStorage so this browser can prove host identity later.
      const hostSecret = crypto.randomUUID().replace(/-/g, '') + crypto.randomUUID().replace(/-/g, '');

      const response = await fetch(`${API_BASE_URL}/rooms`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          video_id: uploadResult.video_id,
          owner_id: generateOwnerId(),
          host_secret: hostSecret,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed to create room (${response.status})`);
      }

      const roomData = await response.json();
      setRoomResult(roomData);

      // Stash the plaintext secret — the backend only kept the hash, so this is
      // the sole copy that can prove this browser created the room.
      try {
        localStorage.setItem(`host_secret_${roomData.room_id}`, hostSecret);
      } catch (err) {
        console.error('Failed to save host secret:', err);
      }

      // Construct room URL and copy to clipboard
      const roomUrl = `${FRONTEND_BASE_URL}/room/${roomData.room_id}`;
      await copyToClipboard(roomUrl);

      // Navigate to room page
      navigate(`/room/${roomData.room_id}`);
    } catch (err) {
      setError(err.message || 'Failed to create room');
    } finally {
      setCreatingRoom(false);
    }
  };

  const generateOwnerId = () => {
    // Generate a simple owner ID for this session
    return `user_${Date.now().toString(36)}`;
  };

  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.style.position = 'fixed';
      textArea.style.left = '-9999px';
      document.body.appendChild(textArea);
      textArea.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 3000);
      } catch (fallbackErr) {
        console.error('Fallback copy failed:', fallbackErr);
      }
      document.body.removeChild(textArea);
    }
  };

  const handleReset = () => {
    setSelectedFile(null);
    setSelectedFileName('');
    setError('');
    setUploadResult(null);
    setRoomResult(null);
    setCopied(false);
    setIsDragging(false);
    dragDepthRef.current = 0;
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const formatFileSize = (bytes) => {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return '';
    }
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    const decimals = value >= 100 || unitIndex === 0 ? 0 : 1;
    return `${value.toFixed(decimals)} ${units[unitIndex]}`;
  };

  const busy = uploading || creatingRoom;

  return (
    <div className="upload-page">
      <div className="ambient-glow ambient-glow-top" aria-hidden="true" />
      <div className="ambient-glow ambient-glow-bottom" aria-hidden="true" />

      <div className="upload-shell">
        {/* A. Header */}
        <header className="upload-header">
          <div className="brand-group">
            <span className="brand-icon" aria-hidden="true">🎬</span>
            <span className="brand-name">Watch Party</span>
          </div>
          <span className="header-tagline">Your people. Your movie night.</span>
        </header>

        {/* B. Hero */}
        <section className="upload-hero">
          <div className="hero-deco" aria-hidden="true">
            <span className="deco-tile deco-a">🍿</span>
            <span className="deco-tile deco-b">🎬</span>
            <span className="deco-tile deco-c">✨</span>
          </div>
          <h1 className="hero-title">Your next movie night starts here.</h1>
          <p className="hero-subtitle">Bring your video. Invite your people. Press play together.</p>
        </section>

        {/* C-E. Main upload card */}
        <main className="upload-card">
          <h2 className="card-title">Bring your video</h2>
          <p className="card-subtitle">Choose a video from your device to get the watch party started.</p>

          <div className="upload-form">
            {/* Drop zone — the real file input is absolutely positioned over it,
                so mouse clicks, keyboard (Enter/Space) and touch all open the picker. */}
            <div
              className={`dropzone ${isDragging ? 'dragging' : ''} ${busy ? 'dropzone-disabled' : ''}`}
              onDragEnter={handleDragEnter}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileSelect}
                accept="video/mp4,video/webm,video/x-matroska,video/quicktime,video/x-msvideo,.mp4,.webm,.mkv,.mov,.avi"
                disabled={busy}
                className="file-input"
                id="video-file"
                aria-label="Choose a video file from your device"
              />
              <div className="dropzone-inner">
                {isDragging ? (
                  <>
                    <svg className="dropzone-icon dropzone-icon-drag" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="4 14 10 14 10 20" />
                      <polyline points="20 10 14 10 14 4" />
                      <line x1="14" y1="10" x2="21" y2="3" />
                      <line x1="3" y1="21" x2="10" y2="14" />
                    </svg>
                    <p className="dropzone-title">Release to add your video</p>
                  </>
                ) : (
                  <>
                    <svg className="dropzone-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <rect x="2" y="4" width="20" height="16" rx="3" />
                      <line x1="7" y1="4" x2="7" y2="20" />
                      <line x1="17" y1="4" x2="17" y2="20" />
                      <line x1="2" y1="9" x2="7" y2="9" />
                      <line x1="2" y1="15" x2="7" y2="15" />
                      <line x1="17" y1="9" x2="22" y2="9" />
                      <line x1="17" y1="15" x2="22" y2="15" />
                      <polygon points="10 9 15 12 10 15 10 9" fill="currentColor" stroke="none" />
                    </svg>
                    <p className="dropzone-title">Drop your video here</p>
                    <p className="dropzone-sub">or choose a file from your device</p>
                    <span className="dropzone-cta">Choose video</span>
                    <p className="dropzone-formats">
                      <span className="formats-label">Supported formats:</span> MP4 · WebM · MKV · MOV · AVI
                    </p>
                  </>
                )}
              </div>
            </div>

            {/* Selected File Display */}
            {selectedFileName && (
              <div className="selected-file">
                <svg className="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polygon points="23 7 16 7 16 0 23 7" />
                  <path d="M16 7V0H3a2 2 0 0 0-2 2v20a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V7h-7z" />
                </svg>
                <div className="file-details">
                  <span className="file-name" title={selectedFileName}>{selectedFileName}</span>
                  {selectedFile && (
                    <span className="file-size">{formatFileSize(selectedFile.size)}</span>
                  )}
                </div>
                <button
                  onClick={handleReset}
                  className="remove-btn"
                  disabled={busy}
                  aria-label={`Remove selected file ${selectedFileName}`}
                >
                  ×
                </button>
              </div>
            )}

            {/* Upload Progress */}
            {uploading && (
              <div className="progress-container">
                <div
                  className="progress-bar"
                  role="progressbar"
                  aria-valuenow={uploadProgress}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Upload progress"
                >
                  <div
                    className="progress-fill"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <span className="progress-text">
                  {uploadProgress < 100 ? `Uploading… ${uploadProgress}%` : 'Processing…'}
                </span>
              </div>
            )}

            {/* Error Display */}
            {error && (
              <div className="error-message" role="alert">
                <svg className="error-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="15" y1="9" x2="9" y2="15" />
                  <line x1="9" y1="9" x2="15" y2="15" />
                </svg>
                <div className="error-content">
                  <span>{error}</span>
                  {selectedFile && !uploading && (
                    <button
                      type="button"
                      onClick={handleUpload}
                      className="retry-btn"
                    >
                      Try again
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Success Display */}
            {uploadResult && (
              <div className="success-message" role="status">
                <svg className="success-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
                <div className="success-content">
                  <p className="success-title">Upload complete!</p>
                  <p className="success-detail">
                    <strong>Filename:</strong> {uploadResult.filename}
                  </p>
                  <p className="success-detail video-id">
                    <strong>Video ID:</strong> <code>{uploadResult.video_id}</code>
                  </p>
                </div>
              </div>
            )}

            {/* Room Created Display */}
            {roomResult && (
              <div className="room-success-message" role="status">
                <svg className="success-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                  <polyline points="9 22 9 12 15 12 15 22" />
                </svg>
                <div className="success-content">
                  <p className="success-title">Room created!</p>
                  <p className="success-detail">
                    <strong>Room ID:</strong> <code>{roomResult.room_id}</code>
                  </p>
                  <p className="success-detail copied-notice">
                    {copied ? '✓ Link copied to clipboard!' : 'Copying link…'}
                  </p>
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div className="button-group">
              {/* Upload Button */}
              {selectedFile && !uploadResult && (
                <button
                  onClick={handleUpload}
                  disabled={busy}
                  className="upload-btn"
                >
                  {uploading ? `Uploading… ${uploadProgress}%` : 'Upload video'}
                </button>
              )}

              {/* Create Room Button - Only shown after successful upload */}
              {uploadResult && !roomResult && (
                <button
                  onClick={handleCreateRoom}
                  disabled={creatingRoom}
                  className="create-room-btn"
                >
                  {creatingRoom ? 'Creating your room…' : 'Create my watch party'}
                </button>
              )}

              {/* Reset Button */}
              {(selectedFile || uploadResult || error) && (
                <button
                  onClick={handleReset}
                  disabled={busy}
                  className="reset-btn"
                >
                  Start over
                </button>
              )}
            </div>
          </div>
        </main>

        {/* F. Reassurance section */}
        <section className="benefits-section" aria-label="Why Watch Party">
          <div className="benefit">
            <span className="benefit-icon" aria-hidden="true">🎞️</span>
            <div className="benefit-copy">
              <p className="benefit-title">Your local video</p>
              <p className="benefit-text">Upload straight from your device.</p>
            </div>
          </div>
          <div className="benefit">
            <span className="benefit-icon" aria-hidden="true">💌</span>
            <div className="benefit-copy">
              <p className="benefit-title">Easy room invites</p>
              <p className="benefit-text">Share a link — friends just open it.</p>
            </div>
          </div>
          <div className="benefit">
            <span className="benefit-icon" aria-hidden="true">🍿</span>
            <div className="benefit-copy">
              <p className="benefit-title">Synced playback</p>
              <p className="benefit-text">Everyone watches in real time.</p>
            </div>
          </div>
        </section>

        {/* G. Footer */}
        <footer className="upload-footer">
          <p>Watch Party — movie nights, together.</p>
        </footer>
      </div>
    </div>
  );
}

export default UploadPage;
