/**
 * MA Web Player hook
 *
 * The app's own Music Assistant player, mirroring the MA frontend's web player:
 * 1. Sendspin protocol for the audio (via the gateway's /api/sendspin proxy)
 * 2. JSON-RPC control API for play/pause/seek (via /api/ma-jsonrpc)
 *
 * Both are plain WebSockets (SendspinPlayer needs a real one). Audio plays
 * through an <audio> element driven by sendspin-js.
 *
 * Mounted once for the whole app by context/MediaPlayerContext.tsx. When either
 * socket drops unexpectedly (Wi-Fi hand-off, the phone sleeping, a gateway
 * restart) the player tears itself down and reconnects with backoff, so a
 * network blip does not leave a dead player behind until the app is reloaded.
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { SendspinPlayer } from '@sendspin/sendspin-js';
import type { ConnectionState } from './wsManager';
import { getServerOrigin, getWsProtocolFor } from './serverUrl';
import { storageGetSync } from './storage';
import { parseMaPlayerList, type MaPlayerListEntry } from './deviceLabels';
import { extractMaImage } from './maImage';
import { proxiedImage } from './mediaImage';

/**
 * UI-level playback state. Deliberately distinct from Sendspin's own
 * `PlayerState` ("synchronized" | "error"), which describes the transport.
 */
type MAPlaybackState = 'playing' | 'idle';

export interface MAWebPlayerState {
  isConnected: boolean;
  isPlaying: boolean;
  volume: number;
  muted: boolean;
  playerState: MAPlaybackState | null;
  error: string | null;
  mediaUri: string | null;
  mediaTitle: string | null;
  mediaArtist: string | null;
  mediaImage: string | null;
  mediaFavorite?: boolean | null;
  position: number;
  duration: number;
  /** Audiobook/podcast playback speed (1 = normal). */
  playbackRate: number;
  connectionState: ConnectionState;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
}

const API_KEY_STORAGE = 'jarvis_api_key' as const;
/** The web player's Sendspin client id, kept so MA sees the same player across reloads. */
const PLAYER_ID_STORAGE = 'sendspin_webplayer_id';
const MAX_RECONNECT_ATTEMPTS = 10;
const CONNECT_TIMEOUT_MS = 15_000;
const RPC_TIMEOUT_MS = 10_000;
/** How long a fresh connection waits for MA to list this player as available. */
const PLAYER_READY_TIMEOUT_MS = 8_000;
const PLAYER_READY_POLL_MS = 300;

function storedPlayerId(): string {
  try {
    return localStorage.getItem(PLAYER_ID_STORAGE) || '';
  } catch {
    return '';
  }
}

function ensurePlayerId(): string {
  let id = storedPlayerId();
  if (!id) {
    const arr = new Uint32Array(10);
    crypto.getRandomValues(arr);
    id = arr.join('');
    try {
      localStorage.setItem(PLAYER_ID_STORAGE, id);
    } catch {
      /* private mode: a fresh id per session still works */
    }
  }
  return id;
}

/** Backoff before reconnect attempt `n` (1-based): 1 s, 2 s, 4 s … capped at 30 s. */
export function reconnectDelayMs(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
}

/* The gateway WebSocket for `path`. On the Capacitor app the bundled page is
   served from localhost, so the host comes from the configured server URL
   (serverUrl.ts), never window.location. */
function gatewaySocketUrl(path: string, apiToken: string): string {
  const origin = getServerOrigin();
  const host = new URL(origin).host;
  return `${getWsProtocolFor(origin)}://${host}${path}?token=${encodeURIComponent(apiToken)}`;
}

/* Resolve a (possibly relative) MA image reference to an absolute URL so the
   OS MediaSession artwork can load it. Goes through the gateway image proxy:
   MA paths are relative to the MA host, not the app, and the proxy carries the
   credentials. */
function buildMediaArtwork(img?: string | null): MediaImage[] {
  const src = proxiedImage(img);
  if (!src) return [];
  return [{ src, sizes: '512x512', type: 'image/png' }];
}

/** A short human name, or '' for anything else (an object, a JSON blob):
 *  the player name is shown in Music Assistant and Home Assistant. */
function plainName(value: unknown): string {
  if (typeof value !== 'string') return '';
  const name = value.trim();
  return name && name.length <= 40 && !/[{}"]/.test(name) ? name : '';
}

function clientName(): string {
  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  let userName = '';
  try {
    let parsed: unknown = storageGetSync('jarvis_user');
    // The profile is stored as JSON; an older build stored it encoded twice.
    for (let i = 0; i < 2 && typeof parsed === 'string'; i += 1) parsed = JSON.parse(parsed);
    if (parsed && typeof parsed === 'object') {
      const user = parsed as { display_name?: unknown; username?: unknown };
      userName = plainName(user.display_name) || plainName(user.username);
    }
  } catch {
    // Not JSON
  }
  if (!userName) userName = plainName(storageGetSync('username'));
  return `${userName ? userName + "'s " : ''}Web Player (${isMobile ? 'Mobile' : 'Desktop'})`;
}

const sameId = (a: string, b: string) => a === b || a.includes(b) || b.includes(a);

/* Hook */

export function useMAWebPlayer() {
  const playerRef = useRef<SendspinPlayer | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const jsonrpcWsRef = useRef<WebSocket | null>(null);
  const sendspinWsRef = useRef<WebSocket | null>(null);
  const playerIdRef = useRef<string>(storedPlayerId());
  const [playerId, setPlayerId] = useState<string>(() => storedPlayerId());
  const msgIdRef = useRef(0);
  // JSON-RPC calls still waiting for MA, so a closed connection can fail them
  // at once instead of leaving each to its timeout (BUG-48).
  const pendingRpcRef = useRef(new Map<string, (error: Error) => void>());
  /** The in-flight connection attempt, shared by concurrent callers. */
  const initPromiseRef = useRef<Promise<boolean> | null>(null);
  /** True once someone asked for the player; false after an explicit disconnect. */
  const wantConnectedRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const [state, setStateLocal] = useState<MAWebPlayerState>({
    isConnected: false,
    isPlaying: false,
    volume: 70,
    muted: false,
    playerState: null,
    error: null,
    mediaUri: null,
    mediaTitle: null,
    mediaArtist: null,
    mediaImage: null,
    mediaFavorite: null,
    position: 0,
    duration: 0,
    playbackRate: 1,
    connectionState: 'DISCONNECTED',
    reconnectAttempts: 0,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
  });
  const volumeRef = useRef({ volume: state.volume, muted: state.muted });
  useEffect(() => {
    volumeRef.current = { volume: state.volume, muted: state.muted };
  }, [state.volume, state.muted]);
  const playbackRateRef = useRef(state.playbackRate);
  useEffect(() => {
    playbackRateRef.current = state.playbackRate;
    if (audioRef.current) audioRef.current.playbackRate = state.playbackRate;
  }, [state.playbackRate]);

  /** Browsers only start audio from a user gesture: resume the element and
   *  Sendspin's AudioContext while we are inside one. */
  const unlockAudio = useCallback(() => {
    try {
      if (audioRef.current?.paused) void audioRef.current.play().catch(() => {});
      const sched = (playerRef.current as unknown as {
        scheduler?: { resumeAudioContext?: () => Promise<void>; audioContext?: AudioContext };
      } | null)?.scheduler;
      if (sched?.resumeAudioContext) void sched.resumeAudioContext();
      else if (sched?.audioContext?.state === 'suspended') void sched.audioContext.resume();
    } catch {
      /* nothing to unlock yet */
    }
  }, []);

  useEffect(() => {
    window.addEventListener('touchstart', unlockAudio, { once: true, passive: true });
    window.addEventListener('click', unlockAudio, { once: true, passive: true });
    return () => {
      window.removeEventListener('touchstart', unlockAudio);
      window.removeEventListener('click', unlockAudio);
    };
  }, [unlockAudio]);

  // Advance the position once a second while playing: MA only pushes it on
  // player_updated / queue_time_updated, which made the scrubber jump.
  useEffect(() => {
    if (!state.isPlaying || !state.duration) return;
    const id = setInterval(() => {
      setStateLocal(s => {
        if (!s.isPlaying || !s.duration) return s;
        const tp = playerRef.current?.trackProgress;
        if (tp && typeof tp.positionMs === 'number' && tp.positionMs > 0) {
          return { ...s, position: Math.min(s.duration, Math.round(tp.positionMs / 1000)) };
        }
        const nextPos = s.position + 1;
        return nextPos >= s.duration ? s : { ...s, position: nextPos };
      });
    }, 1000);
    return () => clearInterval(id);
  }, [state.isPlaying, state.duration]);

  const setError = useCallback((msg: string, err?: unknown) => {
    console.error('[MAWebPlayer]', msg, err ?? '');
    setStateLocal(s => ({ ...s, error: msg }));
  }, []);

  // MA JSON-RPC events for this player (queue_updated, player_updated).
  const handleMaEvent = useCallback((eventType: string, data: Record<string, unknown>) => {
    const myPid = playerIdRef.current;
    if (myPid) {
      const eventId =
        eventType === 'player_updated'
          ? String(data?.player_id ?? data?.id ?? '')
          : eventType === 'queue_updated'
            ? String(data?.queue_id ?? data?.player_id ?? '')
            : '';
      if (eventId && !sameId(eventId, myPid)) return;
    }

    setStateLocal(prev => {
      const next = { ...prev };

      if (eventType === 'queue_updated') {
        // current_item is only a fallback for title/artist/cover: once the
        // authoritative player_updated current_media has filled them, a
        // playlist/source name here must not override the real track.
        const current = data?.current_item as Record<string, unknown> | undefined;
        if (current) {
          const mediaItem = current.media_item as Record<string, unknown> | undefined;
          const qUri = (current.uri || mediaItem?.uri || (current.streamdetails as Record<string, unknown> | undefined)?.uri) as string | undefined;
          if (qUri && !next.mediaUri) next.mediaUri = qUri;
          if (!next.mediaTitle) next.mediaTitle = (current.name as string) || null;
          if (!next.mediaArtist) next.mediaArtist = (current.artist as string) || null;
          if (!next.mediaImage) next.mediaImage = extractMaImage(current.image) || next.mediaImage;
          if (current.duration) next.duration = current.duration as number;
          const qFav = (current.favorite ?? mediaItem?.favorite) as boolean | undefined;
          if (qFav !== undefined && next.mediaFavorite === undefined) next.mediaFavorite = qFav;
        }
        const queueState = data?.state;
        if (queueState === 'playing') next.isPlaying = true;
        else if (queueState === 'paused' || queueState === 'idle') next.isPlaying = false;
      }

      if (eventType === 'player_updated') {
        // current_media is MA's authoritative "now playing" and changes on
        // every track change -- it drives the title/artist/cover.
        const media = data?.current_media as Record<string, unknown> | undefined;
        if (media) {
          const mUri = (media.uri || (media.media_item as Record<string, unknown> | undefined)?.uri) as string | undefined;
          const isTrackChange = (media.title && media.title !== prev.mediaTitle) || (mUri && mUri !== prev.mediaUri);
          if (mUri) next.mediaUri = mUri;
          if (media.title) next.mediaTitle = media.title as string;
          if (media.artist) next.mediaArtist = media.artist as string;
          const img = extractMaImage(media.image_url || media.image);
          if (img) next.mediaImage = img;
          if (media.duration) next.duration = media.duration as number;
          if (isTrackChange) {
            next.position = typeof media.elapsed_time === 'number' ? media.elapsed_time : 0;
          } else if (typeof media.elapsed_time === 'number' && media.elapsed_time > 0 && !next.isPlaying) {
            next.position = media.elapsed_time;
          }
          const mFav = (media.favorite ?? (media.media_item as Record<string, unknown> | undefined)?.favorite) as boolean | undefined;
          if (mFav !== undefined) next.mediaFavorite = mFav;
        }
        if (data?.volume_level !== undefined && data?.volume_level !== null) {
          // MA reports 0..1, some providers 0..100: normalize to a 0..100 UI scale.
          const vl = data.volume_level as number;
          next.volume = vl <= 1 ? Math.round(vl * 100) : Math.round(vl);
        }
        if (data?.is_volume_muted !== undefined && data?.is_volume_muted !== null) {
          next.muted = Boolean(data.is_volume_muted);
        }
        if (typeof data?.position === 'number' && data.position > 0 && !next.isPlaying) {
          next.position = data.position;
        }
        if (typeof data?.duration === 'number' && data.duration > 0) {
          next.duration = data.duration;
        }
      }

      return next;
    });
  }, []);

  // Send a JSON-RPC command; with expectResult, wait for MA's answer.
  const sendJsonRpc = useCallback((command: string, args: Record<string, unknown>, expectResult: boolean = true): Promise<unknown> => {
    return new Promise((resolve, reject) => {
      const ws = jsonrpcWsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error('Music Assistant is not connected'));
        return;
      }
      msgIdRef.current += 1;
      const id = `counter${msgIdRef.current}`;
      const payload = JSON.stringify({ message_id: id, command, args });

      if (!expectResult) {
        ws.send(payload);
        resolve(undefined);
        return;
      }

      const pending = pendingRpcRef.current;
      const settle = () => {
        clearTimeout(timeout);
        ws.removeEventListener('message', onMessage);
        pending.delete(id);
      };
      const timeout = setTimeout(() => {
        settle();
        reject(new Error(`Music Assistant did not answer ${command}`));
      }, RPC_TIMEOUT_MS);
      pending.set(id, (error) => {
        settle();
        reject(error);
      });

      function onMessage(event: MessageEvent) {
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(event.data as string);
        } catch {
          return;
        }
        if (data?.message_id !== id) return;
        if (data.result !== undefined) {
          settle();
          resolve(data.result);
        } else if (data.error !== undefined || data.error_code !== undefined) {
          settle();
          const detail = data.details || (typeof data.error === 'string' ? data.error : JSON.stringify(data.error || `Error code ${data.error_code}`));
          reject(new Error(`Music Assistant: ${detail}`));
        }
      }

      ws.addEventListener('message', onMessage);
      ws.send(payload);
    });
  }, []);

  /** Close both sockets and the audio element; safe to call repeatedly. */
  const teardown = useCallback((reason: string) => {
    const player = playerRef.current;
    const sendspinWs = sendspinWsRef.current;
    const jsonrpcWs = jsonrpcWsRef.current;
    playerRef.current = null;
    sendspinWsRef.current = null;
    jsonrpcWsRef.current = null;
    try { player?.disconnect('shutdown'); } catch { /* already gone */ }
    try { sendspinWs?.close(1000, reason); } catch { /* already gone */ }
    try { jsonrpcWs?.close(1000, reason); } catch { /* already gone */ }
    try { audioRef.current?.remove(); } catch { /* already gone */ }
    audioRef.current = null;
    // Nothing will answer on a closed socket: fail what is waiting now.
    const waiting = [...pendingRpcRef.current.values()];
    pendingRpcRef.current.clear();
    for (const fail of waiting) fail(new Error(`Music Assistant connection closed (${reason})`));
  }, []);

  // `initPlayer` and the reconnect scheduler refer to each other.
  const scheduleReconnectRef = useRef<() => void>(() => {});

  /** A socket that belongs to the live connection closed without being asked to. */
  const handleConnectionLost = useCallback((which: string, ws: WebSocket, event?: CloseEvent) => {
    if (sendspinWsRef.current !== ws && jsonrpcWsRef.current !== ws) return; // an old socket
    console.warn(`[MAWebPlayer] ${which} connection lost`, event?.code ?? '', event?.reason ?? '');
    teardown('connection-lost');
    setStateLocal(s => ({ ...s, isConnected: false, isPlaying: false, connectionState: 'RECONNECTING' }));
    scheduleReconnectRef.current();
  }, [teardown]);

  /** Open both sockets. Resolves true once the player is usable. */
  const openConnection = useCallback(async (): Promise<boolean> => {
    if (playerRef.current && jsonrpcWsRef.current?.readyState === WebSocket.OPEN) return true;
    teardown('reinit');

    const urlToken = new URLSearchParams(window.location.search).get('token')?.trim();
    const apiToken = urlToken || storageGetSync(API_KEY_STORAGE) || '';
    if (!apiToken) {
      setStateLocal(s => ({ ...s, connectionState: 'FAILED', error: 'Sign in to use the web player.' }));
      return false;
    }

    const pid = ensurePlayerId();
    playerIdRef.current = pid;
    setPlayerId(pid);
    setStateLocal(s => ({ ...s, connectionState: reconnectAttemptsRef.current > 0 ? 'RECONNECTING' : 'CONNECTING' }));

    let sendspinWs: WebSocket = new WebSocket(gatewaySocketUrl('/api/sendspin', apiToken));
    const audio = new Audio();
    audio.crossOrigin = 'anonymous';
    audio.playbackRate = playbackRateRef.current;
    document.body.appendChild(audio);
    audioRef.current = audio;
    audio.onerror = () => setError('Audio element error. Check browser audio permissions');

    try {
      const player = new SendspinPlayer({
        audioElement: audio,
        playerId: pid,
        clientName: clientName(),
        webSocket: sendspinWs,
        onStateChange: (newState) => {
          const server = (newState as unknown as {
            serverState?: { metadata?: { title?: string; artist?: string; image?: unknown; duration?: number }; position?: number };
          }).serverState;
          setStateLocal(s => ({
            ...s,
            isPlaying: newState.isPlaying,
            volume: Math.round(newState.volume),
            muted: newState.muted,
            playerState: newState.isPlaying ? 'playing' : (newState.playerState === 'synchronized' ? 'idle' : null),
            mediaTitle: server?.metadata?.title ?? s.mediaTitle,
            mediaArtist: server?.metadata?.artist ?? s.mediaArtist,
            mediaImage: extractMaImage(server?.metadata?.image) ?? s.mediaImage,
            duration: server?.metadata?.duration ?? s.duration,
            position: server?.position ?? s.position,
            error: newState.playerState === 'error' ? 'Playback error - attempting recovery' : s.error,
          }));
        },
      });
      playerRef.current = player;
      await player.connect();
      // sendspin-js adopts the socket and may have replaced it.
      sendspinWs = (player as unknown as { core: { wsManager: { ws: WebSocket } } }).core.wsManager.ws;
      sendspinWsRef.current = sendspinWs;
      const { volume, muted } = volumeRef.current;
      try {
        player.setVolume(volume);
        player.setMuted(muted);
        audio.volume = Math.max(0, Math.min(1, volume / 100));
        audio.muted = muted;
      } catch {
        /* volume sync is best-effort */
      }
      const ss = sendspinWs;
      ss.onclose = (event) => handleConnectionLost('Sendspin', ss, event);

      const jsonrpcWs = new WebSocket(gatewaySocketUrl('/api/ma-jsonrpc', apiToken));
      jsonrpcWsRef.current = jsonrpcWs;
      jsonrpcWs.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string);
          if (data && typeof data === 'object' && 'event' in data) {
            handleMaEvent(data.event as string, data.data as Record<string, unknown>);
          }
        } catch {
          /* not JSON: ignore */
        }
      };
      jsonrpcWs.onclose = (event) => handleConnectionLost('Control', jsonrpcWs, event);

      await new Promise<void>((resolve, reject) => {
        if (jsonrpcWs.readyState === WebSocket.OPEN) { resolve(); return; }
        const timer = setTimeout(() => reject(new Error('Music Assistant did not answer in time')), CONNECT_TIMEOUT_MS);
        jsonrpcWs.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
        const fail = () => { clearTimeout(timer); reject(new Error('Could not reach Music Assistant')); };
        jsonrpcWs.addEventListener('error', fail, { once: true });
        jsonrpcWs.addEventListener('close', fail, { once: true });
      });
      if (sendspinWs.readyState !== WebSocket.OPEN) throw new Error('The audio stream closed while connecting');

      // MA registers a new Sendspin player a moment after the handshake; a
      // command sent before then fails with "The player is not available".
      const readyBy = Date.now() + PLAYER_READY_TIMEOUT_MS;
      while (Date.now() < readyBy) {
        const info = await sendJsonRpc('players/get', { player_id: pid }).catch(() => null) as { available?: boolean } | null;
        if (info?.available) break;
        await new Promise((r) => setTimeout(r, PLAYER_READY_POLL_MS));
      }

      reconnectAttemptsRef.current = 0;
      setStateLocal(s => ({
        ...s,
        isConnected: true,
        connectionState: 'CONNECTED',
        reconnectAttempts: 0,
        volume: player.volume,
        muted: player.muted,
        error: null,
      }));
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      teardown('init-error');
      setStateLocal(s => ({ ...s, isConnected: false }));
      console.warn('[MAWebPlayer] connect failed:', msg);
      throw new Error(msg, { cause: err });
    }
  }, [teardown, setError, handleMaEvent, handleConnectionLost, sendJsonRpc]);

  /** Connect once, sharing the attempt with anyone who asks meanwhile. */
  const initPlayer = useCallback((): Promise<boolean> => {
    wantConnectedRef.current = true;
    if (!initPromiseRef.current) {
      initPromiseRef.current = openConnection()
        .catch((err: unknown) => {
          scheduleReconnectRef.current();
          throw err;
        })
        .finally(() => {
          initPromiseRef.current = null;
        });
    }
    return initPromiseRef.current;
  }, [openConnection]);

  useEffect(() => {
    scheduleReconnectRef.current = () => {
      if (!wantConnectedRef.current || reconnectTimerRef.current !== null) return;
      const attempt = reconnectAttemptsRef.current + 1;
      if (attempt > MAX_RECONNECT_ATTEMPTS) {
        setStateLocal(s => ({
          ...s,
          connectionState: 'FAILED',
          error: 'Lost the connection to Music Assistant. Tap Reconnect to try again.',
        }));
        return;
      }
      reconnectAttemptsRef.current = attempt;
      setStateLocal(s => ({ ...s, connectionState: 'RECONNECTING', reconnectAttempts: attempt }));
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        void initPlayer().catch(() => { /* scheduled again by initPlayer */ });
      }, reconnectDelayMs(attempt));
    };
  }, [initPlayer]);

  // Coming back to the app (or back online) is the moment to retry at once
  // rather than waiting out a long backoff.
  useEffect(() => {
    const retryNow = () => {
      if (document.hidden || !wantConnectedRef.current || playerRef.current || initPromiseRef.current) return;
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      reconnectAttemptsRef.current = 0;
      void initPlayer().catch(() => {});
    };
    document.addEventListener('visibilitychange', retryNow);
    window.addEventListener('online', retryNow);
    return () => {
      document.removeEventListener('visibilitychange', retryNow);
      window.removeEventListener('online', retryNow);
    };
  }, [initPlayer]);

  // Cleanup on unmount
  useEffect(() => () => {
    wantConnectedRef.current = false;
    if (reconnectTimerRef.current !== null) clearTimeout(reconnectTimerRef.current);
    teardown('shutdown');
  }, [teardown]);

  /* Commands */

  /** Connects if needed; throws a readable error when MA cannot be reached. */
  const ensureConnected = useCallback(async () => {
    if (playerRef.current && jsonrpcWsRef.current?.readyState === WebSocket.OPEN) return;
    await initPlayer();
  }, [initPlayer]);

  /** Run a queue/player command on this web player, surfacing failures. */
  const runOwn = useCallback(async (label: string, command: string, args: Record<string, unknown>) => {
    const pid = playerIdRef.current;
    if (!pid) return;
    try {
      await sendJsonRpc(command, args, false);
    } catch (err) {
      setError(`${label} failed: ${err instanceof Error ? err.message : String(err)}`, err);
    }
  }, [sendJsonRpc, setError]);

  /** Play `mediaUri` (an MA URI -- resolve Audiobookshelf ids with
   *  api.resolveMALibraryUri first), or resume when no URI is given. */
  const play = useCallback(async (mediaUri?: string) => {
    unlockAudio();
    try {
      await ensureConnected();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg, err);
      throw err;
    }
    unlockAudio();
    const pid = playerIdRef.current;
    if (!mediaUri) {
      playerRef.current?.sendCommand('play', undefined);
      await runOwn('Play', 'players/cmd/play', { player_id: pid });
      return;
    }
    try {
      await sendJsonRpc('player_queues/play_media', { queue_id: pid, media: mediaUri, option: 'replace', radio_mode: false }, true);
      setStateLocal(s => ({ ...s, mediaUri, mediaFavorite: null, error: null }));
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err);
      const msg = /Cannot connect to host|Connect call failed/.test(raw)
        ? `Upstream media provider unreachable: ${raw}`
        : raw;
      setError(msg, err);
      throw new Error(msg, { cause: err });
    }
  }, [unlockAudio, ensureConnected, runOwn, sendJsonRpc, setError]);

  /** Put `mediaUri` into this player's queue: after the current item
   *  ('next') or at the end ('add'), without interrupting what plays. */
  const enqueue = useCallback(async (mediaUri: string, option: 'next' | 'add') => {
    await ensureConnected();
    await sendJsonRpc('player_queues/play_media', { queue_id: playerIdRef.current, media: mediaUri, option }, true);
  }, [ensureConnected, sendJsonRpc]);

  const pause = useCallback(async () => {
    playerRef.current?.sendCommand('pause', undefined);
    await runOwn('Pause', 'players/cmd/pause', { player_id: playerIdRef.current });
  }, [runOwn]);

  /** Stop and forget the current item, so nothing reads as "now playing". */
  const stop = useCallback(async () => {
    await runOwn('Stop', 'players/cmd/stop', { player_id: playerIdRef.current });
    setStateLocal(s => ({
      ...s, isPlaying: false, mediaUri: null, mediaTitle: null, mediaArtist: null,
      mediaImage: null, mediaFavorite: null, position: 0, duration: 0,
    }));
  }, [runOwn]);

  const seek = useCallback(async (position: number) => {
    const pos = Math.max(0, Math.round(position));
    const pid = playerIdRef.current;
    await runOwn('Seek', 'player_queues/seek', { queue_id: pid, player_id: pid, position: pos });
    setStateLocal(s => ({ ...s, position: pos }));
  }, [runOwn]);

  const next = useCallback(async () => {
    const pid = playerIdRef.current;
    await runOwn('Next track', 'player_queues/next', { queue_id: pid, player_id: pid });
  }, [runOwn]);

  const previous = useCallback(async () => {
    const pid = playerIdRef.current;
    await runOwn('Previous track', 'player_queues/previous', { queue_id: pid, player_id: pid });
  }, [runOwn]);

  // Send an MA JSON-RPC command for a *physical* MA player picked in the
  // device picker. Legacy command spellings are normalized to the ones the
  // gateway's allowlist accepts.
  const maCommand = useCallback(
    (command: string, args: Record<string, unknown> = {}): Promise<unknown> => {
      let cmd = command;
      const a = { ...args };
      const toQueue = () => {
        if (!a.queue_id && a.player_id) a.queue_id = a.player_id;
      };
      if (['players/cmd/next', 'players/cmd_next', 'player_queues/cmd_next'].includes(cmd)) {
        cmd = 'player_queues/next';
        toQueue();
      } else if (['players/cmd/previous', 'players/cmd_previous', 'player_queues/cmd_previous'].includes(cmd)) {
        cmd = 'player_queues/previous';
        toQueue();
      } else if (['players/cmd/seek', 'players/cmd_seek', 'player_queues/cmd_seek'].includes(cmd)) {
        cmd = 'player_queues/seek';
        toQueue();
        if (typeof a.position === 'number') a.position = Math.round(a.position);
      } else if (cmd.startsWith('players/cmd_')) {
        cmd = 'players/cmd/' + cmd.slice('players/cmd_'.length);
      } else if (cmd === 'players/play_media') {
        cmd = 'player_queues/play_media';
        toQueue();
        if (!a.option) a.option = 'replace';
      } else if (cmd.startsWith('player_queues/cmd_')) {
        cmd = 'player_queues/' + cmd.slice('player_queues/cmd_'.length);
      }
      return sendJsonRpc(cmd, a);
    },
    [sendJsonRpc],
  );

  // List Music Assistant players so the UI device picker can offer them.
  const listMaPlayers = useCallback(async (): Promise<MaPlayerListEntry[]> => {
    try {
      return parseMaPlayerList(await sendJsonRpc('players/all', {}));
    } catch (err) {
      console.warn('[MAWebPlayer] listMaPlayers failed:', err);
      return [];
    }
  }, [sendJsonRpc]);

  const setVolume = useCallback((volume: number) => {
    try {
      if ((playerRef.current as unknown as { core?: { isConnected?: boolean } } | null)?.core?.isConnected) {
        playerRef.current!.setVolume(volume);
      }
      if (audioRef.current) audioRef.current.volume = Math.max(0, Math.min(1, volume / 100));
      setStateLocal(s => (s.volume === volume ? s : { ...s, volume }));
    } catch (err) {
      setError('setVolume failed: ' + (err instanceof Error ? err.message : String(err)), err);
    }
  }, [setError]);

  const setMuted = useCallback((muted: boolean) => {
    try {
      if ((playerRef.current as unknown as { core?: { isConnected?: boolean } } | null)?.core?.isConnected) {
        playerRef.current!.setMuted(muted);
      }
      if (audioRef.current) audioRef.current.muted = muted;
      setStateLocal(s => (s.muted === muted ? s : { ...s, muted }));
    } catch (err) {
      setError('setMuted failed: ' + (err instanceof Error ? err.message : String(err)), err);
    }
  }, [setError]);

  /** Playback speed for books and podcasts; the audio element keeps it across tracks. */
  const setPlaybackRate = useCallback((rate: number) => {
    const next = Math.min(3, Math.max(0.5, rate));
    if (audioRef.current) audioRef.current.playbackRate = next;
    setStateLocal(s => (s.playbackRate === next ? s : { ...s, playbackRate: next }));
  }, []);

  /** Connect now (no-op when connected). Never throws; failures land in `error`. */
  const connect = useCallback(async () => {
    try {
      await ensureConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err), err);
    }
  }, [ensureConnected, setError]);

  const disconnect = useCallback(() => {
    wantConnectedRef.current = false;
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    teardown('shutdown');
    setStateLocal(s => ({ ...s, isConnected: false, isPlaying: false, connectionState: 'DISCONNECTED' }));
  }, [teardown]);

  /** Start over after the automatic retries gave up. */
  const reconnect = useCallback(async () => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptsRef.current = 0;
    teardown('reconnect');
    setStateLocal(s => ({ ...s, isConnected: false, isPlaying: false, error: null, reconnectAttempts: 0 }));
    await connect();
  }, [teardown, connect]);

  // Keep the OS MediaSession (lock screen / media keys) in sync.
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: state.mediaTitle || 'Jarvis OS',
        artist: state.mediaArtist || 'Music Assistant',
        album: 'Jarvis OS',
        artwork: buildMediaArtwork(state.mediaImage),
      });
      navigator.mediaSession.playbackState = state.isPlaying ? 'playing' : 'paused';
    } catch {
      /* MediaSession unsupported in this WebView */
    }
  }, [state.mediaTitle, state.mediaArtist, state.mediaImage, state.isPlaying]);

  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    const setHandler = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        /* action not supported */
      }
    };
    setHandler('play', () => { void play().catch(() => {}); });
    setHandler('pause', () => { void pause(); });
    setHandler('nexttrack', () => { void next(); });
    setHandler('previoustrack', () => { void previous(); });
    return () => {
      for (const action of ['play', 'pause', 'nexttrack', 'previoustrack'] as const) setHandler(action, null);
    };
  }, [play, pause, next, previous]);

  return {
    ...state,
    playerId,
    connect,
    reconnect,
    disconnect,
    play,
    enqueue,
    pause,
    stop,
    seek,
    next,
    previous,
    setVolume,
    setMuted,
    setPlaybackRate,
    maCommand,
    listMaPlayers,
  };
}
