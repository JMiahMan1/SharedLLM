import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../../services/api';
import { errorText } from '../../../lib/errorText';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import { imageWidth } from './image';
import { NowPlayingCard, type NowPlayingView } from './NowPlayingCard';
import { mediaKeys } from '../api/keys';
import { useLivePosition } from '../engine/useLivePosition';
import { isLiveItem } from '../store/ingest';
import { useMediaStore } from '../store/mediaStore';
import { useMediaScreen } from '../pages/MediaScreen';

const TRANSPORT = ['play', 'resume', 'pause', 'next', 'previous', 'stop'];

/**
 * What the selected output is playing, with its controls: this device's web
 * player, or a speaker/TV from the store (its clock runs from the anchor, so
 * only this card re-renders with it).
 */
export function ActiveOutputCard() {
  useMediaToken(); // cover URLs are signed; re-render once the token arrives
  const { player, playback, devices } = useMediaScreen();
  const target = useMediaStore((s) => s.activeOutputId);
  const remote = useMediaStore((s) => (target ? s.players[target] ?? null : null));
  const playingInOtherTab = useMediaStore((s) => s.playingInOtherTab);
  const remotePosition = useLivePosition(remote);
  const isWeb = !target;
  const device = devices.find((d) => d.id === target);

  // Music Assistant knows the real title and the favourite flag for its items.
  const activeUri = isWeb ? player.mediaUri : remote?.item?.uri || null;
  const maItemUri = activeUri && activeUri.includes('://') ? activeUri : null;
  const { data: detail } = useQuery({
    queryKey: mediaKeys.detail(maItemUri ?? ''),
    queryFn: async () => {
      const data = await api.getMediaDetail(maItemUri!);
      return ((data && (data as { result?: unknown }).result) || data) as { name?: string; favorite?: boolean };
    },
    enabled: !!maItemUri,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [favoriteOverride, setFavoriteOverride] = useState<{ uri: string; value: boolean } | null>(null);
  const isFavorite = favoriteOverride?.uri === maItemUri
    ? favoriteOverride.value
    : Boolean(detail?.favorite ?? (isWeb ? player.mediaFavorite : false));
  const toggleFavorite = async () => {
    if (!maItemUri) return;
    const next = !isFavorite;
    setFavoriteOverride({ uri: maItemUri, value: next });
    try {
      await api.setMediaFavorite(maItemUri, next);
    } catch (err) {
      setFavoriteOverride({ uri: maItemUri, value: !next });
      toast.error(errorText(err, 'Could not update favorites'));
    }
  };

  const view: NowPlayingView = isWeb
    ? {
        title: player.mediaTitle || undefined,
        artist: player.mediaArtist || undefined,
        coverUrl: proxiedImage(player.mediaImage, imageWidth(80)),
        state: player.isPlaying ? 'playing' : player.mediaTitle ? 'paused' : 'idle',
        position: player.position,
        duration: player.duration,
        isLive: isLiveItem(undefined, player.mediaUri),
        trackKey: player.mediaUri || player.mediaTitle || '',
      }
    : {
        title: detail?.name && maItemUri === remote?.item?.uri ? detail.name : remote?.item?.title,
        artist: [remote?.item?.artist, remote?.item?.album].filter(Boolean).join(' · '),
        coverUrl: proxiedImage(remote?.item?.image, imageWidth(80)),
        state: remote?.status === 'buffering' ? 'playing' : remote?.status ?? 'idle',
        position: remotePosition,
        duration: remote?.item?.durationSec ?? 0,
        isLive: Boolean(remote?.item?.isLive),
        trackKey: remote?.item?.uri || remote?.item?.title || '',
      };

  const status = 'text-sm text-center text-[var(--site-text-muted)]';
  const action = 'min-h-11 rounded-xl bg-[var(--site-card-bg-hover)] px-4 text-[var(--site-text)] hover:bg-[var(--color-surface-2)]';
  const webFooter = playingInOtherTab ? (
    <div className={`flex flex-wrap items-center justify-center gap-2 ${status}`}>
      <span>Playing in another tab</span>
      <button type="button" onClick={() => void player.play().catch(() => {})} className={action}>Take over</button>
    </div>
  ) : player.connectionState === 'CONNECTED' ? (
    <p className={status}>Playing on this device</p>
  ) : player.connectionState === 'CONNECTING' ? (
    <p className={status}>Connecting this device…</p>
  ) : player.connectionState === 'RECONNECTING' ? (
    <p className={status} role="status">
      Connection lost. Reconnecting{player.reconnectAttempts ? ` (try ${player.reconnectAttempts} of ${player.maxReconnectAttempts})` : ''}…
    </p>
  ) : player.connectionState === 'FAILED' ? (
    <div className={`flex flex-wrap items-center justify-center gap-2 ${status}`} role="alert">
      <span>Couldn’t connect this device. {player.error || ''}</span>
      <button type="button" onClick={() => void player.reconnect()} className={action}>Try again</button>
    </div>
  ) : (
    <p className={status}>Plays through this device’s speakers or headphones</p>
  );
  const remoteFooter = (
    <p className={`${status} truncate`}>
      {device && !device.online ? `${device.name} is offline` : `Playing through ${device?.name || remote?.name || 'the selected speaker'}`}
    </p>
  );

  return (
    <NowPlayingCard
      view={view}
      volume={isWeb ? player.volume : remote?.volume ?? null}
      muted={isWeb ? player.muted : remote?.muted ?? false}
      busy={playback.busy && TRANSPORT.includes(playback.busy) ? playback.busy : null}
      isFavorite={isFavorite}
      idleHint={isWeb ? 'Pick something below to play it here' : `Pick something below to play on ${device?.name || 'this speaker'}`}
      footer={isWeb ? webFooter : remoteFooter}
      onPrevious={() => void playback.transport('previous')}
      onNext={() => void playback.transport('next')}
      onTogglePlay={() => void playback.transport(view.state === 'playing' ? 'pause' : 'resume')}
      onStop={view.state !== 'idle' ? () => void playback.transport('stop') : undefined}
      onVolumeChange={playback.setVolume}
      onMuteToggle={() => playback.setMuted(!(isWeb ? player.muted : remote?.muted ?? false))}
      onFavoriteToggle={maItemUri ? () => void toggleFavorite() : undefined}
      onSeek={view.duration > 0 ? (s) => void playback.seek(s) : undefined}
    />
  );
}
