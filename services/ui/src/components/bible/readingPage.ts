import type { CSSProperties } from 'react';

/**
 * The paper Scripture is set on.
 *
 * The text used to sit on the app's frosted-glass panels, which are 7% white:
 * whatever was behind the page (wallpaper, gradients, the dashboard) showed
 * through the words. A reading surface is opaque, always. The three tones are
 * the ones e-readers settle on: a dark page for night, sepia for long reading,
 * and a light page for daylight. Ink is a soft off-black or off-white rather
 * than pure #000/#fff, which keeps contrast well above WCAG AAA (7:1) without
 * the halation of pure white on black.
 */
export type PageTone = 'dark' | 'sepia' | 'light';

interface Tone {
  label: string;
  paper: string;
  ink: string;
  muted: string;
  number: string;
  rule: string;
  hover: string;
}

export const PAGE_TONES: Record<PageTone, Tone> = {
  dark: {
    label: 'Dark',
    paper: '#14171d',
    ink: '#ebe7de',
    muted: '#a8a296',
    number: '#c79a4e',
    rule: 'rgba(235, 231, 222, 0.12)',
    hover: 'rgba(235, 231, 222, 0.05)',
  },
  sepia: {
    label: 'Sepia',
    paper: '#f4ecd8',
    ink: '#3a2e1f',
    muted: '#6e5d45',
    number: '#9a5b14',
    rule: 'rgba(58, 46, 31, 0.15)',
    hover: 'rgba(58, 46, 31, 0.05)',
  },
  light: {
    label: 'Light',
    paper: '#fdfcf9',
    ink: '#1d1d1f',
    muted: '#5b5b60',
    number: '#a25a0a',
    rule: 'rgba(29, 29, 31, 0.12)',
    hover: 'rgba(29, 29, 31, 0.04)',
  },
};

export const PAGE_TONE_ORDER: PageTone[] = ['dark', 'sepia', 'light'];

export function toneOf(value: string | undefined): PageTone {
  return value === 'sepia' || value === 'light' ? value : 'dark';
}

/** CSS variables for a page; children use `var(--page-ink)` and friends. */
export function pageStyle(value: string | undefined): CSSProperties {
  const t = PAGE_TONES[toneOf(value)];
  return {
    '--page-paper': t.paper,
    '--page-ink': t.ink,
    '--page-muted': t.muted,
    '--page-number': t.number,
    '--page-rule': t.rule,
    '--page-hover': t.hover,
    backgroundColor: t.paper,
    color: t.ink,
  } as CSSProperties;
}

/** Highlight washes strong enough to read on both a dark and a light page. */
export const MARK_WASH: Record<string, string> = {
  yellow: 'rgba(250, 204, 21, 0.22)',
  green: 'rgba(52, 211, 153, 0.2)',
  blue: 'rgba(56, 189, 248, 0.2)',
  pink: 'rgba(244, 114, 182, 0.2)',
  purple: 'rgba(167, 139, 250, 0.22)',
};

export const MARK_EDGE: Record<string, string> = {
  yellow: '#eab308',
  green: '#10b981',
  blue: '#0ea5e9',
  pink: '#ec4899',
  purple: '#8b5cf6',
};

/**
 * How a verse the two side-by-side translations word differently is marked:
 * a dashed outline, so it never collides with a highlight's wash and edge.
 */
export const DIFF_OUTLINE = 'outline outline-1 outline-dashed -outline-offset-1 outline-amber-500/70';

/** The type sizes the reader steps through (A− / A+), as multiples of the base size. */
export const TEXT_STEPS = [0.85, 1, 1.15, 1.3, 1.5];

/** The index of the step nearest `scale`, so a scale saved off the ladder still steps sensibly. */
export function nearestTextStep(scale: number): number {
  let best = 0;
  TEXT_STEPS.forEach((step, index) => {
    if (Math.abs(step - scale) < Math.abs(TEXT_STEPS[best] - scale)) best = index;
  });
  return best;
}
