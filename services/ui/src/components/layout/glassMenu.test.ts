import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * The profile menu is a floating surface over arbitrary page content. When it
 * used .glass-panel (7% white) the page showed straight through it and the
 * menu items were hard to read.
 *
 * These assert the *token values*, not just that a class name is present --
 * a test that only greps for "glass-menu" would still pass if someone later
 * pointed --site-menu-a back at 0.07.
 */
const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');

function token(name: string): string {
  const dark = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (!dark) throw new Error(`--${name} not found in index.css`);
  return dark[1].trim();
}

function alphaOf(rgba: string): number {
  const m = rgba.match(/rgba?\([^)]*?([\d.]+)\s*\)$/);
  if (!m) throw new Error(`Not an rgba() colour: ${rgba}`);
  return parseFloat(m[1]);
}

describe('floating menu surface', () => {
  it('defines near-opaque menu tokens', () => {
    for (const name of ['site-menu-a', 'site-menu-b']) {
      expect(alphaOf(token(name))).toBeGreaterThan(0.85);
    }
  });

  it('is far more opaque than a panel, which is why panels cannot be reused', () => {
    expect(alphaOf(token('site-menu-a'))).toBeGreaterThan(alphaOf(token('site-panel-a')) * 5);
  });

  it('overrides the menu tokens for the light theme', () => {
    // A near-opaque dark menu on a light page reads as a black rectangle.
    const lightBlock = css.slice(css.indexOf('[data-theme-scheme="light"]', css.indexOf('--site-menu-a')));
    const lightMenuA = lightBlock.match(/--site-menu-a:\s*([^;]+);/);
    expect(lightMenuA).toBeTruthy();
    const [r, g, b] = (lightMenuA![1].match(/[\d.]+/g) ?? []).map(Number);
    // Light variant is genuinely light, not just opaque.
    expect((r! + g! + b!) / 3).toBeGreaterThan(200);
  });

  it('the floating surfaces use it and the inline status chip does not', () => {
    const header = readFileSync(resolve(__dirname, 'Header.tsx'), 'utf8');
    // The profile menu, the search results and the notifications panel float
    // over content.
    expect(header.match(/glass-menu/g)?.length).toBe(3);
    // The Pulse chip sits inline in the header flow; a panel is correct there.
    expect(header).toContain('glass-panel neon-border');
  });

  it('keeps a touch of blur so it still reads as part of the design', () => {
    const rule = css.match(/\.glass-menu\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(rule).toMatch(/backdrop-filter:\s*blur/);
  });

  /**
   * The menu is drawn over the *active theme's* surfaces, so its own colours
   * have to agree with whatever --site-text the theme supplies. Every theme
   * writes those tokens as inline styles on <html>, which outrank the
   * stylesheet -- so the menu must be opaque in both schemes and must not
   * hardcode a text colour that fights the theme.
   */
  it('defines a light-scheme menu, because panels are only 7% white', () => {
    // Without this the menu stays dark on a light page: a black rectangle.
    expect(css).toMatch(/--site-menu-a:\s*rgba\(255, 255, 255/);
  });

  it('does not hardcode the menu label colour', () => {
    // Raw text-slate-200 has no light-scheme remap, so the label vanished on a
    // light menu. The rows must read from the theme token instead.
    const header = readFileSync(resolve(__dirname, 'Header.tsx'), 'utf8');
    const menu = header.slice(header.indexOf('glass-menu'));
    expect(menu).not.toMatch(/text-slate-200/);
    expect(menu).toMatch(/text-\[var\(--site-text\)\]/);
  });

  it('keeps the logout row red in both schemes', () => {
    // Log out is the one destructive action in the menu; it must not lose its
    // warning colour when the surface flips to light.
    const header = readFileSync(resolve(__dirname, 'Header.tsx'), 'utf8');
    const row = header.slice(header.indexOf('profile-menu-logout'));
    expect(row).toMatch(/text-rose-300/);
  });
});
