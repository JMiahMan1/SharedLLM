import { useEffect, useState, type CSSProperties } from 'react';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';

/**
 * The artwork accent (§6.5): a vivid colour from the cover, made readable on
 * the dark player surfaces, exposed as `--media-accent` / `--media-accent-soft`
 * on the player roots. Without a usable cover the theme's own accent is used.
 */

export type Rgb = [number, number, number];

/** The near-black the Now Playing scrim and the dark themes sit on. */
export const DARK_SURFACE: Rgb = [11, 17, 32];
/** WCAG AA for normal text. */
export const MIN_CONTRAST = 4.5;
export const SAMPLE_SIZE = 96;
const CACHE_LIMIT = 100;

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance, 0 (black) to 1 (white). */
export function relativeLuminance([r, g, b]: Rgb): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio between two colours, 1 to 21. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function toHsl([r, g, b]: Rgb): [number, number, number] {
  const rn = r / 255, gn = g / 255, bn = b / 255;
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === rn ? (gn - bn) / d + (gn < bn ? 6 : 0) : max === gn ? (bn - rn) / d + 2 : (rn - gn) / d + 4;
  return [h / 6, s, l];
}

function toRgb([h, s, l]: [number, number, number]): Rgb {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const hue = (p: number, q: number, t: number) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [hue(p, q, h + 1 / 3), hue(p, q, h), hue(p, q, h - 1 / 3)].map((v) => Math.round(v * 255)) as Rgb;
}

/**
 * Raise (or, on a light surface, lower) the colour's HSL lightness until it
 * reaches `min` contrast against `surface`, keeping its hue.
 */
export function ensureContrast(color: Rgb, surface: Rgb = DARK_SURFACE, min = MIN_CONTRAST): Rgb {
  if (contrastRatio(color, surface) >= min) return color;
  const [h, s, l] = toHsl(color);
  const lighter = relativeLuminance(surface) < 0.5;
  for (let step = 1; step <= 100; step += 1) {
    const next = toRgb([h, s, Math.max(0, Math.min(1, lighter ? l + step / 100 : l - step / 100))]);
    if (contrastRatio(next, surface) >= min) return next;
  }
  return lighter ? [255, 255, 255] : [0, 0, 0];
}

/**
 * A vivid, readable colour from RGBA pixels, or null for a cover with no
 * colour (typographic black-and-white art). Averaging every pixel gives mud,
 * so only saturated, not-too-dark pixels count.
 */
export function pickAccentColor(pixels: Uint8ClampedArray, surface: Rgb = DARK_SURFACE): Rgb | null {
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    if (pixels[i + 3] < 128) continue;
    const pr = pixels[i], pg = pixels[i + 1], pb = pixels[i + 2];
    const max = Math.max(pr, pg, pb);
    const min = Math.min(pr, pg, pb);
    if (max < 40) continue;
    if ((max - min) / max < 0.25) continue;
    r += pr;
    g += pg;
    b += pb;
    n += 1;
  }
  if (n === 0) return null;
  return ensureContrast([Math.round(r / n), Math.round(g / n), Math.round(b / n)], surface);
}

const cache = new Map<string, Rgb | null>();

function remember(key: string, value: Rgb | null) {
  if (cache.size >= CACHE_LIMIT) cache.delete(cache.keys().next().value as string);
  cache.set(key, value);
}

/** Tests only. */
export function clearAccentCache(): void {
  cache.clear();
}

/**
 * The accent of a cover (its raw upstream path; the small proxied copy is
 * sampled, same-origin so the canvas can read it). Null until known, and for
 * covers with no colour, so callers fall back to the theme accent.
 */
export function useArtworkAccent(rawImage?: string | null): Rgb | null {
  useMediaToken(); // the sample URL is signed
  const src = proxiedImage(rawImage, SAMPLE_SIZE);
  const [extracted, setExtracted] = useState<{ src: string; color: Rgb | null } | null>(null);

  useEffect(() => {
    if (!src || cache.has(src)) return;
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
        const color = pickAccentColor(context.getImageData(0, 0, canvas.width, canvas.height).data);
        remember(src, color);
        if (!cancelled) setExtracted({ src, color });
      } catch {
        /* A cross-origin cover cannot be read: keep the theme accent. */
      }
    };
    image.src = src;
    return () => {
      cancelled = true;
    };
  }, [src]);

  if (!src) return null;
  if (cache.has(src)) return cache.get(src) ?? null;
  return extracted?.src === src ? extracted.color : null;
}

/** `--media-accent` and `--media-accent-soft` for a player root. */
export function accentStyle(color: Rgb | null): CSSProperties {
  return color
    ? ({ '--media-accent': `rgb(${color.join(', ')})`, '--media-accent-soft': `rgba(${color.join(', ')}, 0.18)` } as CSSProperties)
    : ({ '--media-accent': 'var(--site-accent-text)', '--media-accent-soft': 'var(--site-accent-soft)' } as CSSProperties);
}
