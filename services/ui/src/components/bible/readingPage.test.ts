import { describe, expect, it } from 'vitest';
import { PAGE_TONES, pageStyle, toneOf } from './readingPage';

/** Relative luminance and contrast, per WCAG 2.x. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('the reading page', () => {
  it('is opaque paper in every tone, never see-through glass', () => {
    for (const tone of Object.keys(PAGE_TONES)) {
      const style = pageStyle(tone) as Record<string, string>;
      expect(style.backgroundColor).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('keeps body text above WCAG AAA contrast (7:1) in every tone', () => {
    for (const t of Object.values(PAGE_TONES)) {
      expect(contrast(t.ink, t.paper)).toBeGreaterThan(7);
      expect(contrast(t.muted, t.paper)).toBeGreaterThan(4.5);
    }
  });

  it('falls back to the dark page for an unknown or missing tone', () => {
    expect(toneOf(undefined)).toBe('dark');
    expect(toneOf('glass')).toBe('dark');
  });
});
