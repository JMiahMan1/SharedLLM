/* ── MA Web Player hook ─────────────────────────────────────────────────────
 *
 * Mirrors the Music Assistant frontend web player architecture:
 * 1. Sendspin protocol for audio transport (via /api/sendspin proxy)
 * 2. JSON-RPC control API for play/pause/seek commands (via /api/ma-jsonrpc proxy)
 *
 * The Sendspin connection registers the browser as a playback device.
 * The JSON-RPC connection sends play_media/cmd_play/cmd_pause/cmd_seek commands.
 * Audio plays directly through the browser's <audio> element via sendspin-js.
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { getPlayerId, savePlayerId, setState } from './webPlayer';
import type { SendspinPlayer, PlayerState } from '@sendspin/sendspin-js';

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
}

const API_KEY_STORAGE = 'jarvis_api_key' as const;

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
 */
function createSendspinProxy(baseUrl: string, apiToken: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const wsUrl = new URL('/api/sendspin', baseUrl);
    wsUrl.searchParams.set('token', apiToken);

    const ws = new WebSocket(wsUrl.toString());
    let ready = false;

    ws.onopen = () => {
      // SendspinPlayer will send its own auth message via the WebSocket.
      // We just need to signal that the connection is open.
      ready = true;
      resolve(ws);
    };

    ws.onerror = () => {
      console.error('[MAWebPlayer] Sendspin proxy WebSocket error');
      if (!ready) reject(new Error('Sendspin proxy WebSocket error'));
    };

    ws.onclose = (event) => {
      if (!ready) reject(new Error(`Sendspin proxy WebSocket closed: ${event.code} ${event.reason}`));
    };

    setTimeout(() => {
      if (!ready) {
        ws.close();
        reject(new Error('Sendspin proxy WebSocket timeout'));
      }
    }, 10000);
  });
}

/* ── JSON-RPC WebSocket proxy ──────────────────────────────────────────────
 *
 * Connects to gateway's /api/ma-jsonrpc, which authenticates with MA and
 * proxies JSON-RPC messages bidirectionally.
 *
 * Browser sends: {"message_id": "counter1", "command": "players/play_media", "args": {...}}
 * MA responds:   {"type": "RESULT", "message_id": "counter1", "result": {...}}
 * MA events:     {"event": "queue_updated", "data": {...}}
 */
function createJsonRpcProxy(baseUrl: string, apiToken: string, onEvent: (event: string, data: Record<string, unknown>) => void): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const wsUrl = new URL('/api/ma-jsonrpc', baseUrl);
    wsUrl.searchParams.set('token', apiToken);

    const ws = new WebSocket(wsUrl.toString());

    ws.onopen = () => {
      resolve(ws);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string);
        if (data && typeof data === 'object' && 'event' in data) {
          // MA event: { "event": "queue_updated", "data": {...} }
          onEvent(data.event, data.data);
        }
      } catch {
        // Non-JSON or unparseable — ignore
      }
    };

    ws.onerror = () => {
      console.error('[MAWebPlayer] JSON-RPC proxy WebSocket error');
      reject(new Error('JSON-RPC proxy WebSocket error'));
    };

    ws.onclose = (event) => {
      console.error('[MAWebPlayer] JSON-RPC proxy WebSocket closed:', event.code, event.reason);
      reject(new Error(`JSON-RPC proxy WebSocket closed: ${event.code} ${event.reason}`));
    };

    setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        reject(new Error('JSON-RPC proxy WebSocket timeout'));
      }
    }, 10000);
  });
}

/* ── Hook ────────────────────────────────────────────────────────────────── */

export function useMAWebPlayer(onStateChange?: (state: MAWebPlayerState) => void) {
  const playerRef = useRef<SendspinPlayer | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const jsonrpcWsRef = useRef<WebSocket | null>(null);
  const playerIdRef = useRef<string>('');
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

    onStateChange?.({ ...state, mediaTitle: null, mediaArtist: null, position: 0, duration: 0 });
  }, [onStateChange, state]);

  // JSON-RPC helper: send command and optionally wait for response
  const sendJsonRpc = useCallback((command: string, args: Record<string, unknown>): Promise<unknown> => {
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

    const apiToken = storageGetSync(API_KEY_STORAGE) ?? '';
    const playerId = getPlayerIdRef();
    if (!apiToken) {
      setStateLocal(s => ({ ...s, error: 'No API token available' }));
      return;
    }

    try {
      const baseUrl = window.location.origin;

      // 1. Create Sendspin proxy WebSocket (audio transport)
      const sendspinWs = await createSendspinProxy(baseUrl, apiToken);

      // 2. Create JSON-RPC proxy WebSocket (control API)
      const jsonrpcWs = await createJsonRpcProxy(baseUrl, apiToken, handleMaEvent);
      jsonrpcWsRef.current = jsonrpcWs;

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
        webSocket: sendspinWs,
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
              error: null,
            };
            onStateChange?.({ ...updated });
            return updated;
          });
        },
      });

      playerRef.current = player;

      // 5. Connect SendspinPlayer (registers with MA, starts audio transport)
      console.log('[MAWebPlayer] Calling player.connect()...');
      setStateLocal(s => ({ ...s, isConnected: true, error: null }));
      await player.connect();
      console.log('[MAWebPlayer] player.connect() completed, volume:', player.volume, 'muted:', player.muted);
      setStateLocal(s => ({ ...s, volume: player.volume, muted: player.muted }));

      console.log('[MAWebPlayer] Player initialized and connected');

    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[MAWebPlayer] Init failed:', msg, err);
      setStateLocal(s => ({ ...s, error: msg }));
    }
  }, [getPlayerIdRef, handleMaEvent, onStateChange]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      playerRef.current?.disconnect('shutdown');
      playerRef.current = null;
      jsonrpcWsRef.current?.close();
      jsonrpcWsRef.current = null;
      audioRef.current = null;
    };
  }, []);

  /* ── JSON-RPC Control Methods ──────────────────────────────────────── */

  // Send players/play_media to queue and start playing a specific URI
  const playMedia = useCallback(async (mediaUri: string, player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id available for play_media');
      return;
    }
    try {
      console.log('[MAWebPlayer] play_media:', mediaUri, 'player:', pid);
      const result = await sendJsonRpc('players/play_media', {
        player_id: pid,
        media: mediaUri,
        play_handle: null,
        enqueue: 'play',
      });
      console.log('[MAWebPlayer] play_media result:', result);
    } catch (err) {
      console.error('[MAWebPlayer] play_media failed:', err);
    }
  }, [sendJsonRpc]);

  // Send players/cmd_play to resume playback
  const cmdPlay = useCallback(async (player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_play');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_play:', pid);
      await sendJsonRpc('players/cmd_play', { player_id: pid });
    } catch (err) {
      console.error('[MAWebPlayer] cmd_play failed:', err);
    }
  }, [sendJsonRpc]);

  // Send players/cmd_pause to pause playback
  const cmdPause = useCallback(async (player_id?: string) => {
    const pid = player_id || playerIdRef.current;
    if (!pid) {
      console.error('[MAWebPlayer] No player_id for cmd_pause');
      return;
    }
    try {
      console.log('[MAWebPlayer] cmd_pause:', pid);
      await sendJsonRpc('players/cmd_pause', { player_id: pid });
    } catch (err) {
      console.error('[MAWebPlayer] cmd_pause failed:', err);
    }
  }, [sendJsonRpc]);

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

  // Play: ensure connected, send play_media (if URI provided), then cmd_play
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
      }
      console.log('[MAWebPlayer] Sending cmd_play...');
      await cmdPlay();
    } catch (err) {
      console.error('[MAWebPlayer] play failed:', err);
    }
  }, [initPlayer, playMedia, cmdPlay]);

  // Pause: send cmd_pause
  const pause = useCallback(async () => {
    console.log('[MAWebPlayer] pause called');
    try {
      await cmdPause();
      playerRef.current?.sendCommand('pause', {});
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
      playerRef.current?.setVolume(Math.round(volume));
      // Also sync via JSON-RPC if connected
      if (playerIdRef.current && jsonrpcWsRef.current?.readyState === WebSocket.OPEN) {
        sendJsonRpc('players/set_volume', {
          player_id: playerIdRef.current,
          volume_level: volume / 100,
        }).catch((err) => console.error('[MAWebPlayer] setVolume JSON-RPC failed:', err));
      }
    } catch (err) {
      console.error('[MAWebPlayer] setVolume failed:', err);
    }
  }, [sendJsonRpc]);

  const setMuted = useCallback((muted: boolean) => {
    console.log('[MAWebPlayer] setMuted called:', muted);
    try {
      playerRef.current?.setMuted(muted);
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
    jsonrpcWsRef.current?.close();
    jsonrpcWsRef.current = null;
    setStateLocal(s => ({ ...s, isConnected: false, isPlaying: false }));
  }, []);

  return {
    ...state,
    connect,
    play,
    pause,
    seek,
    setVolume,
    setMuted,
    disconnect,
    playMedia,
    cmdPlay,
    cmdPause,
    cmdSeek,
    audioRef,
    jsonrpcWs: jsonrpcWsRef,
  };
}

export { STORAGE_KEY };
