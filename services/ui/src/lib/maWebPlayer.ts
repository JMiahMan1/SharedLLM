/* ── MA Web Player hook ─────────────────────────────────────────────────────
 *
 * Mirrors the Music Assistant frontend web player architecture:
 * 1. Sendspin protocol for audio transport (via /api/sendspin proxy)
 * 2. JSON-RPC control API for play/pause/seek commands (via /api/ma-jsonrpc proxy)
 *
 * The Sendspin connection registers the browser as a playback device.
 * The JSON-RPC connection queues media in MA; Sendspin commands drive browser playback.
 * Audio plays directly through the browser's <audio> element via sendspin-js.
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { getPlayerId, savePlayerId, setState } from './webPlayer';
import type { SendspinPlayer, PlayerState } from '@sendspin/sendspin-js';
import { WebSocketManager, ConnectionState } from './wsManager';

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

/* ── Sendspin WebSocket proxy ──────────────────────────────────────────────
 *
 * Connects to gateway's /api/sendspin, which authenticates with MA and
 * proxies the raw sendspin protocol bidirectionally.
 * WebSocketManager handles auto-reconnection with exponential backoff.
 *
 * NOTE: Heartbeat is disabled (set to 0) because the sendspin protocol has
 * its own keepalive mechanism inside SendspinPlayer. The WebSocketManager's
 * application-level { type: 'ping' } heartbeat would be forwarded to MA as
 * raw data, which MA doesn't understand and may reject.
 */
function createSendspinProxy(baseUrl: string, apiToken: string): WebSocketManager {
  const wsUrl = new URL('/api/sendspin', baseUrl);
  wsUrl.searchParams.set('token', apiToken);
  return new WebSocketManager(wsUrl.toString(), {
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    // Disable application-level heartbeat — sendspin protocol has its own keepalive
    heartbeatIntervalMs: 0,
    heartbeatTimeoutMs: 0,
  });
}

/* ── JSON-RPC WebSocket proxy ──────────────────────────────────────────────
 *
 * Connects to gateway's /api/ma-jsonrpc, which authenticates with MA and
 * proxies JSON-RPC messages bidirectionally.
 *
 * Browser sends: {"message_id": "counter1", "command": "player_queues/play_media", "args": {...}}
 * MA responds:   {"type": "RESULT", "message_id": "counter1", "result": {...}}
 * MA events:     {"event": "queue_updated", "data": {...}}
 */
function createJsonRpcProxy(
  baseUrl: string,
  apiToken: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): WebSocketManager {
  const wsUrl = new URL('/api/ma-jsonrpc', baseUrl);
  wsUrl.searchParams.set('token', apiToken);
  return new WebSocketManager(wsUrl.toString(), {
    onMessage: (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data as string);
        if (data && typeof data === 'object' && 'event' in data) {
          onEvent(data.event, data.data);
        }
      } catch {
        // Non-JSON or unparseable — ignore
      }
    },
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    // MA's websocket API does not accept ad-hoc ping messages on the command channel.
    heartbeatIntervalMs: 0,
    heartbeatTimeoutMs: 0,
  });
}

/* ── Hook ────────────────────────────────────────────────────────────────── */

export function useMAWebPlayer(onStateChange?: (state: MAWebPlayerState) => void) {
  const playerRef = useRef<SendspinPlayer | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const jsonrpcWsRef = useRef<WebSocketManager | null>(null);
  const sendspinWsRef = useRef<WebSocketManager | null>(null);
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
    position: 0,
    duration: 0,
    connectionState: 'DISCONNECTED',
    reconnectAttempts: 0,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
  });

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
      onStateChange?.({ ...next });
      return next;
    });
  }, [onStateChange]);

  // Handle MA JSON-RPC events (queue_updated, player_updated)
  const handleMaEvent = useCallback((eventType: string, data: Record<string, unknown>) => {
    console.log('[MAWebPlayer] MA event:', eventType, data);

    setStateLocal(prev => {
      const next = { ...prev };

      if (eventType === 'queue_updated') {
        const current = data?.current_item;
        if (current) {
          next.mediaTitle = current.name || null;
          next.mediaArtist = current.artist || null;
          next.duration = current.duration || 0;
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
        // Player state updates from MA (volume, muted, position, etc.)
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
    if (playerRef.current) return;

    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get('token')?.trim();
    const apiToken = urlToken || storageGetSync(API_KEY_STORAGE) || '';
    const playerId = getPlayerIdRef();
    if (!apiToken) {
      setStateLocal(s => ({ ...s, error: 'No API token available — use ?token=xxx in URL or login to UI' }));
      return;
    }

    try {
      const baseUrl = window.location.origin;

      // 1. Create Sendspin proxy WebSocket (audio transport)
      console.log('[MAWebPlayer] Creating Sendspin proxy...');
      const sendspinWs = createSendspinProxy(baseUrl, apiToken);
      sendspinWsRef.current = sendspinWs;

      // Listen for connection state changes
      sendspinWs.addEventListener('open', () => {
        updateConnectionState('CONNECTED', 0);
        reconnectAttemptsRef.current = 0;
      });

      sendspinWs.addEventListener('close', (event) => {
        const closeEv = event as CloseEvent;
        console.log('[MAWebPlayer] Sendspin proxy closed:', closeEv.code, closeEv.reason);
        // WebSocketManager will auto-reconnect, don't set error yet
      });

      sendspinWs.addEventListener('error', () => {
        console.error('[MAWebPlayer] Sendspin proxy error');
      });

      // 2. Create JSON-RPC proxy WebSocket (control API)
      console.log('[MAWebPlayer] Creating JSON-RPC proxy...');
      const jsonrpcWs = createJsonRpcProxy(baseUrl, apiToken, handleMaEvent);
      jsonrpcWsRef.current = jsonrpcWs;

      // Listen for JSON-RPC connection state changes
      jsonrpcWs.addEventListener('open', () => {
        console.log('[MAWebPlayer] JSON-RPC proxy connected');
      });

      jsonrpcWs.addEventListener('close', (event) => {
        const closeEv = event as CloseEvent;
        console.log('[MAWebPlayer] JSON-RPC proxy closed:', closeEv.code, closeEv.reason);
      });

      jsonrpcWs.addEventListener('error', () => {
        console.error('[MAWebPlayer] JSON-RPC proxy error');
      });

      // Wait for at least one connection to establish before proceeding
      // Use a promise that resolves when either WebSocket connects
      const connectionTimeout = 15000;
      let resolved = false;

      const waitForConnection = new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          if (!resolved) reject(new Error('Connection timeout after 15s'));
        }, connectionTimeout);

        const checkConnection = () => {
          if (sendspinWs.readyState === WebSocket.OPEN || jsonrpcWs.readyState === WebSocket.OPEN) {
            resolved = true;
            clearTimeout(timeout);
            console.log('[MAWebPlayer] At least one WebSocket connection established');
            resolve();
          }
        };

        // Check immediately and then poll
        checkConnection();
        const pollInterval = setInterval(() => {
          checkConnection();
          if (resolved) clearInterval(pollInterval);
        }, 500);
      });

      await waitForConnection;

      // 3. Create audio element and attach to DOM
      console.log('[MAWebPlayer] Creating audio element...');
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      document.body.appendChild(audio);
      audioRef.current = audio;

      // 4. Create SendspinPlayer
      console.log('[MAWebPlayer] Creating SendspinPlayer with playerId:', playerId);
      const player = new SendspinPlayer({
        audioElement: audio,
        playerId,
        webSocket: sendspinWs as unknown as WebSocket,
        codecs: ['opus', 'flac'],
        onStateChange: (newState) => {
          console.log('[MAWebPlayer] onStateChange:', newState);
          setStateLocal(s => {
            const updated = {
              ...s,
              isPlaying: newState.isPlaying,
              playerState: newState.playerState,
              volume: newState.volume,
              muted: newState.muted,
              error: newState.playerState === 'error' ? 'Playback error - attempting recovery' : null,
            };
            onStateChange?.({ ...updated });
            return updated;
          });
        },
      });

      playerRef.current = player;

      // 5. Connect SendspinPlayer (registers with MA, starts audio transport)
      console.log('[MAWebPlayer] Calling player.connect()...');
      await player.connect();
      console.log('[MAWebPlayer] player.connect() completed, volume:', player.volume, 'muted:', player.muted);
      setStateLocal(s => ({
        ...s,
        isConnected: true,
        volume: player.volume,
        muted: player.muted,
        error: null,
      }));

      console.log('[MAWebPlayer] Player initialized and connected');

    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] Init failed:', msg, err);
      setStateLocal(s => ({ ...s, error: msg }));
    }
  }, [getPlayerIdRef, handleMaEvent, onStateChange, updateConnectionState]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      playerRef.current?.disconnect('shutdown');
      playerRef.current = null;
      sendspinWsRef.current?.close();
      sendspinWsRef.current = null;
      jsonrpcWsRef.current?.close();
      jsonrpcWsRef.current = null;
      audioRef.current = null;
    };
  }, []);

  /* ── JSON-RPC Control Methods ──────────────────────────────────────── */

  // Queue a URI in MA and then start local browser playback.
  const playMedia = useCallback(async (mediaUri: string, player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id available for play_media');
      return;
    }
    try {
      console.log('[MAWebPlayer] play_media:', mediaUri, 'player:', pid);
      await sendJsonRpc('player_queues/play_media', {
        queue_id: pid,
        media: mediaUri,
        custom_data: { source_change: false },
      }, false);
      playerRef.current?.sendCommand('play');
    } catch (err) {
      console.error('[MAWebPlayer] play_media failed:', err);
    }
  }, [sendJsonRpc]);

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
      console.error('[MAWebPlayer] cmd_play failed:', err);
    }
  }, []);

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
      console.error('[MAWebPlayer] cmd_pause failed:', err);
    }
  }, []);

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
      console.error('[MAWebPlayer] cmd_seek failed:', err);
    }
  }, [sendJsonRpc]);

  /* ── Convenience Methods ───────────────────────────────────────────── */

  // Play: ensure connected, send play_media (if URI provided), then local play.
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
      console.error('[MAWebPlayer] play failed:', err);
    }
  }, [initPlayer, playMedia, cmdPlay]);

  // Pause: send cmd_pause
  const pause = useCallback(async () => {
    console.log('[MAWebPlayer] pause called');
    try {
      await cmdPause();
    } catch (err) {
      console.error('[MAWebPlayer] pause failed:', err);
    }
  }, [cmdPause]);

  // Seek: send cmd_seek via JSON-RPC
  const seek = useCallback(async (position: number) => {
    console.log('[MAWebPlayer] seek called:', position);
    try {
      await cmdSeek(position);
    } catch (err) {
      console.error('[MAWebPlayer] seek failed:', err);
    }
  }, [cmdSeek]);

  const setVolume = useCallback((volume: number) => {
    console.log('[MAWebPlayer] setVolume called:', volume);
    try {
      if (audioRef.current) {
        audioRef.current.volume = Math.max(0, Math.min(1, volume / 100));
      }
    } catch (err) {
      console.error('[MAWebPlayer] setVolume failed:', err);
    }
  }, []);

  const setMuted = useCallback((muted: boolean) => {
    console.log('[MAWebPlayer] setMuted called:', muted);
    try {
      if (audioRef.current) {
        audioRef.current.muted = muted;
      }
    } catch (err) {
      console.error('[MAWebPlayer] setMuted failed:', err);
    }
  }, []);

  const connect = useCallback(async () => {
    console.log('[MAWebPlayer] connect called');
    try {
      if (!playerRef.current) {
        await initPlayer();
      }
    } catch (err) {
      console.error('[MAWebPlayer] connect failed:', err);
    }
  }, [initPlayer]);

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
    // Clean up existing connections
    playerRef.current?.disconnect('shutdown');
    playerRef.current = null;
    sendspinWsRef.current?.close();
    sendspinWsRef.current = null;
    jsonrpcWsRef.current?.close();
    jsonrpcWsRef.current = null;
    audioRef.current = null;

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
    audioRef,
    jsonrpcWs: jsonrpcWsRef,
    sendspinWs: sendspinWsRef,
  };
}

export { STORAGE_KEY };
