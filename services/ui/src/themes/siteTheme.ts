import { useEffect, useState, useCallback, useSyncExternalStore } from 'react';
import { api } from '../services/api';
import { storageGet, storageSet } from '../lib/storage';
import { themeRegistry } from './registry';
import { themeToCssVars, resolveScheme, type HealthThemeCssVars, type ThemePack, type ThemeScheme, type ThemeMotif } from './types';

const LS_SITE_THEME = 'jarvis_site_theme_id';

/**
 * The one active Jarvis theme. Every surface (site chrome and every dashboard
 * widget) follows this — per-widget themes are deliberately not supported.
 */
let activeThemeId = 'aurora';
const activeThemeListeners = new Set<(id: string) => void>();

export function getActiveThemeId(): string {
  return activeThemeId;
}

export function subscribeActiveTheme(listener: (id: string) => void): () => void {
  activeThemeListeners.add(listener);
  return () => activeThemeListeners.delete(listener);
}

/** React hook: the active Jarvis-wide theme id. */
export function useActiveThemeId(): string {
  return useSyncExternalStore(subscribeActiveTheme, getActiveThemeId, getActiveThemeId);
}

function setActiveThemeId(id: string): void {
  if (activeThemeId === id) return;
  activeThemeId = id;
  for (const listener of activeThemeListeners) listener(id);
}

export interface SiteThemePreference {
  themeId: string;
  packs: ThemePack[];
}

/** Hex (#rgb / #rrggbb / #rrggbbaa) to an rgba() string with the given alpha. */
export function withAlpha(hex: string, alpha: number): string {
  let h = hex.replace('#', '').trim();
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  if (h.length === 8) h = h.slice(0, 6);
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return `rgba(139, 92, 246, ${alpha})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Encode an inline SVG as a tiled data-URI background. */
function svgTile(svg: string): string {
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

/**
 * Decorative tiled SVG motifs, data-driven per theme (never hardcoded in
 * components). Each motif is a small seamless tile rendered faintly behind the
 * whole interface so themes can carry a botanical / HUD signature.
 */
export function motifPattern(
  motif: ThemeMotif | undefined,
  accentAlt: string,
  accent: string
): string {
  switch (motif) {
    case 'petal':
      return svgTile(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">' +
          '<g fill="none" stroke-width="1.5">' +
          `<path d="M30 22c8 6 8 16 0 22-8-6-8-16 0-22z" stroke="${accentAlt}" stroke-opacity="0.18"/>` +
          `<path d="M92 66c6 5 6 13 0 18-6-5-6-13 0-18z" stroke="${accent}" stroke-opacity="0.14"/>` +
          `<circle cx="74" cy="20" r="3" stroke="${accentAlt}" stroke-opacity="0.12"/>` +
          `<circle cx="18" cy="78" r="2.5" stroke="${accent}" stroke-opacity="0.12"/>` +
          `<path d="M58 96c7 5 7 14 0 19-7-5-7-14 0-19z" stroke="${accentAlt}" stroke-opacity="0.16"/>` +
          '</g></svg>'
      );
    // Full blossoms — a six-petal flower with a pollen centre, tiled sparsely.
    case 'bloom': {
      const petals = Array.from({ length: 6 }, (_, i) => {
        const a = (i * Math.PI) / 3;
        const cx = 60 + Math.cos(a) * 22;
        const cy = 40 + Math.sin(a) * 22;
        return `<ellipse cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" rx="12" ry="6" transform="rotate(${((i * 60)).toFixed(0)} ${cx.toFixed(1)} ${cy.toFixed(1)})" stroke="${accent}" stroke-opacity="0.16" fill="none" stroke-width="1.4"/>`;
      }).join('');
      return svgTile(
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">' +
          `<g>${petals}<circle cx="60" cy="40" r="4" stroke="${accentAlt}" stroke-opacity="0.22" fill="none" stroke-width="1.4"/></g>` +
          `<circle cx="150" cy="130" r="3" stroke="${accentAlt}" stroke-opacity="0.14" fill="none"/>` +
          `<path d="M138 150c6 4 6 12 0 16-6-4-6-12 0-16z" stroke="${accent}" stroke-opacity="0.12" fill="none"/>` +
          '</svg>'
      );
    }
    // Foliage — soft leaf sprigs on a diagonal.
    case 'leaf':
      return svgTile(
        '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140" viewBox="0 0 140 140">' +
          '<g fill="none" stroke-width="1.4">' +
          `<path d="M20 100c24-8 40-24 48-48 4 26-8 46-30 54-8 3-14-2-18-6z" stroke="${accent}" stroke-opacity="0.13"/>` +
          `<path d="M22 98c16-12 28-24 40-40" stroke="${accentAlt}" stroke-opacity="0.12"/>` +
          `<path d="M96 60c16-4 26-14 30-30 3 18-6 32-22 37-6 2-10-2-12-4z" stroke="${accent}" stroke-opacity="0.11"/>` +
          '</g></svg>'
      );
    // Tron-style HUD lattice.
    case 'hud':
    case 'grid':
      return svgTile(
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">' +
          '<g fill="none" stroke-width="1">' +
          `<path d="M0 24h48M24 0v48" stroke="${accent}" stroke-opacity="0.06"/>` +
          `<rect x="20" y="20" width="8" height="8" stroke="${accentAlt}" stroke-opacity="0.10"/>` +
          '</g></svg>'
      );
    default:
      return 'none';
  }
}

/** Map pack tokens onto site-level CSS variables (same source as widgets). */
export function siteThemeCssVars(tokens: Parameters<typeof themeToCssVars>[0]): HealthThemeCssVars {
  const ht = themeToCssVars(tokens);
  const radius = Math.min(28, Math.max(8, tokens.radius));
  return {
    ...ht,
    '--color-bg-base': tokens.bg,
    '--color-surface-0': withAlpha(tokens.surface, 0.35),
    '--color-surface-1': withAlpha(tokens.surface, 0.55),
    '--color-surface-2': withAlpha(tokens.surface, 0.8),
    '--color-border-soft': tokens.border,
    '--color-border-mid': tokens.border,
    '--color-border-strong': tokens.accent,
    '--color-purple-glow': withAlpha(tokens.accent, 0.35),
    '--color-purple-dim': withAlpha(tokens.accent, 0.12),
    '--site-bg-glow-a': withAlpha(tokens.accent, 0.18),
    '--site-bg-glow-b': withAlpha(tokens.ring2 ?? tokens.accentAlt ?? tokens.accent, 0.12),
    '--site-text': tokens.text,
    '--site-text-muted': tokens.textMuted,
    '--site-accent': tokens.accent,
    '--site-on-accent': tokens.onAccent,
    '--site-accent-soft': withAlpha(tokens.accent, 0.15),
    '--site-accent-hover': withAlpha(tokens.accent, 0.25),
    '--site-accent-border': withAlpha(tokens.accent, 0.30),
    '--site-accent-focus': withAlpha(tokens.accent, 0.50),
    '--site-accent-text': tokens.accentAlt ?? tokens.accent,
    '--site-panel-a': withAlpha(tokens.surface, 0.9),
    '--site-panel-b': withAlpha(tokens.bg, 0.6),
    '--site-card-bg': withAlpha(tokens.surface, 0.45),
    '--site-card-bg-hover': withAlpha(tokens.surface, 0.7),
    '--site-input-bg': withAlpha(tokens.bg, 0.75),
    '--site-placeholder': tokens.textMuted,
    '--site-radius-input': `${Math.max(6, radius - 4)}px`,
    '--site-radius-button': `${Math.max(6, radius - 4)}px`,
    '--site-glow': tokens.glow ?? withAlpha(tokens.accent, 0.2),
    '--site-motif': tokens.motif ?? 'none',
    '--site-scheme': resolveScheme(tokens),
    '--site-pattern': motifPattern(
      tokens.motif,
      tokens.accentAlt ?? tokens.accent,
      tokens.accent
    ),
    '--radius-card': `${radius}px`,
    '--radius-panel': `${Math.min(32, Math.max(10, tokens.radius + 4))}px`,
  };
}

/** Reflect a theme's light/dark scheme on the document (single source of truth). */
function applyScheme(scheme: ThemeScheme): void {
  const root = document.documentElement;
  root.setAttribute('data-theme-scheme', scheme);
  root.style.colorScheme = scheme;
  root.classList.toggle('dark', scheme === 'dark');
  const body = document.body;
  if (body) {
    body.classList.toggle('night-mode', scheme === 'dark');
    body.classList.toggle('day-mode', scheme === 'light');
  }
}

function applyVarsToRoot(vars: HealthThemeCssVars, scheme: ThemeScheme): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  root.setAttribute('data-site-theme-active', '1');
  applyScheme(scheme);
}

/** Clear dynamic site theme vars (back to index.css defaults). */
export function clearSiteTheme(): void {
  const root = document.documentElement;
  for (const key of [
    '--color-bg-base',
    '--color-surface-0',
    '--color-surface-1',
    '--color-surface-2',
    '--color-border-soft',
    '--color-border-mid',
    '--color-border-strong',
    '--color-purple-glow',
    '--color-purple-dim',
    '--site-text',
    '--site-text-muted',
    '--site-accent',
    '--site-on-accent',
    '--site-accent-soft',
    '--site-accent-hover',
    '--site-accent-border',
    '--site-accent-focus',
    '--site-accent-text',
    '--site-bg-glow-a',
    '--site-bg-glow-b',
    '--site-panel-a',
    '--site-panel-b',
    '--site-card-bg',
    '--site-card-bg-hover',
    '--site-input-bg',
    '--site-placeholder',
    '--site-radius-input',
    '--site-radius-button',
    '--site-glow',
    '--site-motif',
    '--site-scheme',
    '--site-pattern',
    '--radius-card',
    '--radius-panel',
    '--ht-bg',
    '--ht-surface',
    '--ht-text',
    '--ht-text-muted',
    '--ht-border',
    '--ht-accent',
    '--ht-on-accent',
    '--ht-progress',
    '--ht-progress-track',
    '--ht-ring',
    '--ht-ring-2',
    '--ht-ring-3',
    '--ht-accent-alt',
    '--ht-glow',
    '--ht-font',
    '--ht-number-font',
    '--ht-radius',
    '--ht-motif',
    '--ht-corner-cut',
  ]) {
    root.style.removeProperty(key);
  }
  root.removeAttribute('data-site-theme-active');
}

/**
 * Apply a theme (by id) from the shared registry to the document root.
 * Widgets and the website read the same pack tokens.
 */
export function applySiteTheme(themeId: string): string {
  const theme = themeRegistry.resolveTheme(themeId);
  const scheme = resolveScheme(theme.tokens);
  applyVarsToRoot(siteThemeCssVars(theme.tokens), scheme);
  document.documentElement.setAttribute('data-theme-id', theme.id);
  setActiveThemeId(theme.id);
  return theme.id;
}

/** Preset themes used when a global light/dark preference is requested. */
export const THEME_PRESETS: Record<ThemeScheme, string> = {
  light: 'clean-athletic',
  dark: 'aurora',
};

/** Resolve the light/dark scheme for a theme id (falls back to aurora). */
export function resolveThemeScheme(themeId: string): ThemeScheme {
  return resolveScheme(themeRegistry.resolveTheme(themeId).tokens);
}

async function hydratePacksFromServer(packs: ThemePack[]): Promise<void> {
  for (const pack of packs) {
    try {
      // Skip re-import if identical pack id already present with same version
      const existing = themeRegistry.getPack(pack.id);
      if (existing && existing.version === pack.version && !existing.builtin) {
        continue;
      }
      if (pack.id === 'jarvis-default') continue;
      themeRegistry.importPackJson(JSON.stringify(pack), { force: false });
    } catch {
      // invalid pack from server — ignore, still apply theme_id
    }
  }
}

/**
 * Loads the user's website theme preference, installs any synced packs,
 * and keeps document CSS vars in sync. Same theme files drive widgets.
 */
export function useSiteTheme() {
  const [themeId, setThemeId] = useState<string>(() => {
    return localStorage.getItem(LS_SITE_THEME) || 'aurora';
  });
  const [ready, setReady] = useState(false);

  const apply = useCallback((id: string) => {
    const resolved = applySiteTheme(id);
    setThemeId(resolved);
    void storageSet(LS_SITE_THEME, resolved);
    return resolved;
  }, []);

  useEffect(() => {
    const cancelled = { value: false };

    const load = async () => {
      const local = (await storageGet(LS_SITE_THEME)) || localStorage.getItem(LS_SITE_THEME);
      let nextId = local || 'aurora';
      let serverPacks: ThemePack[] = [];

      try {
        // Only ask the server when we actually have a session; this hook runs
        // before AuthProvider resolves, so an unauthenticated 401 here would
        // otherwise bounce the app to /login on every cold start.
        const token = await storageGet('jarvis_api_key');
        if (token) {
          const pref = await api.getUserTheme();
          if (pref?.theme_id) nextId = pref.theme_id;
          serverPacks = Array.isArray(pref?.packs) ? (pref.packs as ThemePack[]) : [];
        }
      } catch {
        // offline / not signed in — local only
      }

      if (!cancelled.value && serverPacks.length) {
        await hydratePacksFromServer(serverPacks);
      }
      if (!cancelled.value) {
        apply(nextId);
        setReady(true);
      }
    };

    void load();
    return () => {
      cancelled.value = true;
    };
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    const unsub = themeRegistry.subscribe(() => {
      // Re-apply if the active theme was edited/removed
      if (!cancelled) apply(themeId);
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, [apply, themeId]);

  const setSiteTheme = useCallback(
    async (id: string) => {
      const resolved = apply(id);
      try {
        await api.updateUserTheme({ theme_id: resolved });
      } catch {
        // keep local selection
      }
      return resolved;
    },
    [apply]
  );

  /** Push local user packs + selection to the server (per-user sync). */
  const syncPacksToServer = useCallback(async () => {
    const packs = themeRegistry
      .listPacks()
      .filter((p) => !p.builtin && p.id !== 'jarvis-default');
    try {
      await api.updateUserTheme({ theme_id: themeId, packs });
      return true;
    } catch {
      return false;
    }
  }, [themeId]);

  return { themeId, setSiteTheme, ready, syncPacksToServer };
}
