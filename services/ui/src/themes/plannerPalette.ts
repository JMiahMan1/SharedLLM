/**
 * The "paper planner" palette shared by the calendar and the chores widget,
 * as `--os-*` custom properties for a scoped <style> block.
 *
 * Light themes get the warm paper look. Dark themes take the active theme's
 * text, accent and borders, so the planner follows Tron, Iron, Neon and the
 * rest instead of always showing the original purple.
 */
export const PLANNER_LIGHT = `
  --os-paper:#f5efe3;
  --os-paper-deep:#ece4d2;
  --os-card:#fffdf8;
  --os-ink:#34302a;
  --os-ink-soft:#756d5f;
  --os-ink-faint:#857c6b;
  --os-line:#e3dac6;
  --os-ember:#d95b3a;
  --os-ember-deep:#bf4526;
  --os-ember-soft:#f8ddd2;
  --os-on-ember:#fffdf8;
  --os-sun:#ffd9a0;
  --os-sun-soft:#fdf0da;
  --os-shadow:0 1px 3px rgba(72,60,38,0.07),0 10px 28px -10px rgba(72,60,38,0.16);
  --os-input-bg:#fffdf8;
  --os-panel-bg:rgba(255,253,248,0.6);
  --os-body-font:'Nunito',ui-sans-serif,system-ui,sans-serif;
  --os-display:'Fraunces',Georgia,'Times New Roman',serif;
`;

export const PLANNER_DARK = `
  --os-paper:rgba(255,255,255,0.03);
  --os-paper-deep:rgba(255,255,255,0.07);
  --os-card:rgba(255,255,255,0.06);
  --os-ink:var(--site-text);
  --os-ink-soft:color-mix(in srgb, var(--site-text) 62%, transparent);
  --os-ink-faint:var(--site-text-muted);
  --os-line:var(--color-border-mid);
  --os-ember:var(--site-accent);
  --os-ember-deep:var(--site-accent-text);
  --os-ember-soft:var(--site-accent-soft);
  --os-on-ember:var(--site-on-accent);
  --os-sun:var(--site-accent-text);
  --os-sun-soft:var(--site-accent-soft);
  --os-shadow:var(--shadow-panel);
  --os-input-bg:var(--site-input-bg);
  --os-panel-bg:rgba(255,255,255,0.05);
  --os-body-font:'Outfit',ui-sans-serif,system-ui,sans-serif;
  --os-display:'Outfit',ui-sans-serif,system-ui,sans-serif;
`;

/** Readable planner ink or paper for text on a colored fill (#rrggbb). */
export function textOn(hex?: string): string {
  const c = (hex || '').replace('#', '');
  if (c.length < 6) return '#34302a';
  const r = parseInt(c.slice(0, 2), 16);
  const g = parseInt(c.slice(2, 4), 16);
  const b = parseInt(c.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? '#34302a' : '#fffdf8';
}
