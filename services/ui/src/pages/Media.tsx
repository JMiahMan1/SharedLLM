import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Library, List, Loader2, Music, Play, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import { useSharedMAWebPlayer } from '../context/MediaPlayerContext';
import { errorText } from '../lib/errorText';
import { formatDeviceLabel } from '../lib/deviceLabels';
import type { MaPlayerListEntry } from '../lib/deviceLabels';
import { listRemoteMaPlayers, sendMaRemoteCommand } from '../lib/maRemotePlayer';
import { proxiedImage } from '../lib/mediaImage';
import { DeviceSelector } from '../components/media/DeviceSelector';
import { NowPlayingCard, type NowPlayingView } from '../components/media/NowPlayingCard';
import { MediaExplorer } from '../components/media/MediaExplorer';
import type { DeviceOption, MediaStatus, PlayRequest } from '../components/media/types';

/* helpers */

const STATUS_POLL_MS = 3000;
/** After the user moves a volume slider, ignore polled volumes this long. */
const VOLUME_GRACE_MS = 4000;

// Normalize `last_played` (epoch s, epoch ms or ISO string) to epoch ms.
const normalizePlayedAt = (value: unknown): number | undefined => {
  if (value === undefined || value === null || value === '') return undefined;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isNaN(n)) return n <= 0 ? undefined : n > 1e12 ? n : n * 1000;
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? undefined : parsed;
};

const formatTimeAgo = (ts: number): string => {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const playerState = (s?: string): NowPlayingView['state'] =>
  s === 'playing' || s === 'buffering' ? 'playing' : s === 'paused' ? 'paused' : 'idle';

interface ShelfItem {
  key: string;
  req: PlayRequest;
  subtitle: string;
  kind: 'audiobook' | 'music' | 'playlist';
  progress?: number;
  lastPlayed?: number;
}

function ShelfCard({ item, busy, disabled, onPlay }: { item: ShelfItem; busy: boolean; disabled: boolean; onPlay: () => void }) {
  const tone =
    item.kind === 'audiobook' ? 'bg-amber-500/20 text-amber-400'
      : item.kind === 'playlist' ? 'bg-cyan-500/20 text-cyan-400'
        : 'bg-purple-500/20 text-purple-400';
  const Icon = item.kind === 'audiobook' ? BookOpen : item.kind === 'playlist' ? List : Music;
  return (
    <button
      type="button"
      onClick={onPlay}
      disabled={disabled}
      className="glass-panel rounded-xl p-3 min-h-11 flex items-center gap-3 text-left transition-colors hover:bg-white/10 disabled:opacity-60 group"
    >
      <span className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${tone}`}>
        {busy ? <Loader2 size={18} className="animate-spin" /> : <Icon size={18} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-white text-sm font-medium truncate">{item.req.title}</span>
        {item.subtitle ? <span className="block text-xs text-slate-400 truncate">{item.subtitle}</span> : null}
        {item.progress !== undefined && (
          <span className="flex items-center gap-2 mt-1.5">
            <span className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
              <span className="block h-full bg-amber-400 rounded-full" style={{ width: `${Math.round(item.progress * 100)}%` }} />
            </span>
            <span className="text-[11px] text-slate-500 shrink-0 tabular-nums">{Math.round(item.progress * 100)}%</span>
          </span>
        )}
        {item.lastPlayed !== undefined && <span className="block text-[11px] text-slate-500 mt-1">{formatTimeAgo(item.lastPlayed)}</span>}
      </span>
      <Play size={16} className="text-slate-500 shrink-0 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100" aria-hidden />
    </button>
  );
}

function ShelfMessage({ title, detail, onRetry }: { title: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className="glass-panel rounded-2xl p-6 text-center">
      <p className="text-sm text-slate-300">{title}</p>
      {detail ? <p className="text-xs text-slate-500 mt-1">{detail}</p> : null}
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 min-h-11 px-4 inline-flex items-center gap-2 text-sm text-cyan-300 hover:text-cyan-200">
          <RefreshCw size={14} />Try again
        </button>
      )}
    </div>
  );
}

/* page */

const Media = () => {
  const { trigger } = useHaptics();
  const maPlayer = useSharedMAWebPlayer();

  /** '' = this device's web player, `ma:<id>` = an MA-only player, else an HA entity. */
  const [searchParams] = useSearchParams();
  const requestedOutput = searchParams.get('output') ?? '';
  const [selectedTarget, setSelectedTarget] = useState(requestedOutput);
  const isWebPlayer = !selectedTarget;
  const selectedRef = useRef(selectedTarget);
  useEffect(() => {
    selectedRef.current = selectedTarget;
  }, [selectedTarget]);
  // Once the person picks a device the page stops second-guessing them; until
  // then, a speaker that is already playing is followed automatically so the
  // page never shows an empty card over live music. Arriving from the output
  // picker's "Control" action counts as a choice.
  const userPickedRef = useRef(Boolean(requestedOutput));

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [players, setPlayers] = useState<MediaStatus[]>([]);
  const [maPlayers, setMaPlayers] = useState<MaPlayerListEntry[]>([]);
  const [showExplorer, setShowExplorer] = useState(false);

  // Remote (HA / MA) volume, position and duration.
  const [volume, setVolume] = useState(50);
  const [muted, setMuted] = useState(false);
  const lastVolumeChangeRef = useRef(0);
  const volumeTimerRef = useRef<number | null>(null);
  const [remoteTime, setRemoteTime] = useState(0);

  /* device status */

  const refreshStatus = useCallback(async () => {
    try {
      const resp = await api.mediaStatus();
      if (resp.status !== 'SUCCESS' || !resp.detail) return;
      const detail = resp.detail as { active?: MediaStatus | null; all_players?: MediaStatus[] };
      const active = detail.active;
      // `active` carries the richest view of the playing device; prefer it.
      const all = (detail.all_players || [])
        .filter((p) => p.entity_id && p.entity_id !== 'web_player')
        .map((p) => (active && p.entity_id === active.entity_id ? { ...p, ...active } : p));
      setPlayers(all);
      if (
        active?.entity_id && active.entity_id !== 'web_player' && playerState(active.state) === 'playing' &&
        !userPickedRef.current && !selectedRef.current
      ) {
        userPickedRef.current = true; // adopt once
        setSelectedTarget(active.entity_id);
        if (!all.some((p) => p.entity_id === active.entity_id)) setPlayers([active, ...all]);
      }
    } catch {
      /* the next poll retries */
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- polling an external service
    void refreshStatus();
    const id = window.setInterval(() => {
      if (!document.hidden) void refreshStatus();
    }, STATUS_POLL_MS);
    return () => clearInterval(id);
  }, [refreshStatus]);

  // Music Assistant players that Home Assistant does not also expose. Use the
  // web player's control socket when it is already connected; otherwise ask the
  // gateway, so a remote speaker never requires this browser to become a player.
  const { isConnected: maConnected, listMaPlayers } = maPlayer;
  const isMaTarget = selectedTarget.startsWith('ma:');
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const list = maConnected ? await listMaPlayers() : await listRemoteMaPlayers();
      if (!cancelled) setMaPlayers((prev) => (JSON.stringify(prev) === JSON.stringify(list) ? prev : list));
    };
    void load();
    // A selected MA player's state only comes from here, so keep it fresh.
    const id = isMaTarget ? window.setInterval(() => { if (!document.hidden) void load(); }, STATUS_POLL_MS * 2) : null;
    return () => {
      cancelled = true;
      if (id !== null) clearInterval(id);
    };
  }, [maConnected, listMaPlayers, isMaTarget]);

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
    staleTime: 60_000,
  });

  const devices = useMemo<DeviceOption[]>(() => {
    const byId = new Map<string, DeviceOption>();
    const statusById = new Map(players.map((p) => [p.entity_id as string, p]));
    for (const e of entities) {
      const p = statusById.get(e.entity_id);
      const state = p?.state ?? e.state;
      byId.set(e.entity_id, {
        id: e.entity_id,
        name: formatDeviceLabel(e.friendly_name, p?.friendly_name, e.entity_id),
        detail: p?.icon_kind === 'tv' ? 'TV' : state === 'playing' ? 'Playing' : 'Speaker',
        online: state !== 'unavailable' && state !== 'unknown',
      });
    }
    for (const p of players) {
      if (byId.has(p.entity_id as string)) continue;
      byId.set(p.entity_id as string, {
        id: p.entity_id as string,
        name: formatDeviceLabel(p.friendly_name, p.entity_id),
        detail: p.icon_kind === 'tv' ? 'TV' : p.state === 'playing' ? 'Playing' : 'Speaker',
        online: p.state !== 'unavailable' && p.state !== 'unknown',
      });
    }
    // Most MA players are Home Assistant entities too; list those once. Our own
    // web player is the "This device" chip.
    const covered = new Set<string>([...byId.keys(), ...players.map((p) => p.ma_player_id || '').filter(Boolean)]);
    for (const p of maPlayers) {
      if (covered.has(p.player_id) || p.player_id === maPlayer.playerId) continue;
      byId.set(`ma:${p.player_id}`, { id: `ma:${p.player_id}`, name: p.name, detail: 'Music Assistant', online: p.available });
    }
    return [...byId.values()].sort((a, b) => Number(b.online) - Number(a.online));
  }, [entities, players, maPlayers, maPlayer.playerId]);

  const selectedDevice = devices.find((d) => d.id === selectedTarget);
  const remoteStatus = useMemo<MediaStatus | null>(() => {
    if (!selectedTarget) return null;
    if (isMaTarget) {
      const p = maPlayers.find((m) => `ma:${m.player_id}` === selectedTarget);
      return p ? { entity_id: selectedTarget, friendly_name: p.name, state: p.state } : null;
    }
    return players.find((p) => p.entity_id === selectedTarget) ?? null;
  }, [selectedTarget, isMaTarget, maPlayers, players]);

  // Follow the device's volume unless the person is moving the slider.
  useEffect(() => {
    if (!remoteStatus || Date.now() - lastVolumeChangeRef.current < VOLUME_GRACE_MS) return;
    /* eslint-disable react-hooks/set-state-in-effect -- mirror polled device state */
    if (typeof remoteStatus.volume_level === 'number') setVolume(Math.round(remoteStatus.volume_level * 100));
    if (typeof remoteStatus.is_volume_muted === 'boolean') setMuted(remoteStatus.is_volume_muted);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [remoteStatus]);

  // Position: take each poll, and tick between polls while playing. A poll
  // within 3 s of the ticked value is ignored so the clock does not stutter.
  const remotePos = remoteStatus?.position ?? 0;
  const remotePlaying = playerState(remoteStatus?.state) === 'playing';
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mirror polled position
    setRemoteTime((prev) => (remotePlaying && prev > 0 && Math.abs(remotePos - prev) < 3 ? prev : remotePos));
  }, [remotePos, remotePlaying, remoteStatus?.media_content_id]);
  const remoteDuration = remoteStatus?.duration ?? 0;
  useEffect(() => {
    if (!remotePlaying) return;
    const id = window.setInterval(() => {
      setRemoteTime((t) => (remoteDuration > 0 && t >= remoteDuration ? remoteDuration : t + 1));
    }, 1000);
    return () => clearInterval(id);
  }, [remotePlaying, remoteDuration]);

  /* favourites (Music Assistant items) */

  const activeUri = isWebPlayer ? maPlayer.mediaUri : remoteStatus?.media_content_id || null;
  const maItemUri = activeUri && activeUri.includes('://') ? activeUri : null;
  const { data: detail } = useQuery({
    queryKey: ['media-detail', maItemUri],
    queryFn: async () => {
      const data = await api.getMediaDetail(maItemUri!);
      return ((data && (data as { result?: unknown }).result) || data) as {
        uri?: string; name?: string; favorite?: boolean;
        artists?: Array<{ name: string }>; album?: { name: string }; podcast?: { name: string };
      };
    },
    enabled: !!maItemUri,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [favoriteOverride, setFavoriteOverride] = useState<{ uri: string; value: boolean } | null>(null);
  const isFavorite = favoriteOverride?.uri === maItemUri
    ? favoriteOverride.value
    : Boolean(detail?.favorite ?? (isWebPlayer ? maPlayer.mediaFavorite : false));

  const toggleFavorite = useCallback(async () => {
    if (!maItemUri) return;
    const target = !isFavorite;
    setFavoriteOverride({ uri: maItemUri, value: target });
    try {
      await api.setMediaFavorite(maItemUri, target);
    } catch (err) {
      setFavoriteOverride({ uri: maItemUri, value: !target });
      toast.error(errorText(err, 'Could not update favorites'));
    }
  }, [maItemUri, isFavorite]);

  /* playback */

  /** Start `req` on the selected device; `key` is the row showing a spinner. */
  const playItem = useCallback(async (req: PlayRequest, key: string) => {
    void trigger('heavy');
    setBusy(key);
    setError(null);
    const target = selectedRef.current;
    // Music Assistant only plays its own URIs; an Audiobookshelf id is mapped
    // server-side (a guessed URI leaves the player silent).
    const maUri = async () =>
      req.kind === 'abs' ? (await api.resolveMALibraryUri(req.id, req.title)).ma_uri : req.uri;
    try {
      if (!target) {
        await maPlayer.play(await maUri());
      } else if (target.startsWith('ma:')) {
        const pid = target.slice(3);
        const media = await maUri();
        await sendMaRemoteCommand('player_queues/play_media', { queue_id: pid, media: [media], option: 'replace' });
      } else {
        const resp = req.kind === 'abs'
          ? await api.playAudiobook({ book_id: req.id, entity_id: target, resume: true })
          : await api.mediaPlay({ entity_id: target, query: req.uri, media_type: 'music' });
        if (resp.status === 'FAILURE') throw new Error(resp.message || 'Playback failed');
        void refreshStatus();
      }
    } catch (err) {
      setError(errorText(err, 'Playback failed'));
    } finally {
      setBusy(null);
    }
  }, [maPlayer, refreshStatus, trigger]);

  /** Transport for the selected remote device. */
  const sendTransport = useCallback(async (command: 'play' | 'resume' | 'pause' | 'next' | 'previous' | 'stop') => {
    const target = selectedRef.current;
    if (!target) return;
    void trigger('light');
    setBusy(command);
    setError(null);
    try {
      if (target.startsWith('ma:')) {
        const pid = target.slice(3);
        const maCmd = {
          play: 'players/cmd/play', resume: 'players/cmd/play', pause: 'players/cmd/pause',
          next: 'player_queues/next', previous: 'player_queues/previous', stop: 'players/cmd/stop',
        }[command];
        await sendMaRemoteCommand(maCmd, maCmd.startsWith('player_queues/') ? { queue_id: pid } : { player_id: pid });
        // Keep the card in step until the next poll; the poll remains the
        // authority, so a command MA accepted but could not act on self-corrects.
        const nextState = command === 'pause' ? 'paused' : command === 'stop' ? 'idle' : command === 'play' || command === 'resume' ? 'playing' : null;
        if (nextState) {
          setMaPlayers((prev) => prev.map((p) => (p.player_id === pid ? { ...p, state: nextState } : p)));
        }
      } else {
        const resp = await api.mediaTransport({ entity_id: target, command });
        if (resp.status === 'FAILURE') throw new Error(resp.message || 'Command failed');
        await refreshStatus();
      }
    } catch (err) {
      setError(errorText(err, 'Command failed'));
    } finally {
      setBusy(null);
    }
  }, [refreshStatus, trigger]);

  const runWeb = useCallback(async (command: string, fn: () => Promise<unknown>) => {
    void trigger('light');
    setBusy(command);
    try {
      await fn();
    } catch (err) {
      setError(errorText(err, 'Command failed'));
    } finally {
      setBusy(null);
    }
  }, [trigger]);

  const handleRemoteVolume = useCallback((v: number) => {
    const target = selectedRef.current;
    if (!target) return;
    lastVolumeChangeRef.current = Date.now();
    setVolume(v);
    setMuted(false);
    if (volumeTimerRef.current) clearTimeout(volumeTimerRef.current);
    volumeTimerRef.current = window.setTimeout(() => {
      const send = target.startsWith('ma:')
        ? sendMaRemoteCommand('players/cmd/volume_set', { player_id: target.slice(3), volume_level: v })
        : api.mediaTransport({ entity_id: target, command: 'volume_set', volume_level: v / 100 });
      void Promise.resolve(send).catch(() => toast.error('Could not change the volume'));
    }, 200);
  }, []);

  const toggleRemoteMute = useCallback(() => {
    const target = selectedRef.current;
    if (!target) return;
    const next = !muted;
    lastVolumeChangeRef.current = Date.now();
    setMuted(next);
    const send = target.startsWith('ma:')
      ? sendMaRemoteCommand('players/cmd/volume_mute', { player_id: target.slice(3), muted: next })
      : api.mediaTransport({ entity_id: target, command: 'volume_mute', muted: next });
    void Promise.resolve(send).catch(() => {
      setMuted(!next);
      toast.error('Could not mute that player');
    });
  }, [muted]);

  const handleRemoteSeek = useCallback(async (seconds: number) => {
    const target = selectedRef.current;
    if (!target) return;
    setRemoteTime(seconds);
    try {
      if (target.startsWith('ma:')) {
        await sendMaRemoteCommand('player_queues/seek', { queue_id: target.slice(3), position: Math.round(seconds) });
      } else {
        const resp = await api.mediaTransport({ entity_id: target, command: 'seek', position: seconds });
        if (resp.status === 'FAILURE') throw new Error(resp.message);
      }
    } catch {
      // The optimistic position snaps back on the next poll; say why.
      toast.error('Could not seek on that player');
    }
  }, []);

  const selectDevice = useCallback((id: string) => {
    void trigger('light');
    userPickedRef.current = true;
    setSelectedTarget(id);
    setError(null);
    // Remember the choice server-side (voice "pause" and the dashboard follow
    // it). The web player records itself once it plays.
    if (id && !id.startsWith('ma:')) {
      void api.syncMediaState({ entity_id: id, state: 'idle' }).catch(() => {});
    }
  }, [trigger]);

  /* shelves */

  const playlistsQ = useQuery({ queryKey: ['ma-playlists'], queryFn: () => api.getMusicAssistantPlaylists(), retry: 1, staleTime: 60_000 });
  const recentQ = useQuery({ queryKey: ['ma-recent'], queryFn: () => api.getMusicAssistantRecent(), retry: 1, staleTime: 60_000 });
  const lastPlayedQ = useQuery({ queryKey: ['abs-last-played'], queryFn: () => api.getAudiobookshelfLastPlayed(), retry: 1, staleTime: 60_000 });

  const jumpBackIn = useMemo<ShelfItem[]>(() => {
    const items: ShelfItem[] = [];
    for (const book of lastPlayedQ.data?.books ?? []) {
      // The gateway reports progress as a percentage (older builds: a ratio).
      const raw = Number(book.progress ?? 0);
      const progress = raw > 1 ? raw / 100 : raw;
      if (book.is_complete || progress >= 1) continue; // finished: nothing to jump back into
      items.push({
        key: `abs-${book.id}`,
        req: { kind: 'abs', id: book.id, title: book.title },
        subtitle: book.author,
        kind: 'audiobook',
        progress,
        lastPlayed: normalizePlayedAt(book.last_played),
      });
    }
    for (const item of recentQ.data?.recent ?? []) {
      if (items.some((i) => i.req.title === item.name)) continue; // the same book from both services
      items.push({
        key: `ma-${item.uri}`,
        req: { kind: 'uri', uri: item.uri, title: item.name },
        subtitle: item.artist,
        kind: item.type === 'audiobook' ? 'audiobook' : 'music',
        lastPlayed: normalizePlayedAt(item.last_played),
      });
    }
    return items.sort((a, b) => (b.lastPlayed ?? 0) - (a.lastPlayed ?? 0)).slice(0, 6);
  }, [lastPlayedQ.data, recentQ.data]);

  const shelvesFailed = (recentQ.isError || !!lastPlayedQ.data?.notice || lastPlayedQ.isError) && jumpBackIn.length === 0;

  /* render */

  const remoteView: NowPlayingView = {
    title: detail?.name && maItemUri === remoteStatus?.media_content_id ? detail.name : remoteStatus?.media_title,
    artist: [remoteStatus?.media_artist, remoteStatus?.media_album].filter(Boolean).join(' · '),
    coverUrl: proxiedImage(remoteStatus?.entity_picture),
    state: playerState(remoteStatus?.state),
    position: remoteTime,
    duration: remoteDuration,
    trackKey: remoteStatus?.media_content_id || remoteStatus?.media_title || '',
  };
  const webView: NowPlayingView = {
    title: maPlayer.mediaTitle || undefined,
    artist: maPlayer.mediaArtist || undefined,
    coverUrl: proxiedImage(maPlayer.mediaImage),
    state: maPlayer.isPlaying ? 'playing' : maPlayer.mediaTitle ? 'paused' : 'idle',
    position: maPlayer.position,
    duration: maPlayer.duration,
    trackKey: maPlayer.mediaUri || maPlayer.mediaTitle || '',
  };
  const view = isWebPlayer ? webView : remoteView;

  const webFooter = (() => {
    switch (maPlayer.connectionState) {
      case 'CONNECTED':
        return <p className="text-sm text-cyan-300 text-center">Playing on this device</p>;
      case 'CONNECTING':
        return <p className="text-sm text-slate-400 text-center">Connecting to Music Assistant…</p>;
      case 'RECONNECTING':
        return (
          <p className="text-sm text-amber-300 text-center">
            Connection lost. Reconnecting{maPlayer.reconnectAttempts ? ` (try ${maPlayer.reconnectAttempts} of ${maPlayer.maxReconnectAttempts})` : ''}…
          </p>
        );
      case 'FAILED':
        return (
          <div className="flex flex-wrap items-center justify-center gap-2 text-sm text-red-300">
            <span>{maPlayer.error || 'Could not connect to Music Assistant.'}</span>
            <button type="button" onClick={() => void maPlayer.reconnect()} className="min-h-11 px-4 rounded-xl bg-white/10 text-white hover:bg-white/15">
              Reconnect
            </button>
          </div>
        );
      default:
        return <p className="text-sm text-slate-400 text-center">Plays through this device’s speakers or headphones</p>;
    }
  })();

  return (
    <div className="space-y-5 max-w-4xl mx-auto pb-24">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      {error && (
        <div role="alert" className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-200 text-sm flex items-center justify-between gap-3">
          <span className="min-w-0 break-words">{error}</span>
          <button type="button" onClick={() => setError(null)} className="min-h-11 px-3 underline text-xs shrink-0">Dismiss</button>
        </div>
      )}

      <DeviceSelector
        selectedTarget={selectedTarget}
        devices={devices}
        onSelect={selectDevice}
        webPlayerPlaying={maPlayer.isPlaying}
      />

      <NowPlayingCard
        view={view}
        volume={isWebPlayer ? maPlayer.volume : volume}
        muted={isWebPlayer ? maPlayer.muted : muted}
        busy={busy && ['play', 'resume', 'pause', 'next', 'previous', 'stop'].includes(busy) ? busy : null}
        isFavorite={isFavorite}
        idleHint={isWebPlayer ? 'Pick something below to play it here' : `Pick something below to play on ${selectedDevice?.name || 'this speaker'}`}
        footer={isWebPlayer ? webFooter : (
          <p className="text-sm text-purple-300 text-center truncate">
            {selectedDevice && !selectedDevice.online ? `${selectedDevice.name} is offline` : `Playing through ${selectedDevice?.name || remoteStatus?.friendly_name || 'the selected speaker'}`}
          </p>
        )}
        onPrevious={isWebPlayer ? () => void runWeb('previous', maPlayer.previous) : () => void sendTransport('previous')}
        onNext={isWebPlayer ? () => void runWeb('next', maPlayer.next) : () => void sendTransport('next')}
        onTogglePlay={
          isWebPlayer
            ? () => void runWeb(maPlayer.isPlaying ? 'pause' : 'play', () => (maPlayer.isPlaying ? maPlayer.pause() : maPlayer.play()))
            : () => void sendTransport(remoteView.state === 'playing' ? 'pause' : 'resume')
        }
        onStop={view.state !== 'idle' ? (isWebPlayer ? () => void runWeb('stop', maPlayer.stop) : () => void sendTransport('stop')) : undefined}
        onVolumeChange={isWebPlayer ? maPlayer.setVolume : handleRemoteVolume}
        onMuteToggle={isWebPlayer ? () => maPlayer.setMuted(!maPlayer.muted) : toggleRemoteMute}
        onFavoriteToggle={maItemUri ? () => void toggleFavorite() : undefined}
        onSeek={view.duration > 0 ? (isWebPlayer ? (s) => void maPlayer.seek(s) : (s) => void handleRemoteSeek(s)) : undefined}
      />

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Jump back in</h2>
        {(recentQ.isLoading || lastPlayedQ.isLoading) && jumpBackIn.length === 0 ? (
          <div className="flex justify-center py-8" role="status" aria-label="Loading"><Loader2 size={24} className="text-slate-500 animate-spin" /></div>
        ) : shelvesFailed ? (
          <ShelfMessage
            title="Couldn’t load your recent listening"
            detail="Music Assistant or Audiobookshelf did not answer. Check their settings in Identity."
            onRetry={() => { void recentQ.refetch(); void lastPlayedQ.refetch(); }}
          />
        ) : jumpBackIn.length === 0 ? (
          <ShelfMessage title="Nothing to jump back into yet" detail="Books in progress and recent music show up here." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {jumpBackIn.map((item) => (
              <ShelfCard key={item.key} item={item} busy={busy === item.key} disabled={busy !== null} onPlay={() => void playItem(item.req, item.key)} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Playlists</h2>
        {playlistsQ.isLoading ? (
          <div className="flex justify-center py-8" role="status" aria-label="Loading"><Loader2 size={24} className="text-slate-500 animate-spin" /></div>
        ) : playlistsQ.isError ? (
          <ShelfMessage title="Couldn’t load playlists" detail="Music Assistant did not answer." onRetry={() => void playlistsQ.refetch()} />
        ) : !playlistsQ.data?.playlists?.length ? (
          <ShelfMessage title="No playlists yet" detail="Playlists you make in Music Assistant appear here." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {playlistsQ.data.playlists.map((pl) => {
              const item: ShelfItem = {
                key: `pl-${pl.uri}`,
                req: { kind: 'uri', uri: pl.uri, title: pl.name },
                subtitle: pl.items > 0 ? `${pl.items} tracks` : 'Playlist',
                kind: 'playlist',
              };
              return <ShelfCard key={item.key} item={item} busy={busy === item.key} disabled={busy !== null} onPlay={() => void playItem(item.req, item.key)} />;
            })}
          </div>
        )}
      </section>

      <button
        type="button"
        onClick={() => setShowExplorer(true)}
        className="w-full min-h-11 py-4 rounded-2xl bg-gradient-to-r from-cyan-500/10 to-purple-500/10 border border-cyan-500/20 text-cyan-300 font-medium hover:from-cyan-500/20 hover:to-purple-500/20 transition-colors flex items-center justify-center gap-2"
      >
        <Library size={20} />
        Browse all media
      </button>

      <MediaExplorer open={showExplorer} onClose={() => setShowExplorer(false)} onPlay={playItem} />
    </div>
  );
};

export default Media;
