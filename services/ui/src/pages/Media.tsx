import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Library, List, Loader2, Music, Play, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import { useSharedMAWebPlayer } from '../context/MediaPlayerContext';
import { errorText } from '../lib/errorText';
import { sendMaRemoteCommand } from '../lib/maRemotePlayer';
import { proxiedImage } from '../lib/mediaImage';
import { useMediaToken } from '../lib/mediaToken';
import { formatTimeAgo, normalizePlayedAt } from '../features/media/format';
import { mediaKeys } from '../features/media/api/keys';
import { useBooksInProgress, usePlaylists, useRecent } from '../features/media/api/queries';
import { useMediaActions, useMediaStore } from '../features/media/store/mediaStore';
import { deviceOptions, selectActiveOutputId, selectPlayers } from '../features/media/store/selectors';
import { useMediaStatusFeed } from '../features/media/engine/statusFeed';
import { useLivePosition } from '../features/media/engine/useLivePosition';
import { DeviceSelector } from '../components/media/DeviceSelector';
import { NowPlayingCard, type NowPlayingView } from '../components/media/NowPlayingCard';
import { MediaExplorer } from '../components/media/MediaExplorer';
import type { DeviceOption, PlayRequest, QueueOption } from '../components/media/types';

/* helpers */

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
  // Cover URLs are signed with the media token; re-render when it arrives.
  useMediaToken();

  /** '' = this device's web player, `ma:<id>` = an MA-only player, else an HA entity. */
  const selectedTarget = useMediaStore(selectActiveOutputId);
  const players = useMediaStore(selectPlayers);
  const { selectOutput, setVolume: storeVolume, setMuted: storeMuted, patchPlayer } = useMediaActions();
  const isWebPlayer = !selectedTarget;
  const selectedRef = useRef(selectedTarget);
  useEffect(() => {
    selectedRef.current = selectedTarget;
  }, [selectedTarget]);
  // Arriving from the output picker's "Control" action is a choice; until the
  // person picks, the store follows a speaker that is already playing so the
  // page never shows an empty card over live music.
  const [searchParams] = useSearchParams();
  const requestedOutput = searchParams.get('output');
  useEffect(() => {
    if (requestedOutput) selectOutput(requestedOutput);
  }, [requestedOutput, selectOutput]);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showExplorer, setShowExplorer] = useState(false);
  const volumeTimerRef = useRef<number | null>(null);

  /* device status */

  const { refresh: refreshStatus } = useMediaStatusFeed(maPlayer);

  const { data: entities = [] } = useQuery({
    queryKey: mediaKeys.entities(),
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
    staleTime: 60_000,
  });

  const devices = useMemo<DeviceOption[]>(
    () => deviceOptions(entities, players, maPlayer.playerId),
    [entities, players, maPlayer.playerId],
  );

  const selectedDevice = devices.find((d) => d.id === selectedTarget);
  const remote = selectedTarget ? players[selectedTarget] ?? null : null;
  const volume = remote?.volume ?? 50;
  const muted = remote?.muted ?? false;
  const remotePosition = useLivePosition(remote);

  /* favourites (Music Assistant items) */

  const activeUri = isWebPlayer ? maPlayer.mediaUri : remote?.item?.uri || null;
  const maItemUri = activeUri && activeUri.includes('://') ? activeUri : null;
  const { data: detail } = useQuery({
    queryKey: mediaKeys.detail(maItemUri ?? ''),
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

  /** Queue a Music Assistant item on the selected device: this device's own
   *  queue, an MA player's queue, or a Home Assistant speaker's MA queue. */
  const queueItem = useCallback(async (req: PlayRequest, option: QueueOption) => {
    if (req.kind !== 'uri') return;
    void trigger('light');
    setError(null);
    const target = selectedRef.current;
    try {
      if (!target) {
        await maPlayer.enqueue(req.uri, option);
      } else if (target.startsWith('ma:')) {
        await sendMaRemoteCommand('player_queues/play_media', { queue_id: target.slice(3), media: [req.uri], option });
      } else {
        const resp = await api.mediaPlay({ entity_id: target, query: req.uri, media_type: 'music', enqueue: option });
        if (resp.status === 'FAILURE') throw new Error(resp.message || 'Could not add to the queue');
      }
      toast.success(option === 'next' ? `${req.title} plays next` : `${req.title} added to the queue`);
    } catch (err) {
      setError(errorText(err, 'Could not add to the queue'));
    }
  }, [maPlayer, trigger]);

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
        if (nextState) patchPlayer(target, { status: nextState });
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
  }, [patchPlayer, refreshStatus, trigger]);

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
    storeVolume(target, v);
    if (volumeTimerRef.current) clearTimeout(volumeTimerRef.current);
    volumeTimerRef.current = window.setTimeout(() => {
      const send = target.startsWith('ma:')
        ? sendMaRemoteCommand('players/cmd/volume_set', { player_id: target.slice(3), volume_level: v })
        : api.mediaTransport({ entity_id: target, command: 'volume_set', volume_level: v / 100 });
      void Promise.resolve(send).catch(() => toast.error('Could not change the volume'));
    }, 200);
  }, [storeVolume]);

  const toggleRemoteMute = useCallback(() => {
    const target = selectedRef.current;
    if (!target) return;
    const next = !muted;
    storeMuted(target, next);
    const send = target.startsWith('ma:')
      ? sendMaRemoteCommand('players/cmd/volume_mute', { player_id: target.slice(3), muted: next })
      : api.mediaTransport({ entity_id: target, command: 'volume_mute', muted: next });
    void Promise.resolve(send).catch(() => {
      storeMuted(target, !next);
      toast.error('Could not mute that player');
    });
  }, [muted, storeMuted]);

  const handleRemoteSeek = useCallback(async (seconds: number) => {
    const target = selectedRef.current;
    if (!target) return;
    patchPlayer(target, { positionSec: seconds, positionUpdatedAt: Date.now() });
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
  }, [patchPlayer]);

  const selectDevice = useCallback((id: string) => {
    void trigger('light');
    selectOutput(id);
    setError(null);
    // Remember the choice server-side (voice "pause" and the dashboard follow
    // it). The web player records itself once it plays.
    if (id && !id.startsWith('ma:')) {
      void api.syncMediaState({ entity_id: id, state: 'idle' }).catch(() => {});
    }
  }, [selectOutput, trigger]);

  /* shelves */

  const playlistsQ = usePlaylists();
  const recentQ = useRecent();
  const lastPlayedQ = useBooksInProgress();

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
  const absDown = lastPlayedQ.isError || !!lastPlayedQ.data?.notice;
  const maDown = recentQ.isError;
  const shelvesDetail = absDown && maDown
    ? 'Neither Music Assistant nor Audiobookshelf answered. Check their URLs in Identity.'
    : absDown
      ? 'Audiobookshelf did not answer. Check its URL in Identity.'
      : 'Music Assistant did not answer. Check its URL in Identity.';

  /* render */

  const remoteView: NowPlayingView = {
    title: detail?.name && maItemUri === remote?.item?.uri ? detail.name : remote?.item?.title,
    artist: [remote?.item?.artist, remote?.item?.album].filter(Boolean).join(' · '),
    coverUrl: proxiedImage(remote?.item?.image),
    state: remote?.status === 'buffering' ? 'playing' : remote?.status ?? 'idle',
    position: remotePosition,
    duration: remote?.item?.durationSec ?? 0,
    trackKey: remote?.item?.uri || remote?.item?.title || '',
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
            {selectedDevice && !selectedDevice.online ? `${selectedDevice.name} is offline` : `Playing through ${selectedDevice?.name || remote?.name || 'the selected speaker'}`}
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
            detail={shelvesDetail}
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

      <MediaExplorer open={showExplorer} onClose={() => setShowExplorer(false)} onPlay={playItem} onQueue={queueItem} />
    </div>
  );
};

export default Media;
