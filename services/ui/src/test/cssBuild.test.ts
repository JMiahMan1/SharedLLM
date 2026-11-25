import { readdirSync, readFileSync, statSync } from 'fs';
import path from 'path';
import { optimize } from '@tailwindcss/node';
import { describe, expect, it } from 'vitest';

/**
 * The production build runs every stylesheet through Tailwind's optimizer
 * (lightningcss); the dev server does not. lightningcss drops an unprefixed
 * declaration that is followed by its -webkit- twin, so `backdrop-filter`
 * then `-webkit-backdrop-filter` shipped only the prefixed one: Chrome and the
 * Android WebView got no blur, and a .glass-panel stopped being the containing
 * block for its absolute children. Every prefixed pair in our own CSS must
 * keep its standard property through the same optimizer the build uses.
 */

const SRC = path.resolve(__dirname, '..');

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return cssFiles(full);
    return name.endsWith('.css') ? [full] : [];
  });
}

/** Innermost rule bodies (no nested braces) with a -webkit- declaration and its standard twin. */
function prefixedPairs(css: string): { prop: string; body: string }[] {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const pairs: { prop: string; body: string }[] = [];
  for (const m of noComments.matchAll(/\{([^{}]*)\}/g)) {
    const body = m[1];
    for (const p of body.matchAll(/(?:^|[;\s])-webkit-([a-z-]+)\s*:/g)) {
      const prop = p[1];
      if (new RegExp(`(?:^|[;\\s])${prop}\\s*:`).test(body)) pairs.push({ prop, body: body.trim() });
    }
  }
  return pairs;
}

describe('our CSS survives the production optimizer', () => {
  const files = cssFiles(SRC);

  it('finds the stylesheets, including the backdrop-filter pairs in index.css', () => {
    const index = files.find((f) => f.endsWith(`${path.sep}index.css`));
    expect(index).toBeTruthy();
    expect(prefixedPairs(readFileSync(index!, 'utf8')).some((p) => p.prop === 'backdrop-filter')).toBe(true);
  });

  for (const file of files) {
    const pairs = prefixedPairs(readFileSync(file, 'utf8'));
    if (!pairs.length) continue;
    it(`${path.relative(SRC, file)}: every -webkit- pair keeps its standard property`, () => {
      const lost = pairs.filter(({ prop, body }) => {
        const out = optimize(`.t{${body}}`, { minify: true }).code;
        return !new RegExp(`[{;]${prop}:`).test(out);
      });
      expect(lost.map((l) => `${l.prop} in { ${l.body.slice(0, 80)} }`), 'write the -webkit- declaration first').toEqual([]);
    });
  }
});
