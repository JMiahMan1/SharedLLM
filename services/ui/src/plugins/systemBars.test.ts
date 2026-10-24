import { describe, it, expect } from 'vitest';
import { cssColorToHex } from './systemBars';

describe('cssColorToHex', () => {
  it('turns the page background into a colour Android can paint', () => {
    expect(cssColorToHex('rgb(3, 7, 17)')).toBe('#030711');
    expect(cssColorToHex('rgba(255, 255, 255, 1)')).toBe('#ffffff');
    expect(cssColorToHex('rgb(3 7 17 / 0.9)')).toBe('#030711');
  });

  it('gives nothing for a transparent or unreadable colour', () => {
    expect(cssColorToHex('rgba(0, 0, 0, 0)')).toBeUndefined();
    expect(cssColorToHex('transparent')).toBeUndefined();
    expect(cssColorToHex('')).toBeUndefined();
  });
});
