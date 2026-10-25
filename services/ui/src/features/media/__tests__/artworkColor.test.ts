import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import {
  accentStyle, clearAccentCache, contrastRatio, DARK_SURFACE, ensureContrast, MIN_CONTRAST, pickAccentColor,
  relativeLuminance, useArtworkAccent, type Rgb,
} from '../engine/artworkColor';
import { ensureMediaToken, resetMediaTokenForTests } from '../../../lib/mediaToken';

function pixels(colors: Array<[number, number, number, number]>): Uint8ClampedArray {
  const data = new Uint8ClampedArray(colors.length * 4);
  colors.forEach(([r, g, b, a], i) => {
    data[i * 4] = r;
    data[i * 4 + 1] = g;
    data[i * 4 + 2] = b;
    data[i * 4 + 3] = a;
  });
  return data;
}

describe('contrastRatio (P6-T2)', () => {
  it('matches the WCAG reference values', () => {
    expect(contrastRatio([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 1);
    expect(contrastRatio([255, 255, 255], [255, 255, 255])).toBe(1);
    // #767676 on white is the classic 4.54:1.
    expect(contrastRatio([118, 118, 118], [255, 255, 255])).toBeCloseTo(4.54, 1);
    expect(relativeLuminance([255, 255, 255])).toBe(1);
    expect(relativeLuminance([0, 0, 0])).toBe(0);
  });

  it('is symmetric', () => {
    expect(contrastRatio([10, 200, 30], DARK_SURFACE)).toBe(contrastRatio(DARK_SURFACE, [10, 200, 30]));
  });
});

describe('ensureContrast', () => {
  it('lightens a dark colour until it reads on the dark surface, keeping its hue', () => {
    const navy: Rgb = [20, 40, 90];
    const fixed = ensureContrast(navy);
    expect(contrastRatio(fixed, DARK_SURFACE)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(fixed[2]).toBeGreaterThan(fixed[0]); // still blue
  });

  it('darkens on a light surface, and leaves a readable colour alone', () => {
    const white: Rgb = [255, 255, 255];
    const fixed = ensureContrast([255, 220, 120], white);
    expect(contrastRatio(fixed, white)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(ensureContrast([250, 250, 250])).toEqual([250, 250, 250]);
  });

  it('handles a grey and an impossible request', () => {
    expect(contrastRatio(ensureContrast([30, 30, 30]), DARK_SURFACE)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(ensureContrast([30, 30, 30], DARK_SURFACE, 30)).toEqual([255, 255, 255]);
    expect(ensureContrast([200, 200, 200], [255, 255, 255], 30)).toEqual([0, 0, 0]);
  });
});

describe('pickAccentColor', () => {
  it('picks a saturated colour, ignores the dark border, and is readable', () => {
    const colour = pickAccentColor(pixels([
      [8, 8, 8, 255],
      [12, 10, 14, 255],
      [220, 40, 60, 255],
      [200, 30, 50, 255],
    ]))!;
    expect(colour[0]).toBeGreaterThan(colour[1]);
    expect(colour[0]).toBeGreaterThan(colour[2]);
    expect(contrastRatio(colour, DARK_SURFACE)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it('gives nothing for a black-and-white cover or transparent pixels, so the theme accent shows', () => {
    expect(pickAccentColor(pixels([[0, 0, 0, 255], [120, 120, 120, 255], [240, 240, 240, 255]]))).toBeNull();
    expect(pickAccentColor(pixels([[255, 0, 0, 0]]))).toBeNull();
  });
});

describe('accent CSS variables', () => {
  it('sets the cover colour, or falls back to the theme accent', () => {
    expect(accentStyle([10, 20, 30])).toEqual({ '--media-accent': 'rgb(10, 20, 30)', '--media-accent-soft': 'rgba(10, 20, 30, 0.18)' });
    expect(accentStyle(null)).toEqual({ '--media-accent': 'var(--site-accent-text)', '--media-accent-soft': 'var(--site-accent-soft)' });
  });
});

describe('useArtworkAccent', () => {
  const drawn = vi.fn();
  beforeEach(async () => {
    clearAccentCache();
    resetMediaTokenForTests();
    localStorage.setItem('jarvis_api_key', 'k');
    await ensureMediaToken();
    // An image that "loads" at once, and a canvas that returns red pixels.
    vi.stubGlobal('Image', class {
      onload: (() => void) | null = null;
      crossOrigin = '';
      set src(_v: string) {
        drawn();
        queueMicrotask(() => this.onload?.());
      }
    });
    vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
      if (tag !== 'canvas') return document.createElementNS('http://www.w3.org/1999/xhtml', tag);
      return {
        width: 0,
        height: 0,
        getContext: () => ({
          drawImage: () => {},
          getImageData: () => ({ data: pixels([[220, 30, 40, 255]]) }),
        }),
      };
    }) as typeof document.createElement);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    drawn.mockReset();
  });

  it('reads the cover once, then answers from the cache', async () => {
    const { result } = renderHook(() => useArtworkAccent('http://ma/img/1'));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current![0]).toBeGreaterThan(result.current![1]);
    const again = renderHook(() => useArtworkAccent('http://ma/img/1'));
    expect(again.result.current).toEqual(result.current);
    expect(drawn).toHaveBeenCalledTimes(1);
  });

  it('has nothing without a cover', () => {
    const { result } = renderHook(() => useArtworkAccent(null));
    expect(result.current).toBeNull();
  });
});
