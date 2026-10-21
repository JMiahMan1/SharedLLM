import { describe, expect, it } from 'vitest';
import { FALLBACK_ACCENT, pickAccentColor } from './useAccentColor';

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

describe('pickAccentColor', () => {
  it('picks a saturated colour and ignores the dark border of a cover', () => {
    const colour = pickAccentColor(pixels([
      [8, 8, 8, 255],
      [12, 10, 14, 255],
      [220, 40, 60, 255],
      [200, 30, 50, 255],
    ]));
    const [r, g, b] = colour.match(/\d+/g)!.map(Number);
    expect(r).toBeGreaterThan(g);
    expect(r).toBeGreaterThan(b);
    expect(r).toBeGreaterThanOrEqual(170);
  });

  it('lifts a dark saturated colour so it stays visible on the scrim', () => {
    const colour = pickAccentColor(pixels([[20, 40, 90, 255]]));
    const [r, g, b] = colour.match(/\d+/g)!.map(Number);
    expect(Math.max(r, g, b)).toBeGreaterThanOrEqual(170);
    expect(b).toBeGreaterThan(r);
  });

  it('falls back to the house accent for a black-and-white cover', () => {
    expect(pickAccentColor(pixels([
      [0, 0, 0, 255],
      [120, 120, 120, 255],
      [240, 240, 240, 255],
    ]))).toBe(FALLBACK_ACCENT);
  });

  it('ignores transparent pixels', () => {
    expect(pickAccentColor(pixels([[255, 0, 0, 0]]))).toBe(FALLBACK_ACCENT);
  });
});
