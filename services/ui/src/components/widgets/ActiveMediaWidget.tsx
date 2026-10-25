import { useEffect, useRef } from 'react';
import { Pause, Play, SkipBack, SkipForward } from 'lucide-react';
import toast from 'react-hot-toast';
import type { IActiveMediaWidgetProps } from '../../types/widget';
import { useOptionalWebPlayer } from '../../context/webPlayerContext';
import { useMediaToken } from '../../lib/mediaToken';
import { adapterFor } from '../../features/media/engine/adapters/adapters';
import type { TransportCommand } from '../../features/media/engine/adapters/PlayerAdapter';
import { useMediaStatusFeed } from '../../features/media/engine/statusFeed';
import { useLivePosition } from '../../features/media/engine/useLivePosition';
import { VOLUME_DEBOUNCE_MS } from '../../features/media/engine/usePlayback';
import { useMediaStore } from '../../features/media/store/mediaStore';
import { playingElsewhere } from '../../features/media/store/selectors';
import { Artwork } from '../../features/media/components/Artwork';
import { Scrubber, VolumeSlider } from '../../features/media/components/Scrubber';
import { formatDuration } from '../../features/media/format';

const controlButton =
  'flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--site-text-muted)] transition-colors hover:bg-[var(--site-card-bg-hover)] hover:text-[var(--site-text)]';

/**
 * The dashboard's "now playing" card (P5-T9), on the same store, routing and
 * controls as the media section: this device's own player when it has
 * something loaded (driven directly, never through the server's record of
 * it), else whatever plays around the house, from the shared status feed and
 * event stream. Seek is sent once on release and volume once it settles
 * (BUG-54); every control has a name and a 44 px target (BUG-68).
 */
const ActiveMediaWidget = ({ userSettings, onMediaStop, settingsButton }: IActiveMediaWidgetProps) => {
  useMediaToken(); // cover art URLs are signed
  const web = useOptionalWebPlayer();
  useMediaStatusFeed(web, { maPlayers: false });
  const loaded = useMediaStore((s) => s.statusReadAt !== null);
  const players = useMediaStore((s) => s.players);
  const { setVolume: storeVolume, setMuted: storeMuted, patchPlayer } = useMediaStore((s) => s.actions);

  // This device first while it has something loaded and is playing (or
  // nothing else is); otherwise the busiest speaker in the house.
  const remote = playingElsewhere(players)[0] ?? null;
  const showWeb = Boolean(web?.mediaTitle) && (web!.isPlaying || !remote || remote.status !== 'playing');
  const shown = showWeb ? null : remote;
  const remotePosition = useLivePosition(shown);
  const outputId = showWeb ? '' : shown?.id ?? null;
  const volumeTimer = useRef<number | null>(null);

  const nothing = loaded && !showWeb && !shown;
  useEffect(() => {
    if (nothing && !userSettings.is_pinned) onMediaStop?.();
  }, [nothing, userSettings.is_pinned, onMediaStop]);
  useEffect(() => () => {
    if (volumeTimer.current) clearTimeout(volumeTimer.current);
  }, []);

  const adapter = () => (outputId === null ? null : adapterFor(outputId, web));

  const transport = async (command: TransportCommand) => {
    const a = adapter();
    if (!a || outputId === null) return;
    if (outputId && (command === 'pause' || command === 'resume')) {
      patchPlayer(outputId, { status: command === 'pause' ? 'paused' : 'playing' });
    }
    try {
      await a.transport(outputId, command);
    } catch {
      toast.error(command === 'next' ? 'Could not skip ahead' : command === 'previous' ? 'Could not go back' : 'Could not control playback');
    }
  };
  const seek = (seconds: number) => {
    const a = adapter();
    if (!a || outputId === null) return;
    if (outputId) patchPlayer(outputId, { positionSec: seconds, positionUpdatedAt: Date.now() });
    a.seek(outputId, seconds).catch(() => toast.error('Could not seek on that player'));
  };
  const setVolume = (volume: number) => {
    const a = adapter();
    if (!a || outputId === null) return;
    if (!outputId) {
      void a.setVolume(outputId, volume);
      return;
    }
    storeVolume(outputId, volume);
    if (volumeTimer.current) clearTimeout(volumeTimer.current);
    volumeTimer.current = window.setTimeout(() => {
      a.setVolume(outputId, volume).catch(() => toast.error('Could not set the volume on that player'));
    }, VOLUME_DEBOUNCE_MS);
  };
  const toggleMute = () => {
    const a = adapter();
    if (!a || outputId === null) return;
    const next = !(showWeb ? web!.muted : shown?.muted);
    if (outputId) storeMuted(outputId, next);
    a.setMuted(outputId, next).catch(() => toast.error('Could not mute that player'));
  };

  const view = showWeb && web
    ? {
        title: web.mediaTitle || '', artist: web.mediaArtist || '', device: 'This device', image: web.mediaImage,
        playing: web.isPlaying, position: web.position, duration: web.duration, volume: web.volume, muted: web.muted,
        trackKey: web.mediaUri || web.mediaTitle || '',
      }
    : shown
      ? {
          title: shown.item?.title || (shown.appName ? `Playing on ${shown.appName}` : 'Playing something'),
          artist: shown.item?.artist || '', device: shown.name, image: shown.item?.image ?? null,
          playing: shown.status === 'playing', position: remotePosition, duration: shown.item?.durationSec ?? 0,
          volume: shown.volume ?? 50, muted: shown.muted, trackKey: shown.item?.uri || shown.item?.title || '',
        }
      : null;

  // Laid out for the 200px "wide" cell it defaults to: track info, then one
  // row of transport + volume, then the scrubber. Anything taller scrolls
  // inside the card instead of spilling into the widget below.
  return (
    <div className="glass-card relative flex h-full flex-col overflow-hidden p-4">
      <div className="absolute right-2 top-2 z-10">{settingsButton}</div>

      {!loaded && !view ? (
        <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading media">
          <div className="skeleton h-12 w-full rounded-xl" />
        </div>
      ) : view ? (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          <div className="flex items-center gap-3 pr-10">
            <Artwork image={view.image} kind="track" size={48} />
            <div className="min-w-0">
              <p className="truncate font-bold text-[var(--site-text)]">{view.title}</p>
              {view.artist ? <p className="truncate text-sm text-[var(--site-text-muted)]">{view.artist}</p> : null}
              <p className="truncate text-xs text-[var(--site-text-muted)]">{view.device}</p>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button type="button" onClick={() => void transport('previous')} className={controlButton} aria-label="Previous track">
              <SkipBack size={18} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => void transport(view.playing ? 'pause' : 'resume')}
              aria-label={view.playing ? 'Pause' : 'Play'}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--site-accent-soft)] text-[var(--site-accent-text)] transition-colors hover:bg-[var(--site-accent-hover)]"
            >
              {view.playing ? <Pause size={18} aria-hidden /> : <Play size={18} aria-hidden />}
            </button>
            <button type="button" onClick={() => void transport('next')} className={controlButton} aria-label="Next track">
              <SkipForward size={18} aria-hidden />
            </button>
            <VolumeSlider volume={view.volume} muted={view.muted} onChange={setVolume} onToggleMute={toggleMute} className="ml-1 min-w-0 flex-1" />
          </div>

          {view.duration > 0 ? (
            <div className="flex items-center gap-2">
              <Scrubber position={view.position} duration={view.duration} onSeek={seek} trackKey={view.trackKey} times="none" className="flex-1" />
              <p className="shrink-0 text-[11px] tabular-nums text-[var(--site-text-muted)]">
                {formatDuration(view.position)} / {formatDuration(view.duration)}
              </p>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-[var(--site-text-muted)]">Nothing playing</p>
        </div>
      )}
    </div>
  );
};

export default ActiveMediaWidget;
