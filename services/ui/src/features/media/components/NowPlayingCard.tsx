import { useState } from 'react';
import type { ReactNode } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, Square, Heart, Music, Loader2,
} from 'lucide-react';
import { formatDuration } from '../format';
import { Scrubber, VolumeSlider } from './Scrubber';

// Cover image that falls back to nothing (the parent shows the gradient icon)
// if the proxied URL fails. Keyed by `src` at the call site, so a new cover
// remounts it and clears `failed`.
function CoverImage({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={src}
      alt=""
      width={80}
      height={80}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className="w-full h-full object-cover relative z-10"
    />
  );
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
  /** True only for radio/stream items (UX-12), never from duration <= 0. */
  isLive?: boolean;
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
  volume: number | null;
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
  const { title, artist, coverUrl, state, position, duration, isLive, trackKey } = view;
  const active = state !== 'idle';

  return (
    <div className="glass-panel rounded-2xl p-4 sm:p-5 border border-[var(--site-accent-border)] relative">
      <div className="absolute inset-0 bg-gradient-to-br from-[var(--site-accent-soft)] via-transparent to-transparent pointer-events-none rounded-2xl" />

      <div className="relative flex items-center gap-4">
        <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-xl bg-gradient-to-br from-[var(--site-accent-soft)] to-[var(--color-surface-1)] flex items-center justify-center shrink-0 relative overflow-hidden">
          {coverUrl ? <CoverImage key={coverUrl} src={coverUrl} /> : null}
          <Music size={28} className="text-[var(--media-accent,var(--site-accent-text))] absolute" aria-hidden />
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
            className={`${iconButton} shrink-0 ${isFavorite ? 'text-[var(--media-accent,var(--site-accent-text))]' : ''}`}
            aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={isFavorite}
          >
            <Heart size={20} fill={isFavorite ? 'currentColor' : 'none'} />
          </button>
        )}
      </div>

      {active && !isLive && duration > 0 && (
        <Scrubber
          position={position}
          duration={duration}
          onSeek={onSeek}
          trackKey={trackKey}
          times="total"
          className="relative mt-3"
        />
      )}
      {active && isLive && (
        <div className="relative mt-3 flex items-center gap-2 py-2" aria-label="Live stream">
          <span className="h-2.5 w-2.5 rounded-full bg-rose-500 animate-pulse motion-reduce:animate-none" aria-hidden />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-rose-300">Live</span>
          <div className="flex-1 h-1.5 rounded-full bg-gradient-to-r from-rose-500/40 via-rose-400/20 to-transparent" />
          <span className="text-[11px] text-slate-500 font-mono">{formatDuration(position)}</span>
        </div>
      )}
      {active && !isLive && duration <= 0 && (
        <div className="relative mt-3 py-2" aria-label="Track progress loading" data-testid="scrubber-skeleton">
          <div className="skeleton h-1.5 w-full rounded-full" />
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
            className="w-14 h-14 rounded-full bg-[var(--site-accent-soft)] border border-[var(--site-accent-border)] flex items-center justify-center text-[var(--media-accent,var(--site-accent-text))] hover:bg-[var(--site-accent-hover)] transition-colors disabled:opacity-50"
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
        <VolumeSlider volume={volume} muted={muted} onChange={onVolumeChange} onToggleMute={onMuteToggle} className="sm:w-56" />
      </div>

      <div className="relative mt-3 pt-3 border-t border-white/5">{footer}</div>
    </div>
  );
}
