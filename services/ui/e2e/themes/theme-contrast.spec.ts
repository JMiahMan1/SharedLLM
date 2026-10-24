import { test, expect, type Page, type Route } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { seedAuth } from '../fixtures/mediaMocks';
import pack from '../../src/themes/packs/jarvis-default.pack.json' with { type: 'json' };

/**
 * Every site theme must keep text readable and controls visible on every page.
 *
 * The interface is written with raw Tailwind colors (text-white, bg-black/20,
 * text-amber-300...) and index.css remaps them per theme. A remap that is
 * missing only shows up as unreadable text on one theme, which no unit test
 * sees, so this measures it: for each visible text element, the contrast of
 * its color against the background actually painted behind it.
 */

const OUT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../.tmp/theme-audit');
fs.mkdirSync(OUT_DIR, { recursive: true });

const THEMES = pack.themes.filter((t) => t.scope !== 'android_widget').map((t) => t.id);
const PAGES = [
  '/', '/settings', '/communication', '/notes', '/recipes', '/family', '/calendar', '/media',
  '/wander', '/fitness', '/bible', '/remote', '/knowledge', '/workspaces', '/docs', '/identity', '/admin',
];
/** WCAG AA for large text; anything under it is hard to read for everyone. */
const MIN_TEXT = 3;

async function mockApi(page: Page, themeId: string) {
  await page.route((url: URL) => url.pathname.startsWith('/execute/'), (route: Route) => route.fulfill({ json: { status: 'SUCCESS' } }));
  await page.route((url: URL) => url.pathname.startsWith('/api/'), (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/api/users/me') return route.fallback();
    if (p === '/api/users/me/theme') return route.fulfill({ json: { theme_id: themeId, packs: [] } });
    // Endpoints whose real response is an object with a list inside.
    if (p === '/api/geo/stars') return route.fulfill({ json: { user_id: 'admin', stars: 0, grants: [] } });
    if (p === '/api/storage/learning') return route.fulfill({ json: { items: [] } });
    // Collections are mostly `.map`ed straight away; an array is the safe default.
    return route.fulfill({ json: [] });
  });
}

interface Finding { page: string; text: string; ratio: number; fg: string; bg: string; cls: string }

/** Runs in the page: contrast of every visible text element against its painted background. */
function measure(minText: number): Finding[] {
  type RGBA = [number, number, number, number];
  // Computed colors come back as rgb(), oklch() or color-mix() results; a
  // canvas turns any of them into rgba.
  const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!;
  const parse = (c: string): RGBA | null => {
    const m = c.match(/(rgba?|oklch|oklab|color|lab|lch|hsla?)\([^)]*\)/);
    if (!m) return null;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = m[0];
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top: RGBA, under: RGBA): RGBA => {
    const a = top[3] + under[3] * (1 - top[3]);
    if (a === 0) return [0, 0, 0, 0];
    const mix = (i: number) => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a;
    return [mix(0), mix(1), mix(2), a];
  };
  const lum = (c: RGBA) => {
    const f = (x: number) => { const s = x / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const layer = (el: Element): RGBA | null => {
    const cs = getComputedStyle(el);
    const bg = parse(cs.backgroundColor);
    if (bg && bg[3] > 0) return bg;
    // Glass surfaces paint a gradient; its first stop stands in for the fill.
    const img = cs.backgroundImage;
    if (img && img.includes('gradient') && !img.includes('radial')) {
      const first = parse(img.slice(img.indexOf('(') + 1));
      if (first) return first;
    }
    return null;
  };
  const backdrop = (el: Element): RGBA => {
    const layers: RGBA[] = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const l = layer(e);
      if (l) { layers.push(l); if (l[3] >= 1) break; }
    }
    let base: RGBA = parse(getComputedStyle(document.body).backgroundColor) ?? [0, 0, 0, 1];
    if (base[3] < 1) base = over(base, [255, 255, 255, 1]);
    return layers.reverse().reduce((acc, l) => over(l, acc), base);
  };
  const out: Finding[] = [];
  const seen = new Set<string>();
  for (const el of Array.from(document.querySelectorAll('body *'))) {
    const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent!.trim().length > 1);
    if (!hasText) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || Number(cs.opacity) < 0.3) continue;
    let hidden = false;
    for (let e: Element | null = el; e; e = e.parentElement) {
      if (Number(getComputedStyle(e).opacity) < 0.3) { hidden = true; break; }
    }
    if (hidden) continue;
    // Gradient-clipped text (and transparent text inside it) paints the
    // clipping element's gradient; its first stop stands in for the color.
    let clipper: Element | null = null;
    for (let e: Element | null = el; e && !clipper; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.backgroundClip === 'text' || s.webkitBackgroundClip === 'text') clipper = e;
    }
    const fgRaw = clipper
      ? parse(getComputedStyle(clipper).backgroundImage.slice(getComputedStyle(clipper).backgroundImage.indexOf('(') + 1))
      : parse(cs.color);
    if (!fgRaw) continue;
    const bg = backdrop(clipper?.parentElement ?? el);
    const fg = over(fgRaw, bg);
    const a = lum(fg), b = lum(bg);
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio >= minText) continue;
    const text = (el.textContent || '').trim().slice(0, 40);
    const key = `${el.className}|${text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      page: location.pathname, text, ratio: Math.round(ratio * 100) / 100,
      fg: cs.color, bg: `rgb(${bg.slice(0, 3).map(Math.round).join(',')})`,
      cls: String(el.className).slice(0, 160),
    });
  }
  return out;
}

for (const themeId of THEMES) {
  test(`theme ${themeId} keeps text readable on every page`, async ({ page }, info) => {
    test.setTimeout(240_000);
    await seedAuth(page);
    await page.addInitScript((id) => localStorage.setItem('jarvis_site_theme_id', id), themeId);
    await mockApi(page, themeId);
    const findings: Finding[] = [];
    page.on('pageerror', (e) => console.log(`[pageerror ${themeId}] ${page.url()} ${e.message}`));
    for (const p of PAGES) {
      await page.goto(p, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction((id) => document.documentElement.getAttribute('data-theme-id') === id, themeId);
      await page.waitForTimeout(1200);
      findings.push(...(await page.evaluate(measure, MIN_TEXT)));
      await page.screenshot({ path: path.join(OUT_DIR, `${info.project.name}-${themeId}${p.replace(/\//g, '_') || '_home'}.png`), fullPage: false });
    }
    fs.writeFileSync(path.join(OUT_DIR, `${info.project.name}-${themeId}.json`), JSON.stringify(findings, null, 2));
    expect(findings, findings.map((f) => `${f.page} ${f.ratio} "${f.text}" ${f.fg} on ${f.bg} [${f.cls}]`).join('\n')).toEqual([]);
  });
}
