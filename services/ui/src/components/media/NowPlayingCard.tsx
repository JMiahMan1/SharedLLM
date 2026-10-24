import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, Square, Heart, Music, Loader2,
  Volume1, Volume2, VolumeX,
} from 'lucide-react';
import { formatDuration } from '../../features/media/format';

// Cover image that falls back to nothing (the parent shows the gradient icon)
// if the proxied URL fails. Keyed by `src` at the call site, so a new cover
// remounts it and clears `failed`.
function CoverImage({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return <img src={src} alt="" onError={() => setFailed(true)} className="w-full h-full object-cover relative z-10" />;
}

const iconButton =
  'min-w-11 min-h-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-50';

export interface NowPlayingView {
  title?: string;
  artist?: string;
  coverUrl?: string | null;
  state: 'playing' | 'paused' | 'idle';
  position: number;
  duration: number;
  /** Changes when the track changes (clears a leftover scrub position). */
  trackKey: string;
}

/** The player card: what is playing, transport, volume and the scrubber. */
export function NowPlayingCard({
  view,
  volume,
  muted,
  busy,
  isFavorite,
  idleHint,
  footer,
  onPrevious,
  onTogglePlay,
  onNext,
  onStop,
  onVolumeChange,
  onMuteToggle,
  onFavoriteToggle,
  onSeek,
}: {
  view: NowPlayingView;
  volume: number;
  muted: boolean;
  /** The transport command in flight, if any (shows its spinner). */
  busy: string | null;
  isFavorite: boolean;
  idleHint: string;
  footer: ReactNode;
  onPrevious: () => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onStop?: () => void;
  onVolumeChange: (v: number) => void;
  onMuteToggle: () => void;
  onFavoriteToggle?: () => void;
  onSeek?: (seconds: number) => void;
}) {
  const { title, artist, coverUrl, state, position, duration, trackKey } = view;
  const active = state !== 'idle';

  const trackRef = useRef<HTMLDivElement>(null);
  const dragRaf = useRef<number | null>(null);
  const dragClientX = useRef<number | null>(null);
  const draggingRef = useRef(false);
  const [dragTime, setDragTime] = useState<number | null>(null);
  // Keyed on the track, not its metadata: the status refresh mid-drag must
  // not cancel the seek under the user's finger.
  useEffect(() => {
    if (!draggingRef.current) setDragTime(null);
  }, [trackKey]);

  const timeAt = (clientX: number): number => {
    if (!trackRef.current || duration <= 0) return 0;
    const rect = trackRef.current.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return Math.round(Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * duration);
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragRaf.current !== null) cancelAnimationFrame(dragRaf.current);
    dragRaf.current = null;
    draggingRef.current = false;
    setDragTime(null);
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    } catch { /* not captured */ }
  };

  const shown = dragTime ?? position;
  const pct = duration > 0 ? Math.min(100, Math.max(0, (shown / duration) * 100)) : 0;

  return (
    <div className="glass-panel rounded-2xl p-4 sm:p-5 border border-cyan-500/20 relative">
      <div className="absolute inset-0 bg-gradient-to-br from-cyan-500/5 via-transparent to-purple-500/5 pointer-events-none rounded-2xl" />

      <div className="relative flex items-center gap-4">
        <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0 relative overflow-hidden">
          {coverUrl ? <CoverImage key={coverUrl} src={coverUrl} /> : null}
          <Music size={28} className="text-cyan-400 absolute" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          {active ? (
            <>
              <p className="text-white font-medium text-lg leading-snug truncate">{title || 'Untitled'}</p>
              {artist ? <p className="text-sm text-slate-400 truncate">{artist}</p> : null}
            </>
          ) : (
            <>
              <p className="text-white font-medium text-lg">Nothing playing</p>
              <p className="text-sm text-slate-400">{idleHint}</p>
            </>
          )}
        </div>
        {active && onFavoriteToggle && (
          <button
            type="button"
            onClick={onFavoriteToggle}
            className={`${iconButton} shrink-0 ${isFavorite ? 'text-red-500 hover:text-red-400' : ''}`}
            aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={isFavorite}
          >
            <Heart size={20} fill={isFavorite ? 'currentColor' : 'none'} />
          </button>
        )}
      </div>

      {active && duration > 0 && (
        <div className="relative mt-3">
          <div
            ref={trackRef}
            onPointerDown={(e) => {
              if (!onSeek) return;
              try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* ignore */ }
              draggingRef.current = true;
              setDragTime(timeAt(e.clientX));
            }}
            onPointerMove={(e) => {
              if (!draggingRef.current) return;
              // Coalesce into an animation frame: pointermove fires faster than
              // the WebView paints, which made the knob lag the finger.
              dragClientX.current = e.clientX;
              if (dragRaf.current === null) {
                dragRaf.current = requestAnimationFrame(() => {
                  dragRaf.current = null;
                  if (draggingRef.current && dragClientX.current !== null) setDragTime(timeAt(dragClientX.current));
                });
              }
            }}
            onPointerUp={(e) => {
              if (!draggingRef.current || !onSeek) return;
              const t = timeAt(e.clientX);
              endDrag(e);
              onSeek(t);
            }}
            onPointerCancel={endDrag}
            role={onSeek ? 'slider' : undefined}
            tabIndex={onSeek ? 0 : undefined}
            aria-label="Track position"
            aria-valuemin={0}
            aria-valuemax={Math.round(duration)}
            aria-valuenow={Math.round(shown)}
            aria-valuetext={`${formatDuration(shown)} of ${formatDuration(duration)}`}
            onKeyDown={onSeek ? (e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
                e.preventDefault();
                onSeek(Math.min(duration, position + 10));
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
                e.preventDefault();
                onSeek(Math.max(0, position - 10));
              }
            } : undefined}
            className={`relative py-4 select-none touch-none ${onSeek ? 'cursor-pointer group' : ''}`}
          >
            <div className="w-full h-2 bg-white/10 rounded-full overflow-hidden pointer-events-none">
              <div className="h-full bg-gradient-to-r from-cyan-400 to-purple-400 rounded-full" style={{ width: `${pct}%` }} />
            </div>
            {onSeek && (
              <div
                className={`absolute top-1/2 -translate-y-1/2 h-5 w-5 -ml-2.5 bg-white rounded-full shadow-md shadow-cyan-500/50 pointer-events-none transition-transform ${
                  dragTime !== null ? 'scale-125' : 'pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100'
                }`}
                style={{ left: `${pct}%` }}
              />
            )}
          </div>
          <div className="flex justify-between text-[11px] text-slate-500 font-mono -mt-1">
            <span>{formatDuration(shown)}</span>
            <span>{formatDuration(duration)}</span>
          </div>
        </div>
      )}
      {active && duration <= 0 && (
        <div className="relative mt-3 flex items-center gap-2 py-2" aria-label="Live stream">
          <span className="h-2.5 w-2.5 rounded-full bg-rose-500 animate-pulse" aria-hidden />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-rose-300">Live</span>
          <div className="flex-1 h-1.5 rounded-full bg-gradient-to-r from-rose-500/40 via-rose-400/20 to-transparent" />
          <span className="text-[11px] text-slate-500 font-mono">{formatDuration(position)}</span>
        </div>
      )}

      <div className="relative mt-3 flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex items-center justify-center gap-2 sm:flex-1 sm:justify-start">
          <button type="button" onClick={onPrevious} disabled={busy !== null} className={iconButton} aria-label="Previous track">
            {busy === 'previous' ? <Loader2 size={20} className="animate-spin" /> : <SkipBack size={20} />}
          </button>
          <button
            type="button"
            onClick={onTogglePlay}
            disabled={busy !== null}
            className="w-14 h-14 rounded-full bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-300 hover:bg-cyan-500/30 transition-colors disabled:opacity-50"
            aria-label={state === 'playing' ? 'Pause' : 'Play'}
          >
            {busy === 'play' || busy === 'pause' || busy === 'resume' ? (
              <Loader2 size={22} className="animate-spin" />
            ) : state === 'playing' ? <Pause size={24} /> : <Play size={24} />}
          </button>
          <button type="button" onClick={onNext} disabled={busy !== null} className={iconButton} aria-label="Next track">
            {busy === 'next' ? <Loader2 size={20} className="animate-spin" /> : <SkipForward size={20} />}
          </button>
          {onStop && (
            <button type="button" onClick={onStop} disabled={busy !== null} className={`${iconButton} text-red-400 hover:text-red-300`} aria-label="Stop">
              {busy === 'stop' ? <Loader2 size={18} className="animate-spin" /> : <Square size={18} />}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 sm:w-56">
          <button type="button" onClick={onMuteToggle} className={`${iconButton} shrink-0`} aria-label={muted ? 'Unmute' : 'Mute'} aria-pressed={muted}>
            {muted || volume === 0 ? <VolumeX size={18} /> : volume < 50 ? <Volume1 size={18} /> : <Volume2 size={18} />}
          </button>
          <input
            type="range"
            min="0"
            max="100"
            value={muted ? 0 : volume}
            onChange={(e) => onVolumeChange(Number(e.target.value))}
            className="flex-1 min-w-0 accent-cyan-400 h-11 cursor-pointer"
            aria-label="Volume"
          />
          <span className="text-xs text-slate-400 w-10 text-right tabular-nums shrink-0">{muted ? 'Muted' : `${volume}%`}</span>
        </div>
      </div>

      <div className="relative mt-3 pt-3 border-t border-white/5">{footer}</div>
    </div>
  );
}
