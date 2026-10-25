import { useState } from 'react';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import { KIND_ICON, type ItemKind } from '../items';
import { imageDpr } from './image';

/**
 * Cover art through the signed image proxy, sized for where it is drawn
 * (`size` css px × device pixel ratio, BUG-11), loaded lazily and decoded off
 * the main thread. A `srcSet`/`sizes` pair (P7-T5) lets the browser pick the
 * 1x or 2x candidate, so a phone does not download a desktop cover and a 1x
 * screen does not download a 2x one. Without art, or when it fails, a gradient
 * with the kind's icon stands in; artists are round (UX-28).
 */
export function Artwork({
  image, kind, size, className = '', rounded, fill, sizes,
}: {
  image?: string | null;
  kind: ItemKind;
  /** Rendered width in css px (the widest it gets when `fill`). */
  size: number;
  className?: string;
  rounded?: 'full' | 'card';
  /** Fill the parent's width as a square instead of a fixed size. */
  fill?: boolean;
  /** The `sizes` hint for the candidate list; defaults to the fixed `size`. */
  sizes?: string;
}) {
  useMediaToken(); // re-render once the URL can be signed
  // The drawn width and its DPR double, deduped so a 1x screen asks for one.
  const widths = [Math.round(size), Math.round(size * imageDpr())]
    .filter((w, i, all) => all.indexOf(w) === i);
  const candidates = widths
    .map((w) => ({ w, url: proxiedImage(image, w) }))
    .filter((c): c is { w: number; url: string } => Boolean(c.url));
  const src = candidates.at(-1)?.url ?? null;
  const srcSet = candidates.length > 1 ? candidates.map((c) => `${c.url} ${c.w}w`).join(', ') : undefined;
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
          srcSet={srcSet}
          sizes={sizes ?? `${Math.round(size)}px`}
          alt=""
          loading="lazy"
          decoding="async"
          width={Math.round(size)}
          height={Math.round(size)}
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
