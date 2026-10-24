import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Play } from 'lucide-react';
import { Artwork } from './Artwork';
import { ItemContextMenu, type MenuItemSource } from './ItemContextMenu';
import { muted, text } from './ui';
import type { ItemKind } from '../items';

/** A ring around progress through a book or episode (0..1). */
export function ProgressRing({ value, size = 36 }: { value: number; size?: number }) {
  const stroke = 3;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, Math.max(0, value));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${Math.round(pct * 100)}% listened`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="rgba(0,0,0,0.55)" stroke="var(--color-border-mid)" strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--site-accent)" strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - pct)} transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

/**
 * An artwork tile for shelves and grids (UX-15). The whole card is the
 * target: it plays (`onPlay`) or opens the item (`to`). A play button sits on
 * the art, always visible on touch screens and on hover with a mouse
 * (UX-24); for a collection it plays without opening.
 */
export function MediaCard({
  title, subtitle, image, kind, onPlay, to, busy, progress, label, menuItem,
}: {
  title: string;
  subtitle?: string;
  image?: string | null;
  kind: ItemKind;
  onPlay?: () => void;
  to?: string | null;
  busy?: boolean;
  progress?: number;
  /** Extra line, e.g. "2h 13m left". */
  label?: string | null;
  /** Adds the "…" menu (Play next, Add to queue, Go to, Favorite). */
  menuItem?: MenuItemSource;
}) {
  const body = (
    <>
      <span className="relative block">
        <Artwork image={image} kind={kind} size={184} fill />
        {progress !== undefined ? (
          <span className="absolute bottom-2 left-2"><ProgressRing value={progress} /></span>
        ) : null}
      </span>
      <span className={`mt-2 block truncate text-sm font-medium ${text}`}>{title}</span>
      {subtitle ? <span className={`block truncate text-xs ${muted}`}>{subtitle}</span> : null}
      {label ? <span className={`block truncate text-xs ${muted}`}>{label}</span> : null}
    </>
  );
  // The overlay sits on the art: a box the art's size over the card's top.
  const overlay = (child: ReactNode) => (
    <span className="pointer-events-none absolute inset-x-0 top-0 aspect-square">
      <span className="absolute bottom-2 right-2">{child}</span>
    </span>
  );
  const disc = 'flex h-11 w-11 items-center justify-center rounded-full bg-[var(--site-accent)] text-[var(--site-on-accent)] shadow-lg';

  // A collection opens; something playable on its own plays from anywhere on the card.
  return (
    <div className="group relative w-[clamp(132px,38vw,184px)] shrink-0 snap-start">
      {to ? (
        <Link to={to} data-result className="block rounded-[var(--radius-card)] focus-visible:outline-2 focus-visible:outline-[var(--site-accent-focus)]">
          {body}
        </Link>
      ) : (
        <button
          type="button"
          data-result
          onClick={onPlay}
          disabled={busy || !onPlay}
          aria-label={`Play ${title}`}
          className="block w-full rounded-[var(--radius-card)] text-left focus-visible:outline-2 focus-visible:outline-[var(--site-accent-focus)]"
        >
          {body}
        </button>
      )}
      {to && onPlay ? overlay(
        <button
          type="button"
          onClick={onPlay}
          aria-label={`Play ${title}`}
          disabled={busy}
          className={`${disc} pointer-events-auto transition-opacity pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100`}
        >
          {busy ? <Loader2 size={18} className="animate-spin" aria-hidden /> : <Play size={18} aria-hidden />}
        </button>,
      ) : null}
      {menuItem ? (
        <span className="absolute right-1 top-1 rounded-xl bg-black/40 backdrop-blur-sm">
          <ItemContextMenu item={menuItem} />
        </span>
      ) : null}
      {!to && busy ? overlay(<span className={disc}><Loader2 size={18} className="animate-spin" aria-hidden /></span>) : null}
    </div>
  );
}
