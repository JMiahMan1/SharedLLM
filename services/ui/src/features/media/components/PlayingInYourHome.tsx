import { Pause, Play } from 'lucide-react';
import toast from 'react-hot-toast';
import { useMediaStore } from '../store/mediaStore';
import { playingElsewhere } from '../store/selectors';
import { adapterFor } from '../engine/adapters/adapters';
import { refreshMediaStatus } from '../engine/statusFeed';
import { useMediaScreen } from '../pages/MediaScreen';
import { Artwork } from './Artwork';
import { Shelf, ShelfRow } from './Shelf';
import { muted, text } from './ui';
import type { RemotePlayer } from '../types';

/**
 * Everything playing or paused around the house (UX-37), one card per
 * device: tap it to make it the device the controls drive; the small button
 * pauses or resumes it in place. Hidden when nothing is playing anywhere.
 */
/** The title, or the app when a TV casts something that gives none. */
const title = (p: RemotePlayer) => p.item?.title || (p.appName ? `Playing on ${p.appName}` : '');

export function PlayingInYourHome() {
  const { selectDevice, player } = useMediaScreen();
  const players = useMediaStore((s) => s.players);
  const target = useMediaStore((s) => s.activeOutputId);
  const { patchPlayer } = useMediaStore((s) => s.actions);
  const busy = playingElsewhere(players, player.playerId).filter((p) => p.id !== target);
  if (busy.length === 0) return null;

  const toggle = async (id: string, name: string, playing: boolean) => {
    patchPlayer(id, { status: playing ? 'paused' : 'playing' });
    try {
      await adapterFor(id, player).transport(id, playing ? 'pause' : 'resume');
    } catch {
      toast.error(`Could not ${playing ? 'pause' : 'play'} ${name}`);
    } finally {
      // The device is the authority: re-read it either way.
      void refreshMediaStatus();
    }
  };

  return (
    <Shelf title="Playing in your home">
      <ShelfRow>
        {busy.map((p) => {
          const playing = p.status === 'playing';
          return (
            <div key={p.id} className="glass-panel flex w-[min(80vw,300px)] shrink-0 snap-start items-center gap-3 rounded-[var(--radius-card)] p-2">
              <button
                type="button"
                onClick={() => selectDevice(p.id)}
                aria-label={`Control ${p.name}: ${title(p)}`}
                className="flex min-h-11 min-w-0 flex-1 items-center gap-3 rounded-xl text-left hover:bg-[var(--site-card-bg-hover)]"
              >
                <Artwork image={p.item?.image} kind="track" size={48} />
                <span className="min-w-0">
                  <span className={`block truncate text-sm font-medium ${text}`}>{title(p)}</span>
                  <span className={`block truncate text-xs ${muted}`}>{p.name}</span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => void toggle(p.id, p.name, playing)}
                aria-label={`${playing ? 'Pause' : 'Play'} ${p.name}`}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--site-accent-soft)] text-[var(--site-accent-text)] hover:bg-[var(--site-accent-hover)]"
              >
                {playing ? <Pause size={18} aria-hidden /> : <Play size={18} aria-hidden />}
              </button>
            </div>
          );
        })}
      </ShelfRow>
    </Shelf>
  );
}
