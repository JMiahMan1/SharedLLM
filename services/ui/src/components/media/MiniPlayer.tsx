import { Link, useLocation } from 'react-router-dom';
import { Music, Pause, Play, SkipBack, SkipForward } from 'lucide-react';
import { useOptionalWebPlayer } from '../../context/webPlayerContext';

/**
 * The web player above the page, so music does not disappear when the reader
 * opens Calendar, Bible or Family. It is deliberately small: the full transport
 * and queue live on /media, and this bar only needs to get back there or pause
 * what is already playing.
 */
export default function MiniPlayer() {
  const player = useOptionalWebPlayer();
  const location = useLocation();
  if (!player || !player.mediaTitle || location.pathname.startsWith('/media')) return null;

  const pct = player.duration > 0 ? Math.min(100, Math.max(0, (player.position / player.duration) * 100)) : 0;

  return (
    <div
      className="fixed inset-x-3 bottom-20 md:bottom-5 z-40 mx-auto max-w-2xl"
      data-testid="mini-player"
    >
      <div className="glass-panel relative overflow-hidden rounded-2xl border border-cyan-500/25 shadow-lg shadow-black/30">
        <div className="absolute inset-x-0 top-0 h-0.5 bg-white/10">
          <div className="h-full bg-cyan-400 transition-[width] duration-500" style={{ width: `${pct}%` }} />
        </div>
        <div className="flex items-center gap-2 p-2 pl-3">
          <Music size={16} className="shrink-0 text-cyan-300" aria-hidden />
          <Link to="/media" className="min-w-0 flex-1 rounded-lg px-1 py-1.5 hover:bg-white/5">
            <p className="truncate text-sm font-medium text-white">{player.mediaTitle}</p>
            {player.mediaArtist ? (
              <p className="truncate text-[11px] text-slate-400">{player.mediaArtist}</p>
            ) : null}
          </Link>
          <button
            type="button"
            aria-label="Previous track"
            onClick={() => void player.previous()}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10 hover:text-white"
          >
            <SkipBack size={17} />
          </button>
          <button
            type="button"
            aria-label={player.isPlaying ? 'Pause' : 'Play'}
            onClick={() => (player.isPlaying ? player.pause() : player.play())}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-xl bg-cyan-500/20 text-cyan-200 hover:bg-cyan-500/30"
          >
            {player.isPlaying ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button
            type="button"
            aria-label="Next track"
            onClick={() => void player.next()}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10 hover:text-white"
          >
            <SkipForward size={17} />
          </button>
        </div>
      </div>
    </div>
  );
}
