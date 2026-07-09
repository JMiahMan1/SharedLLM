/* ── MA Web Player hook ─────────────────────────────────────────────────────
 *
 * Mirrors the Music Assistant frontend web player architecture:
 * 1. Sendspin protocol for audio transport (via /api/sendspin proxy)
 * 2. JSON-RPC control API for play/pause/seek commands (via /api/ma-jsonrpc proxy)
 *
 * Uses native WebSocket for Sendspin (required by SendspinPlayer library)
 * and WebSocketManager for JSON-RPC (auto-reconnect with backoff).
 * Audio plays directly through the browser's <audio> element via sendspin-js.
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { getPlayerId, savePlayerId, setState } from './webPlayer';
import { SendspinPlayer, type PlayerState } from '@sendspin/sendspin-js';
import type { ConnectionState } from './wsManager';

const STORAGE_KEY = 'sendspin_webplayer_id';

interface MAWebPlayerState {
  isConnected: boolean;
  isPlaying: boolean;
  volume: number;
  muted: boolean;
  playerState: PlayerState | null;
  error: string | null;
  mediaTitle: string | null;
  mediaArtist: string | null;
  mediaImage: string | null;
  position: number;
  duration: number;
  connectionState: ConnectionState;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
}

const API_KEY_STORAGE = 'jarvis_api_key' as const;
const MAX_RECONNECT_ATTEMPTS = 10;

function storageGetSync(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// Extract a usable image reference from a Music Assistant image payload.
// MA sends a few shapes:
//  - a full URL string
//  - a QueueItem `image` object: { type, path, provider, proxy_id }
//  - a PlayerMedia `image_url` string (current_media on player_updated)
//  - a legacy { uri, path } object
// We return whatever the gateway's /api/media/imageproxy can resolve.
function extractMaImage(img: unknown): string | null {
  if (!img) return null;
  if (typeof img === 'string') return img;
  if (typeof img === 'object') {
    const i = img as Record<string, unknown>;
    if (typeof i.image_url === 'string' && i.image_url) return i.image_url;
    if (typeof i.uri === 'string' && i.uri) return i.uri;
    if (typeof i.path === 'string' && i.path) return i.path;
  }
  return null;
}

/* ── Sendspin URL builder ──────────────────────────────────────────────────
 *
 * Returns a native WebSocket URL for the sendspin proxy.
 * SendspinPlayer requires a real WebSocket (not a wrapper class).
 */
function getSendspinUrl(baseUrl: string, apiToken: string): string {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${wsProtocol}://${window.location.host}/api/sendspin?token=${encodeURIComponent(apiToken)}`;
}

/* ── JSON-RPC URL builder ─────────────────────────────────────────────────
 *
 * Returns a native WebSocket URL for the JSON-RPC proxy.
 * Matches ma-stream-test.ts: plain WebSocket, not WebSocketManager.
 */
function getJsonRpcUrl(apiToken: string): string {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${wsProtocol}://${window.location.host}/api/ma-jsonrpc?token=${encodeURIComponent(apiToken)}`;
}

/* ── Hook ────────────────────────────────────────────────────────────────── */

export function useMAWebPlayer(onStateChange?: (state: MAWebPlayerState) => void) {
  const playerRef = useRef<SendspinPlayer | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const jsonrpcWsRef = useRef<WebSocket | null>(null);
  const sendspinWsRef = useRef<WebSocket | null>(null);
  const playerIdRef = useRef<string>('');
  const reconnectAttemptsRef = useRef(0);
  const [msgId, setMsgId] = useState(0);
  const [state, setStateLocal] = useState<MAWebPlayerState>({
    isConnected: false,
    isPlaying: false,
    volume: 70,
    muted: false,
    playerState: null,
    error: null,
    mediaTitle: null,
    mediaArtist: null,
    mediaImage: null,
    position: 0,
    duration: 0,
    connectionState: 'DISCONNECTED',
    reconnectAttempts: 0,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
  });

  const onStateChangeRef = useRef(onStateChange);
  useEffect(() => {
    onStateChangeRef.current = onStateChange;
  }, [onStateChange]);

  useEffect(() => {
    onStateChangeRef.current?.(state);
  }, [state]);

  const setError = useCallback((msg: string, err?: unknown) => {
    console.error('[MAWebPlayer] ERROR:', msg, err);
    setStateLocal(s => ({ ...s, error: msg }));
  }, []);

  const getPlayerIdRef = useCallback(() => {
    let id = getPlayerId();
    if (!id) {
      const arr = new Uint32Array(10);
      crypto.getRandomValues(arr);
      id = arr.join('');
      savePlayerId(id);
      setState({ player_id: id });
    }
    playerIdRef.current = id;
    return id;
  }, []);

  // Track connection state changes
  const updateConnectionState = useCallback((connState: ConnectionState, reconnectAttempts: number) => {
    const isConnected = connState === 'CONNECTED';
    const isFailed = connState === 'FAILED';

    console.log('[MAWebPlayer] Connection state:', connState, 'attempts:', reconnectAttempts);

    setStateLocal(s => {
      const next = {
        ...s,
        isConnected,
        connectionState: connState,
        reconnectAttempts,
        error: isFailed ? `Connection failed after ${MAX_RECONNECT_ATTEMPTS} attempts. Please refresh.` : null,
      };
      return next;
    });
  }, []);

  // Handle MA JSON-RPC events (queue_updated, player_updated)
  const handleMaEvent = useCallback((eventType: string, data: Record<string, unknown>) => {
    console.log('[MAWebPlayer] MA event:', eventType, data);

    setStateLocal(prev => {
      const next = { ...prev };

      if (eventType === 'queue_updated') {
        // current_item holds the active queue item. Use it only as a fallback
        // for title/artist/cover — if the authoritative player_updated
        // current_media has already populated them, don't let a playlist/source
        // name in current_item override the real track.
        const current = data?.current_item;
        if (current) {
          if (!next.mediaTitle) next.mediaTitle = current.name || null;
          if (!next.mediaArtist) next.mediaArtist = current.artist || null;
          if (!next.mediaImage) next.mediaImage = extractMaImage(current.image) || next.mediaImage;
          if (current.duration) next.duration = current.duration;
        }

        // Update play state from queue state
        const queueState = data?.state;
        if (queueState === 'playing') {
          next.isPlaying = true;
        } else if (queueState === 'paused' || queueState === 'idle') {
          next.isPlaying = false;
        }
      }

      if (eventType === 'player_updated') {
        // current_media is MA's authoritative "now playing" track and updates on
        // every track change — this is what drives the title/artist/cover.
        const media = data?.current_media;
        if (media) {
          if (media.title) next.mediaTitle = media.title;
          if (media.artist) next.mediaArtist = media.artist;
          const img = extractMaImage(media.image_url || media.image);
          if (img) next.mediaImage = img;
          if (media.duration) next.duration = media.duration;
          if (typeof media.elapsed_time === 'number') next.position = media.elapsed_time;
        }
        // Player-level state (volume, muted, position)
        if (data?.volume_level !== undefined) {
          next.volume = Math.round(data.volume_level * 100);
        }
        if (data?.is_volume_muted !== undefined) {
          next.muted = data.is_volume_muted;
        }
        if (data?.position !== undefined) {
          next.position = data.position;
        }
        if (data?.duration !== undefined) {
          next.duration = data.duration;
        }
      }

      return next;
    });
  }, []);

  // JSON-RPC helper: send command and optionally wait for response
  const sendJsonRpc = useCallback((command: string, args: Record<string, unknown>, expectResult: boolean = true): Promise<unknown> => {
    return new Promise((resolve, reject) => {
      const ws = jsonrpcWsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error('JSON-RPC WebSocket not connected'));
        return;
      }

      const id = `counter${msgId + 1}`;
      setMsgId(prev => prev + 1);

      const payload = JSON.stringify({
        message_id: id,
        command,
        args,
      });

      if (!expectResult) {
        ws.send(payload);
        resolve(undefined);
        return;
      }

      // Wait for response with timeout
      const timeout = setTimeout(() => {
        reject(new Error(`JSON-RPC command ${command} timed out`));
      }, 10000);

      const onMessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data as string);
          if (data?.message_id === id && data?.type === 'RESULT') {
            clearTimeout(timeout);
            ws.removeEventListener('message', onMessage);
            resolve(data.result);
          }
          if (data?.message_id === id && data?.error) {
            clearTimeout(timeout);
            ws.removeEventListener('message', onMessage);
            reject(new Error(`JSON-RPC error: ${JSON.stringify(data.error)}`));
          }
        } catch {
          // Not our response
        }
      };

      ws.addEventListener('message', onMessage);
      ws.send(payload);
    });
  }, [msgId]);

  const initPlayer = useCallback(async () => {
    console.log('[MAWebPlayer] initPlayer called, playerRef.current:', !!playerRef.current);
    if (playerRef.current) return;

    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get('token')?.trim();
    const storedToken = storageGetSync(API_KEY_STORAGE);
    const apiToken = urlToken || storedToken || '';
    console.log('[MAWebPlayer] initPlayer token check: urlToken=', !!urlToken, 'storedToken=', !!storedToken, 'apiToken=', !!apiToken);
    const playerId = getPlayerIdRef();
    if (!apiToken) {
      console.error('[MAWebPlayer] initPlayer FAILED: no API token available');
      setStateLocal(s => ({ ...s, error: 'No API token available — use ?token=xxx in URL or login to UI' }));
      return;
    }

    const markSendspinConnected = () => {
      updateConnectionState('CONNECTED', 0);
      reconnectAttemptsRef.current = 0;
    };

    let sendspinWs: WebSocket;
    let jsonrpcWs: WebSocket;
    let player: SendspinPlayer;

    try {
      console.log('[MAWebPlayer] [1/6] Starting initialization...');
      const baseUrl = window.location.origin;

      // 1. Create native WebSocket for Sendspin
      console.log('[MAWebPlayer] [1/6] Creating Sendspin WebSocket...');
      const sendspinWsUrl = getSendspinUrl(baseUrl, apiToken);
      sendspinWs = new WebSocket(sendspinWsUrl);

      sendspinWs.onerror = (event) => {
        console.error('[MAWebPlayer] Sendspin WebSocket error event:', event);
        setError('Sendspin WebSocket error', event);
      };

      let sendspinCloseHandler: ((event: CloseEvent) => void) | null = null;

      // 2. Create audio element
      console.log('[MAWebPlayer] [2/6] Creating audio element...');
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      document.body.appendChild(audio);
      audioRef.current = audio;

      audio.onerror = () => {
        console.error('[MAWebPlayer] Audio element error');
        setError('Audio element error — check browser audio permissions');
      };

      // 3. Create SendspinPlayer
      console.log('[MAWebPlayer] [3/6] Creating SendspinPlayer with playerId:', playerId);
      player = new SendspinPlayer({
        audioElement: audio,
        playerId,
        webSocket: sendspinWs,
        onStateChange: (newState) => {
          try {
            console.log('[MAWebPlayer] onStateChange:', newState);
            setStateLocal(s => ({
              ...s,
              isPlaying: newState.isPlaying,
              volume: Math.round(newState.volume),
              muted: newState.muted,
              playerState: newState.isPlaying ? 'playing' : (newState.isConnected ? 'idle' : null),
              mediaTitle: (newState as unknown as { serverState?: { metadata?: { title?: string } } }).serverState?.metadata?.title ?? s.mediaTitle,
              mediaArtist: (newState as unknown as { serverState?: { metadata?: { artist?: string } } }).serverState?.metadata?.artist ?? s.mediaArtist,
              mediaImage: extractMaImage((newState as unknown as { serverState?: { metadata?: { image?: unknown } } }).serverState?.metadata?.image) ?? s.mediaImage,
              duration: (newState as unknown as { serverState?: { metadata?: { duration?: number } } }).serverState?.metadata?.duration ?? s.duration,
              position: (newState as unknown as { serverState?: { position?: number } }).serverState?.position ?? s.position,
              error: newState.playerState === 'error' ? 'Playback error - attempting recovery' : s.error,
            }));
          } catch (callbackErr) {
            const msg = callbackErr instanceof Error ? callbackErr.message : String(callbackErr);
            console.error('[MAWebPlayer] onStateChange callback error:', msg, callbackErr);
          }
        },
      });

      playerRef.current = player;

      // 4. Connect SendspinPlayer
      console.log('[MAWebPlayer] [4/6] Calling player.connect()...');
      try {
        await player.connect();
      } catch (connectErr) {
        const msg = connectErr instanceof Error ? connectErr.message : String(connectErr);
        console.error('[MAWebPlayer] player.connect() failed:', msg);
        setError(`Player connection failed: ${msg}`);
        player.disconnect('connect_failed');
        playerRef.current = null;
        return;
      }
      sendspinWs = (player as unknown as { core: { wsManager: { ws: WebSocket } } }).core.wsManager.ws;
      sendspinWsRef.current = sendspinWs;
      markSendspinConnected();
      console.log('[MAWebPlayer] player.connect() completed, volume:', player.volume, 'muted:', player.muted);

      // Set up onclose handler AFTER adopt() to avoid being overwritten
      sendspinCloseHandler = (event) => {
        console.error('[MAWebPlayer] Sendspin WebSocket closed:', event.code, event.reason);
        if (sendspinWsRef.current === sendspinWs) {
          setError(`Sendspin WebSocket closed (code: ${event.code}, reason: ${event.reason})`);
        }
      };
      sendspinWs.onclose = sendspinCloseHandler;

      // 5. Create plain JSON-RPC WebSocket
      console.log('[MAWebPlayer] [5/6] Creating JSON-RPC WebSocket...');
      const jsonrpcUrl = getJsonRpcUrl(apiToken);
      jsonrpcWs = new WebSocket(jsonrpcUrl);
      jsonrpcWsRef.current = jsonrpcWs;

      jsonrpcWs.onopen = () => {
        console.log('[MAWebPlayer] JSON-RPC WebSocket connected');
        updateConnectionState('CONNECTED', 0);
      };

      jsonrpcWs.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string);
          if (data && typeof data === 'object' && 'event' in data) {
            handleMaEvent(data.event as string, data.data as Record<string, unknown>);
          }
        } catch (parseErr) {
          console.warn('[MAWebPlayer] Failed to parse JSON-RPC message:', event.data, parseErr);
        }
      };

      jsonrpcWs.onclose = (event) => {
        console.log('[MAWebPlayer] JSON-RPC WebSocket closed:', event.code, event.reason);
        if (jsonrpcWsRef.current === jsonrpcWs && event.code !== 1000) {
          setError(`JSON-RPC WebSocket closed (code: ${event.code}, reason: ${event.reason})`);
        }
      };

      jsonrpcWs.onerror = () => {
        console.error('[MAWebPlayer] JSON-RPC WebSocket error event');
        setError('JSON-RPC WebSocket error');
      };

      // 6. Wait for both connections
      console.log('[MAWebPlayer] [6/6] Waiting for both connections...');
      const connTimeout = 15000;
      const start = Date.now();
      while (Date.now() - start < connTimeout) {
        const sReady = sendspinWs && sendspinWs.readyState === WebSocket.OPEN;
        const jReady = jsonrpcWs && jsonrpcWs.readyState === WebSocket.OPEN;
        if (sReady && jReady) {
          console.log('[MAWebPlayer] Both WebSocket connections established');
          break;
        }
        await new Promise(r => setTimeout(r, 500));
      }

      // Verify both connections are still open after timeout window
      const sFinal = sendspinWs.readyState === WebSocket.OPEN;
      const jFinal = jsonrpcWs.readyState === WebSocket.OPEN;
      if (!sFinal || !jFinal) {
        const missing: string[] = [];
        if (!sFinal) missing.push('sendspin');
        if (!jFinal) missing.push('jsonrpc');
        throw new Error(`Connection timeout: ${missing.join(' and ')} WebSocket(s) failed to connect within ${connTimeout}ms`);
      }

      setStateLocal(s => ({
        ...s,
        isConnected: true,
        volume: player.volume,
        muted: player.muted,
        error: null,
      }));

      console.log('[MAWebPlayer] Player initialized and connected successfully');

    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError('Init failed: ' + msg, err);
      // Cleanup failed connections
      try { playerRef.current?.disconnect('init-error'); } catch { /* ignore cleanup errors */ }
      try { sendspinWs?.close(1000, 'init-error'); } catch { /* ignore cleanup errors */ }
      try { jsonrpcWs?.close(1000, 'init-error'); } catch { /* ignore cleanup errors */ }
      playerRef.current = null;
      sendspinWsRef.current = null;
      jsonrpcWsRef.current = null;
    }
  }, [getPlayerIdRef, handleMaEvent, updateConnectionState, setError]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      try {
        console.log('[MAWebPlayer] Cleanup: disconnecting player...');
        playerRef.current?.disconnect('shutdown');
        playerRef.current = null;
      } catch (err) {
        console.error('[MAWebPlayer] Cleanup error (disconnect):', err);
      }
      try {
        sendspinWsRef.current?.close(1000, 'shutdown');
        sendspinWsRef.current = null;
      } catch (err) {
        console.error('[MAWebPlayer] Cleanup error (sendspinWs):', err);
      }
      try {
        jsonrpcWsRef.current?.close(1000, 'shutdown');
        jsonrpcWsRef.current = null;
      } catch (err) {
        console.error('[MAWebPlayer] Cleanup error (jsonrpcWs):', err);
      }
      try {
        audioRef.current?.remove();
        audioRef.current = null;
      } catch (err) {
        console.error('[MAWebPlayer] Cleanup error (audio):', err);
      }
    };
  }, []);

  /* ── JSON-RPC Control Methods ──────────────────────────────────────── */

  // Queue a URI in MA using the same signature as the MA frontend, then start playback.
  // Converts raw ABS book UUIDs to MA-compatible library:// URIs.
  const playMedia = useCallback(async (mediaUri: string, player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id available for play_media');
      return;
    }

    let resolvedUri = mediaUri;
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    if (mediaUri.startsWith('audiobookshelf://')) {
      const idClean = mediaUri.replace('audiobookshelf://', '').replace('abs-', '').replace('ma-', '');
      resolvedUri = `library://audiobookshelf/book/${idClean}`;
    } else if (uuidRegex.test(mediaUri)) {
      resolvedUri = `library://audiobookshelf/book/${mediaUri}`;
    } else if (mediaUri.startsWith('abs-') || mediaUri.startsWith('ma-')) {
      const idClean = mediaUri.replace('abs-', '').replace('ma-', '');
      if (uuidRegex.test(idClean)) {
        resolvedUri = `library://audiobookshelf/book/${idClean}`;
      }
    }

    try {
      console.log('[MAWebPlayer] play_media:', resolvedUri, 'player:', pid);
      await sendJsonRpc('player_queues/play_media', {
        queue_id: pid,
        media: resolvedUri,
        option: 'replace',
        radio_mode: false,
      }, false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] play_media failed:', msg);
      setError('play_media failed: ' + msg);
    }
  }, [sendJsonRpc, setError]);

  // Resume local browser playback.
  const cmdPlay = useCallback(async (player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_play');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_play:', pid);
      playerRef.current?.sendCommand('play');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] cmd_play failed:', msg);
      setError('cmd_play failed: ' + msg);
    }
  }, [setError]);

  // Pause local browser playback.
  const cmdPause = useCallback(async (player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_pause');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_pause:', pid);
      playerRef.current?.sendCommand('pause');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] cmd_pause failed:', msg);
      setError('cmd_pause failed: ' + msg);
    }
  }, [setError]);

  // Send players/cmd_seek to seek to a position
  const cmdSeek = useCallback(async (position: number, player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_seek');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_seek:', position, 'player:', pid);
      await sendJsonRpc('players/cmd_seek', { player_id: pid, position });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] cmd_seek failed:', msg);
      setError('cmd_seek failed: ' + msg);
    }
  }, [sendJsonRpc, setError]);

  // Skip to the next track. Use MA's canonical players/cmd_next (the same call
  // MA's own web player uses — see music-assistant/frontend SendspinPlayer.vue)
  // with the browser/sendspin player id. This advances the MA queue for the web
  // player so the next item is streamed over sendspin. A raw sendspin controller
  // `client/command` "next" does NOT reliably advance the MA queue, which left
  // Next/Previous stuck on the same track.
  const cmdNext = useCallback(async (_player_id?: string) => {
    const pid = _player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_next');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_next (players/cmd_next):', pid);
      await sendJsonRpc('players/cmd_next', { player_id: pid }, false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] cmd_next failed:', msg);
      setError('cmd_next failed: ' + msg);
    }
  }, [sendJsonRpc, setError]);

  // Skip to the previous track via MA players/cmd_previous (see cmdNext).
  const cmdPrevious = useCallback(async (_player_id?: string) => {
    const pid = _player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_previous');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_previous (players/cmd_previous):', pid);
      await sendJsonRpc('players/cmd_previous', { player_id: pid }, false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] cmd_previous failed:', msg);
      setError('cmd_previous failed: ' + msg);
    }
  }, [sendJsonRpc, setError]);

  /* ── Convenience Methods ───────────────────────────────────────────── */

  // Play: ensure connected, queue media if provided, then resume local playback.
  const play = useCallback(async (mediaUri?: string) => {
    console.log('[MAWebPlayer] play called, player exists:', !!playerRef.current);
    try {
      if (!playerRef.current) {
        console.log('[MAWebPlayer] Initializing player before play...');
        await initPlayer();
      }
      if (mediaUri) {
        console.log('[MAWebPlayer] Sending play_media with URI:', mediaUri);
        await playMedia(mediaUri);
      } else {
        console.log('[MAWebPlayer] Resuming local playback...');
        await cmdPlay();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] play failed:', msg);
      setError('play failed: ' + msg);
    }
  }, [initPlayer, playMedia, cmdPlay, setError]);

  // Pause: send cmd_pause
  const pause = useCallback(async () => {
    console.log('[MAWebPlayer] pause called');
    try {
      await cmdPause();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] pause failed:', msg);
      setError('pause failed: ' + msg);
    }
  }, [cmdPause, setError]);

  // Seek: send cmd_seek via JSON-RPC
  const seek = useCallback(async (position: number) => {
    console.log('[MAWebPlayer] seek called:', position);
    try {
      await cmdSeek(position);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] seek failed:', msg);
      setError('seek failed: ' + msg);
    }
  }, [cmdSeek, setError]);

  // Next track: send players/cmd_next
  const next = useCallback(async () => {
    console.log('[MAWebPlayer] next called');
    try {
      await cmdNext();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] next failed:', msg);
      setError('next failed: ' + msg);
    }
  }, [cmdNext, setError]);

  // Previous track: send players/cmd_previous
  const previous = useCallback(async () => {
    console.log('[MAWebPlayer] previous called');
    try {
      await cmdPrevious();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] previous failed:', msg);
      setError('previous failed: ' + msg);
    }
  }, [cmdPrevious, setError]);

  // Send an arbitrary MA JSON-RPC command. Used to control a *physical* MA
  // player selected from the device picker (e.g. players/play_media with a
  // player_id). Reuses the browser's ma-jsonrpc WebSocket, which reaches MA
  // directly and sidesteps the gateway's server-side hostname loop.
  const maCommand = useCallback(
    (command: string, args: Record<string, unknown> = {}): Promise<unknown> => sendJsonRpc(command, args),
    [sendJsonRpc],
  );

  // List Music Assistant players so the UI device picker can offer them.
  const listMaPlayers = useCallback(async (): Promise<
    Array<{ player_id: string; name: string; available: boolean; state: string; powered: boolean }>
  > => {
    try {
      const raw = (await sendJsonRpc('players/all', {})) as { result?: unknown } | unknown;
      const players = (
        raw && typeof raw === 'object' && 'result' in (raw as Record<string, unknown>)
          ? (raw as { result: unknown }).result
          : raw
      ) as unknown[];
      if (!Array.isArray(players)) return [];
      return players
        .map((p) => {
          const pl = p as Record<string, unknown>;
          return {
            player_id: String(pl.player_id ?? ''),
            name: String(pl.name ?? pl.display_name ?? pl.player_id ?? 'Unknown Player'),
            available: Boolean(pl.available ?? true),
            state: String(pl.state ?? 'idle'),
            powered: Boolean(pl.powered ?? true),
          };
        })
        .filter((p) => p.player_id);
    } catch (err) {
      console.error('[MAWebPlayer] listMaPlayers failed:', err);
      return [];
    }
  }, [sendJsonRpc]);

  const setVolume = useCallback((volume: number) => {
    console.log('[MAWebPlayer] setVolume called:', volume);
    try {
      if (audioRef.current) {
        audioRef.current.volume = Math.max(0, Math.min(1, volume / 100));
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] setVolume failed:', msg);
      setError('setVolume failed: ' + msg);
    }
  }, [setError]);

  const setMuted = useCallback((muted: boolean) => {
    console.log('[MAWebPlayer] setMuted called:', muted);
    try {
      if (audioRef.current) {
        audioRef.current.muted = muted;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] setMuted failed:', msg);
      setError('setMuted failed: ' + msg);
    }
  }, [setError]);

  const connect = useCallback(async () => {
    console.log('[MAWebPlayer] connect called, playerRef.current:', !!playerRef.current, 'isConnected:', state.isConnected);
    try {
      if (!playerRef.current) {
        console.log('[MAWebPlayer] connect: player not initialized, calling initPlayer...');
        await initPlayer();
        console.log('[MAWebPlayer] connect: initPlayer completed, playerRef.current:', !!playerRef.current);
      } else {
        console.log('[MAWebPlayer] connect: player already initialized');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] connect failed:', msg);
      setError('connect failed: ' + msg);
    }
  }, [initPlayer, setError, state.isConnected]);

  const disconnect = useCallback(() => {
    playerRef.current?.disconnect('shutdown');
    playerRef.current = null;
    sendspinWsRef.current?.close();
    sendspinWsRef.current = null;
    jsonrpcWsRef.current?.close();
    jsonrpcWsRef.current = null;
    setStateLocal(s => ({ ...s, isConnected: false, isPlaying: false }));
  }, []);

  // Reconnect: reinitialize the player when connection fails or drops
  const reconnect = useCallback(async () => {
    console.log('[MAWebPlayer] Reconnecting...');
    try {
      // Clean up existing connections
      playerRef.current?.disconnect('shutdown');
      playerRef.current = null;
      sendspinWsRef.current?.close(1000, 'reconnect');
      sendspinWsRef.current = null;
      jsonrpcWsRef.current?.close(1000, 'reconnect');
      jsonrpcWsRef.current = null;
      audioRef.current?.remove();
      audioRef.current = null;
    } catch (cleanupErr) {
      const msg = cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr);
      console.error('[MAWebPlayer] Reconnect cleanup failed:', msg);
    }

    setStateLocal(s => ({
      ...s,
      isConnected: false,
      isPlaying: false,
      error: null,
      reconnectAttempts: 0,
    }));

    // Wait a moment before reconnecting
    await new Promise(resolve => setTimeout(resolve, 1000));
    await initPlayer();
  }, [initPlayer]);

  return {
    ...state,
    connect,
    play,
    pause,
    seek,
    setVolume,
    setMuted,
    disconnect,
    reconnect,
    playMedia,
    cmdPlay,
    cmdPause,
    cmdSeek,
    cmdNext,
    cmdPrevious,
    next,
    previous,
    maCommand,
    listMaPlayers,
    audioRef,
    jsonrpcWs: jsonrpcWsRef,
    sendspinWs: sendspinWsRef,
  };
}

export { STORAGE_KEY };
