import { useEffect, useState } from 'react';

/** Jarvis cyan, used when the artwork cannot be read (missing, cross-origin, or grey). */
export const FALLBACK_ACCENT = '#22d3ee';

/**
 * Picks a vivid, readable accent out of RGBA pixels.
 *
 * Averaging every pixel gives mud: most covers are dark and neutral, so the
 * mean is a grey-brown. We keep only saturated, mid-or-bright pixels, average
 * those, then lift the result so it stays legible on the dark scrim. Covers
 * with no colour at all (typographic black-and-white albums) fall back to the
 * house cyan rather than an invisible grey.
 */
export function pickAccentColor(pixels: Uint8ClampedArray): string {
  let r = 0;
  let g = 0;
  let b = 0;
  let n = 0;

  for (let i = 0; i < pixels.length; i += 4) {
    if (pixels[i + 3] < 128) continue;
    const pr = pixels[i];
    const pg = pixels[i + 1];
    const pb = pixels[i + 2];
    const max = Math.max(pr, pg, pb);
    const min = Math.min(pr, pg, pb);
    if (max < 40) continue;
    const saturation = max === 0 ? 0 : (max - min) / max;
    if (saturation < 0.25) continue;
    r += pr;
    g += pg;
    b += pb;
    n += 1;
  }

  if (n === 0) return FALLBACK_ACCENT;

  r = Math.round(r / n);
  g = Math.round(g / n);
  b = Math.round(b / n);

  // Lift dark art so the accent is visible against the near-black scrim.
  const brightest = Math.max(r, g, b);
  if (brightest < 170) {
    const scale = 170 / Math.max(1, brightest);
    r = Math.min(255, Math.round(r * scale));
    g = Math.min(255, Math.round(g * scale));
    b = Math.min(255, Math.round(b * scale));
  }

  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * The dominant accent colour of an image URL.
 *
 * Artwork is served through the gateway's image proxy, so it is same-origin
 * and the canvas is readable. If a provider hands us a direct cross-origin
 * URL, the draw throws and we keep the fallback -- never an invisible player.
 * The colour is stored with the URL it came from, so a track change shows the
 * house accent immediately instead of the previous cover's colour.
 */
export function useAccentColor(src?: string | null): string {
  const [extracted, setExtracted] = useState<{ src: string; color: string } | null>(null);

  useEffect(() => {
    if (!src) return;

    let cancelled = false;
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 32;
        canvas.height = 32;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        if (!context) return;
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
        if (!cancelled) setExtracted({ src, color: pickAccentColor(data) });
      } catch {
        /* Cross-origin artwork: keep the house accent. */
      }
    };
    image.src = src;

    return () => {
      cancelled = true;
    };
  }, [src]);

  return src && extracted?.src === src ? extracted.color : FALLBACK_ACCENT;
}
