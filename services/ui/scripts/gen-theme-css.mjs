#!/usr/bin/env node
/**
 * Generates src/themes/theme-remap.css.
 *
 * The interface is written with raw Tailwind colors for the original dark
 * purple look: purple/indigo highlights, dark neutral surfaces
 * (bg-slate-900/80, bg-black/30), pale accent text (text-amber-300) and white
 * text. A theme only restyles what is mapped onto its tokens, so this maps
 * every such class the source actually uses:
 *
 * - For every theme, purple, violet and indigo become the theme's accent.
 * - For dark themes, text too dim to read (text-slate-600, text-red-400/60)
 *   is raised to the theme's muted text or a stronger tint.
 * - For light themes, dark surfaces become the theme's surface, pale text
 *   becomes a darker shade of the same hue, and white text on a solid fill
 *   (a red SOS button) stays white.
 *
 * Run `npm run gen:theme-css` after adding color classes; themeRemap.test.ts
 * runs this with --check and fails when the committed file is out of date.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const UI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const OUT = path.join(UI, 'src/themes/theme-remap.css');

const NEUTRAL = ['slate', 'gray', 'zinc', 'neutral', 'stone'];
const ACCENT = ['purple', 'violet', 'indigo'];
const CHROMA = ['red', 'orange', 'amber', 'yellow', 'lime', 'green', 'emerald', 'teal', 'cyan', 'sky', 'blue',
  'fuchsia', 'pink', 'rose'];
const COLORS = [...NEUTRAL, ...ACCENT, ...CHROMA, 'white', 'black'];
/** Variants whose selector we can express; responsive and other variants are left alone. */
const STATES = { '': '', 'hover:': ':hover', 'focus:': ':focus', 'active:': ':active' };
const ANY = '[data-site-theme-active]';
const LIGHT = '[data-theme-scheme="light"]';
const DARK = '[data-theme-scheme="dark"]';
const WHITE_TEXT = ['.text-white', '.text-slate-50', '.text-slate-100', '.text-slate-200'];

function palette() {
  const css = fs.readFileSync(path.join(UI, 'node_modules/tailwindcss/theme.css'), 'utf8');
  const out = {};
  for (const m of css.matchAll(/--color-([a-z]+)-(\d+):\s*([^;]+);/g)) (out[m[1]] ??= {})[m[2]] = m[3].trim();
  return out;
}

export function sourceFiles(dir = path.join(UI, 'src')) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === 'test' ? [] : sourceFiles(p);
    return /\.(tsx?|jsx?)$/.test(e.name) && !/\.test\./.test(e.name) ? [p] : [];
  });
}

const CLASS_RE = new RegExp(
  String.raw`(?<![\w:-])((?:hover:|focus:|active:)?)(text|bg|border|from|via|to)-(${COLORS.join('|')})(?:-(\d{2,3}))?(?:\/(\d{1,3}|\[[\d.]+\]))?(?![\w\-/\[])`,
  'g',
);

/** Arbitrary hex classes (bg-[#0c1120]): only near-black and near-white matter. */
const HEX_RE = /(?<![\w:-])((?:hover:|focus:|active:)?)(text|bg|border)-\[#([0-9a-fA-F]{6})\](?![\w\-/\[])/g;

function hexAsNeutral(hex) {
  const lin = (i) => { const v = parseInt(hex.slice(i, i + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = 0.2126 * lin(0) + 0.7152 * lin(2) + 0.0722 * lin(4);
  return lum < 0.05 ? 950 : lum > 0.6 ? 100 : null;
}

function usedClasses(files) {
  const found = new Map();
  for (const f of files) {
    const src = fs.readFileSync(f, 'utf8');
    for (const [cls, state, kind, hex] of src.matchAll(HEX_RE)) {
      const shade = hexAsNeutral(hex);
      if (shade !== null) found.set(cls, { cls, state, kind, color: 'slate', shade, alpha: 1 });
    }
    for (const m of src.matchAll(CLASS_RE)) {
      const [cls, state, kind, color, shade, alpha] = m;
      const bare = color === 'white' || color === 'black';
      if (bare === Boolean(shade)) continue; // bg-white-500 or bg-slate are not classes
      const a = alpha === undefined ? 1 : alpha.startsWith('[') ? Number(alpha.slice(1, -1)) : Number(alpha) / 100;
      found.set(cls, { cls, state, kind, color, shade: shade ? Number(shade) : null, alpha: a });
    }
  }
  return [...found.values()].sort((x, y) => x.cls.localeCompare(y.cls));
}

const esc = (s) => s.replace(/([:/[\].#])/g, '\\$1');
const pct = (a) => `${Math.round(a * 1000) / 10}%`;
const mix = (color, a) => (a >= 1 ? color : `color-mix(in srgb, ${color} ${pct(a)}, transparent)`);
const solidAccent = (c) => ACCENT.includes(c.color) && (c.kind === 'bg' || c.kind === 'from') && c.state === '' && c.alpha >= 0.8 &&
  c.shade >= 500 && c.shade <= 700;

/** Accent classes, for every theme. */
function accentValue(c) {
  const { kind, shade, alpha } = c;
  if (kind === 'text') return mix(shade <= 400 ? 'var(--site-accent-text)' : 'var(--site-accent)', alpha);
  if (kind === 'border') return mix('var(--site-accent)', Math.max(alpha, 0.3));
  if (shade >= 800) return mix('var(--site-accent)', Math.min(alpha, 0.2));
  return mix('var(--site-accent)', alpha);
}

/** Light-theme value for one non-accent class, or null to leave it as authored. */
function lightValue(c, pal) {
  const { kind, color, shade, alpha } = c;
  const neutral = NEUTRAL.includes(color);
  const darkNeutral = color === 'black' || (neutral && shade >= 600);
  if (kind === 'text') {
    if (color === 'white') return mix('var(--site-text)', alpha);
    if (neutral && shade <= 300) return mix('var(--site-text)', alpha);
    if (neutral && shade <= 500) return mix('var(--site-text-muted)', alpha);
    if (CHROMA.includes(color) && shade <= 500) return mix(pal[color]['700'], alpha);
    return null;
  }
  if (kind === 'bg') {
    // Solid black is a deliberate dark island (a video, a terminal trace); it
    // stays dark and keeps its white text (see keepsWhiteText).
    if (color === 'black' && alpha >= 1) return null;
    if (darkNeutral) return alpha >= 0.6 ? mix('var(--ht-surface)', alpha) : 'var(--site-card-bg)';
    if (color === 'white') return `rgba(17, 24, 39, ${Math.min(0.12, Math.round(alpha * 60) / 100)})`;
    if (CHROMA.includes(color) && shade >= 800) return mix(pal[color]['100'], Math.max(alpha, 0.6));
    return null;
  }
  if (kind === 'border') {
    if (darkNeutral || color === 'white') return 'var(--color-border-soft)';
    if (CHROMA.includes(color) && shade >= 800) return pal[color]['200'];
    return null;
  }
  // Gradient stops: Tailwind composes them from these variables.
  if (darkNeutral) return mix('var(--ht-surface)', Math.max(alpha, 0.6));
  if (CHROMA.includes(color) && shade >= 800) return mix(pal[color]['100'], Math.max(alpha, 0.6));
  return null;
}

/** Dark-theme value: only text too dim to read on a dark surface. */
function darkValue(c, pal) {
  const { kind, color, shade, alpha } = c;
  if (kind !== 'text') return null;
  if (NEUTRAL.includes(color) && shade >= 600 && shade <= 800) return mix('var(--site-text-muted)', alpha);
  if (CHROMA.includes(color) && shade <= 500 && alpha < 0.8) return mix(pal[color][String(shade)], 0.85);
  return null;
}

const PROP = {
  text: 'color', bg: 'background-color', border: 'border-color',
  from: '--tw-gradient-from', via: '--tw-gradient-via', to: '--tw-gradient-to',
};

/** Fills that keep white text on a light theme: a solid red button, a black video frame. */
function keepsWhiteText(c) {
  if (c.state !== '' || c.alpha < 0.8) return false;
  if (c.kind === 'bg' && c.color === 'black') return c.alpha >= 1;
  return CHROMA.includes(c.color) && c.shade >= 500 && c.shade <= 700 && (c.kind === 'bg' || c.kind === 'from');
}

function rule(scope, c, value) {
  const important = PROP[c.kind].startsWith('--') ? '' : ' !important';
  return `${scope} .${esc(c.cls)}${STATES[c.state]} { ${PROP[c.kind]}: ${value}${important}; }`;
}

/** Text on a fill must not take the light remap: the fill itself and anything inside it. */
function textOn(scope, fills, value) {
  if (!fills.length) return '';
  const f = fills.join(', ');
  const t = WHITE_TEXT.join(', ');
  return `${scope} :is(${f}):is(${t}),\n${scope} :is(${f}) :is(${t}) { color: ${value} !important; }`;
}

export function generate(files = sourceFiles()) {
  const pal = palette();
  const classes = usedClasses(files);
  const accent = classes.filter((c) => ACCENT.includes(c.color)).map((c) => rule(ANY, c, accentValue(c)));
  const light = classes
    .filter((c) => !ACCENT.includes(c.color))
    .map((c) => [c, lightValue(c, pal)])
    .filter(([, v]) => v !== null)
    .map(([c, v]) => rule(LIGHT, c, v));
  const dark = classes
    .filter((c) => !ACCENT.includes(c.color))
    .map((c) => [c, darkValue(c, pal)])
    .filter(([, v]) => v !== null)
    .map(([c, v]) => rule(DARK, c, v));
  const accentFills = classes.filter(solidAccent).map((c) => `.${esc(c.cls)}`);
  const whiteFills = classes.filter(keepsWhiteText).map((c) => `.${esc(c.cls)}`);
  return [
    '/* Generated by scripts/gen-theme-css.mjs from the color classes used in src.',
    '   Do not edit by hand; run `npm run gen:theme-css`. */',
    '',
    '/* Every theme: the original purple and indigo follow the theme accent. */',
    ...accent,
    accentFills.length ? `${ANY} :is(${accentFills.join(', ')}) { color: var(--site-on-accent) !important; }` : '',
    textOn(ANY, accentFills, 'var(--site-on-accent)'),
    '',
    '/* Dark themes: text too dim to read on a dark surface. */',
    ...dark,
    '',
    '/* Light themes: dark surfaces, pale text and white text follow the theme. */',
    ...light,
    textOn(LIGHT, whiteFills, '#fff'),
    textOn(LIGHT, accentFills, 'var(--site-on-accent)'),
    '',
  ].join('\n');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const css = generate();
  if (process.argv.includes('--check')) {
    const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
    if (current !== css) {
      console.error(`${path.relative(UI, OUT)} is out of date; run npm run gen:theme-css`);
      process.exit(1);
    }
  } else {
    fs.writeFileSync(OUT, css);
    console.log(`wrote ${path.relative(UI, OUT)}`);
  }
}
