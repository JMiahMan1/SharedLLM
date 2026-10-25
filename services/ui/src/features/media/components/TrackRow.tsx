import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, Loader2 } from 'lucide-react';
import { formatDuration } from '../format';
import { detailPath, itemKind, itemSubtitle, KIND_LABEL, playRequestFor } from '../items';
import { useMediaScreen } from '../pages/MediaScreen';
import { Artwork } from './Artwork';
import { ItemContextMenu, type MenuItemSource } from './ItemContextMenu';
import { muted, row, text } from './ui';

export interface RowItem extends MenuItemSource {
  artist?: string;
  album?: string;
  image?: string;
  duration?: number | null;
}

/**
 * One item in a list: artwork (or its number), title, who it is by, length,
 * and the "…" menu. A collection opens its page; anything else plays on the
 * selected output, with a spinner on this row only while it starts.
 */
export function TrackRow({
  item, index, subtitle, badge, showArt = true, onPlay, style, className = '',
}: {
  item: RowItem;
  /** Track number instead of artwork (album pages). */
  index?: number | null;
  subtitle?: string;
  badge?: ReactNode;
  showArt?: boolean;
  /** Play differently than the item itself (e.g. a book chapter seeks). */
  onPlay?: () => void;
  /** Positioning from a virtualized list. */
  style?: CSSProperties;
  className?: string;
}) {
  const { playback } = useMediaScreen();
  const kind = itemKind(item);
  const to = onPlay ? null : detailPath(item);
  const req = playRequestFor(item);
  const busyKey = `play:${item.uri}`;
  const busy = playback.busy === busyKey;
  const play = onPlay ?? (req ? () => void playback.play(req, busyKey) : undefined);
  const sub = subtitle ?? itemSubtitle({ artist: item.artist, album: item.album, media_type: item.media_type, uri: item.uri });

  const body = (
    <>
      {showArt ? (
        <span className="relative">
          <Artwork image={item.image} kind={kind} size={44} />
          {busy ? (
            <span className="absolute inset-0 flex items-center justify-center rounded-[var(--radius-card)] bg-black/50">
              <Loader2 size={18} className="animate-spin text-[var(--site-on-accent)]" aria-hidden />
            </span>
          ) : null}
        </span>
      ) : (
        <span className={`w-8 shrink-0 text-center text-sm tabular-nums ${muted}`}>
          {busy ? <Loader2 size={16} className="mx-auto animate-spin" aria-hidden /> : index ?? ''}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-sm font-medium ${text}`}>{item.name}</span>
        <span className={`block truncate text-xs ${muted}`}>{sub}</span>
      </span>
      {badge}
      {item.duration ? <span className={`shrink-0 text-xs tabular-nums ${muted}`}>{formatDuration(item.duration)}</span> : null}
      {to ? <ChevronRight size={16} className={`shrink-0 ${muted}`} aria-hidden /> : null}
    </>
  );

  return (
    <li className={`flex items-center gap-1 ${className}`} style={style}>
      {to ? (
        <Link to={to} data-result className={`${row} min-w-0 flex-1`} aria-label={`${item.name}, ${KIND_LABEL[kind].toLowerCase()}`}>{body}</Link>
      ) : (
        <button
          type="button"
          data-result
          // Enabled while it starts, so keyboard focus stays on this row.
          onClick={busy ? undefined : play}
          disabled={!play}
          aria-label={`Play ${item.name}`}
          aria-busy={busy}
          className={`${row} min-w-0 flex-1 disabled:opacity-60`}
        >
          {body}
        </button>
      )}
      <ItemContextMenu item={item} />
    </li>
  );
}
