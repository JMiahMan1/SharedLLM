import { useEffect, useState, useCallback } from 'react';
import { api } from '../services/api';
import { storageGet, storageSet } from '../lib/storage';
import { themeRegistry } from './registry';
import { themeToCssVars, type HealthThemeCssVars, type ThemePack } from './types';

const LS_SITE_THEME = 'jarvis_site_theme_id';

export interface SiteThemePreference {
  themeId: string;
  packs: ThemePack[];
}

/** Map pack tokens onto site-level CSS variables (same source as widgets). */
export function siteThemeCssVars(tokens: Parameters<typeof themeToCssVars>[0]): HealthThemeCssVars {
  const ht = themeToCssVars(tokens);
  const surface = tokens.surface;
  return {
    ...ht,
    '--color-bg-base': tokens.bg,
    '--color-surface-0': surface,
    '--color-surface-1': surface,
    '--color-surface-2': tokens.border,
    '--color-border-soft': tokens.border,
    '--color-border-mid': tokens.border,
    '--color-border-strong': tokens.accent,
    '--color-purple-glow': tokens.glow ?? `${tokens.accent}59`,
    '--color-purple-dim': `${tokens.accent}1f`,
    '--site-text': tokens.text,
    '--site-text-muted': tokens.textMuted,
    '--site-accent': tokens.accent,
    '--site-on-accent': tokens.onAccent,
    '--radius-card': `${Math.min(28, Math.max(8, tokens.radius))}px`,
    '--radius-panel': `${Math.min(32, Math.max(10, tokens.radius + 4))}px`,
  };
}

function applyVarsToRoot(vars: HealthThemeCssVars): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  root.setAttribute('data-site-theme-active', '1');
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
  applyVarsToRoot(siteThemeCssVars(theme.tokens));
  document.documentElement.setAttribute('data-theme-id', theme.id);
  return theme.id;
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
        const pref = await api.getUserTheme();
        if (pref?.theme_id) nextId = pref.theme_id;
        serverPacks = Array.isArray(pref?.packs) ? pref.packs : [];
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
