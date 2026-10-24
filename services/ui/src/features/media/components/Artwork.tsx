import { useState } from 'react';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import { KIND_ICON, type ItemKind } from '../items';

/** The device pixel ratio, capped so a 3x phone does not fetch 4x covers. */
function dpr(): number {
  return typeof window === 'undefined' ? 1 : Math.min(2, Math.max(1, window.devicePixelRatio || 1));
}

/**
 * Cover art through the signed image proxy, sized for where it is drawn
 * (`size` css px × device pixel ratio, BUG-11), loaded lazily and decoded off
 * the main thread. Without art, or when it fails, a gradient with the kind's
 * icon stands in; artists are round (UX-28).
 */
export function Artwork({
  image, kind, size, className = '', rounded, fill,
}: {
  image?: string | null;
  kind: ItemKind;
  /** Rendered width in css px (the widest it gets when `fill`). */
  size: number;
  className?: string;
  rounded?: 'full' | 'card';
  /** Fill the parent's width as a square instead of a fixed size. */
  fill?: boolean;
}) {
  useMediaToken(); // re-render once the URL can be signed
  const src = proxiedImage(image, Math.round(size * dpr()));
  const [failed, setFailed] = useState<string | null>(null);
  const shape = (rounded ?? (kind === 'artist' || kind === 'author' ? 'full' : 'card')) === 'full'
    ? 'rounded-full'
    : 'rounded-[var(--radius-card)]';
  const Icon = KIND_ICON[kind];
  const showImage = src && failed !== src;
  return (
    <span
      className={`relative block shrink-0 overflow-hidden bg-gradient-to-br from-[var(--site-accent-soft)] to-[var(--color-surface-1)] ${shape} ${fill ? 'aspect-square w-full' : ''} ${className}`}
      style={fill ? undefined : { width: size, height: size }}
    >
      {showImage ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          width={size}
          height={size}
          onError={() => setFailed(src)}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="absolute inset-0 flex items-center justify-center text-[var(--site-accent-text)]" aria-hidden>
          <Icon size={Math.max(16, Math.round(size / 3))} />
        </span>
      )}
    </span>
  );
}
