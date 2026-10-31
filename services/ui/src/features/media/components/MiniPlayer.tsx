import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Music, Pause, Play, SkipBack, SkipForward, X } from 'lucide-react';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import { useMediaStore } from '../store/mediaStore';
import { accentStyle, useArtworkAccent } from '../engine/artworkColor';
import { imageWidth } from './image';

/**
 * The web player above the page, so music does not disappear when the reader
 * opens Calendar, Bible or Family. Tapping it opens the full Now Playing sheet;
 * the controls here are only the ones worth reaching without leaving the page.
 * While nothing is playing it can be closed (the X, or a swipe down); it
 * comes back as soon as something plays.
 */
export default function MiniPlayer({ onOpen }: { onOpen?: () => void }) {
  const player = useOptionalWebPlayer();
  const location = useLocation();
  // Re-render once the signed media token for the cover URL arrives.
  useMediaToken();
  const otherTab = useMediaStore((s) => s.playingInOtherTab);
  const accent = useArtworkAccent(player?.mediaImage);
  // The title it was closed on; cleared when playback starts again.
  const [closedOn, setClosedOn] = useState<string | null>(null);
  const playing = !!player?.isPlaying;
  if (playing && closedOn !== null) setClosedOn(null);
  if (!player || !player.mediaTitle || location.pathname.startsWith('/media')) return null;
  if (!playing && closedOn === player.mediaTitle) return null;
  const close = () => setClosedOn(player.mediaTitle);

  const pct = player.duration > 0 ? Math.min(100, Math.max(0, (player.position / player.duration) * 100)) : 0;
  const image = proxiedImage(player.mediaImage, imageWidth(44));
  const summary = (
    <>
      <p className="truncate text-sm font-medium text-white">{player.mediaTitle}</p>
      {player.mediaArtist ? <p className="truncate text-[11px] text-slate-400">{player.mediaArtist}</p> : null}
    </>
  );

  // Sit above the bottom bar wherever it is shown (its height is published as
  // --bottom-bar, safe-area included); on desktop the var is 0 so this is the
  // same 20px inset as before.
  return (
    <motion.div
      drag={onOpen || !playing ? 'y' : false}
      dragConstraints={{ top: 0, bottom: 0 }}
      dragElastic={0.25}
      dragSnapToOrigin
      onDragEnd={(_, info) => {
        if (info.offset.y < -28 || info.velocity.y < -300) onOpen?.();
        else if (!playing && (info.offset.y > 40 || info.velocity.y > 300)) close();
      }}
      className="fixed inset-x-3 bottom-[calc(var(--bottom-bar)+1.25rem)] z-40 mx-auto max-w-2xl"
      style={accentStyle(accent)}
      data-testid="mini-player"
    >
      <div className="glass-panel relative overflow-hidden rounded-2xl border border-[var(--site-accent-border)] shadow-lg shadow-black/30">
        <div className="absolute inset-x-0 top-0 h-0.5 bg-white/10">
          <div className="h-full bg-[var(--media-accent,var(--site-accent))] transition-[width] duration-500" style={{ width: `${pct}%` }} />
        </div>
        <div className="flex items-center gap-2 p-2 pl-3">
          {image ? (
            <motion.img
              layoutId="np-art"
              src={image}
              alt=""
              width={44}
              height={44}
              loading="lazy"
              decoding="async"
              sizes="44px"
              className="h-11 w-11 shrink-0 rounded-lg object-cover"
            />
          ) : (
            <motion.div
              layoutId="np-art"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-white/10"
            >
              <Music size={17} className="text-[var(--media-accent,var(--site-accent-text))]" aria-hidden />
            </motion.div>
          )}
          {onOpen ? (
            <button
              type="button"
              onClick={onOpen}
              aria-label="Open now playing"
              className="min-w-0 flex-1 rounded-lg px-1 py-1.5 text-left hover:bg-white/5"
            >
              {summary}
            </button>
          ) : (
            <Link to="/media" className="min-w-0 flex-1 rounded-lg px-1 py-1.5 hover:bg-white/5">
              {summary}
            </Link>
          )}
          {otherTab ? (
            <>
              <span className="shrink-0 text-xs text-slate-400">Playing in another tab</span>
              <button
                type="button"
                onClick={() => void player.play().catch(() => {})}
                className="min-h-11 shrink-0 rounded-xl bg-[var(--site-accent-soft)] px-3 text-sm text-[var(--media-accent,var(--site-accent-text))] hover:bg-[var(--site-accent-hover)]"
              >
                Take over
              </button>
            </>
          ) : (
            <>
              {/* Paused, the close button takes its place, so the title keeps its room on a phone. */}
              {playing ? (
                <button
                  type="button"
                  aria-label="Previous track"
                  onClick={() => void player.previous()}
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10 hover:text-white"
                >
                  <SkipBack size={17} />
                </button>
              ) : null}
              <button
                type="button"
                aria-label={player.isPlaying ? 'Pause' : 'Play'}
                onClick={() => (player.isPlaying ? player.pause() : player.play())}
                className="flex min-h-11 min-w-11 items-center justify-center rounded-xl bg-[var(--site-accent-soft)] text-[var(--media-accent,var(--site-accent-text))] hover:bg-[var(--site-accent-hover)]"
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
            </>
          )}
          {!playing ? (
            <button
              type="button"
              aria-label="Close player"
              onClick={close}
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-white"
            >
              <X size={17} />
            </button>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}
