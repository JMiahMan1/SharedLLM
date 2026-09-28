import { useState, useEffect, useCallback, useSyncExternalStore } from 'react';
import { storageGet, storageSet } from '../lib/storage';
import {
  getActiveThemeId,
  subscribeActiveTheme,
  applySiteTheme,
  resolveThemeScheme,
  THEME_PRESETS,
} from '../themes/siteTheme';

type ThemeMode = 'light' | 'dark' | 'system';

const LS_MODE = 'jarvis_theme_mode';

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// Live mirror of the active site theme id so isDark follows the pack (the
// single source of truth), not a separate dark-mode flag that used to fight it.
function subscribeTheme(listener: () => void): () => void {
  return subscribeActiveTheme(() => listener());
}
function getThemeSnapshot(): string {
  return getActiveThemeId();
}

/**
 * Light / dark preference, now derived from the active website theme's scheme
 * rather than an independent flag. Choosing a light/dark mode selects the
 * matching preset theme and persists it; the theme pack stays the source of
 * truth for every surface (site chrome + widgets + the .night/.day body class).
 */
export function useDarkModeSync() {
  const themeId = useSyncExternalStore(subscribeTheme, getThemeSnapshot, getThemeSnapshot);
  const [mode, setMode] = useState<ThemeMode>(() => {
    const saved =
      localStorage.getItem(LS_MODE) ||
      localStorage.getItem('jarvis_dark_mode') ||
      localStorage.getItem('jarvis_theme');
    return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system';
  });

  const isDark = resolveThemeScheme(themeId) === 'dark';

  // Honor a "system" preference for light-OS users on first paint without
  // clobbering an explicit or server-provided theme selection.
  useEffect(() => {
    let cancelled = false;
    const reconcile = async () => {
      const saved = await storageGet(LS_MODE);
      if (cancelled || saved !== 'system') return;
      if (systemPrefersDark()) return; // dark is already the default pack
      applySiteTheme(THEME_PRESETS.light);
      void storageSet('jarvis_site_theme_id', THEME_PRESETS.light);
    };
    void reconcile();
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-apply when the OS scheme flips while in "system" mode.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      if (mode === 'system') applySiteTheme(mq.matches ? THEME_PRESETS.dark : THEME_PRESETS.light);
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [mode]);

  const setThemeMode = useCallback(async (next: ThemeMode) => {
    setMode(next);
    await storageSet(LS_MODE, next);
    const id =
      next === 'system'
        ? systemPrefersDark()
          ? THEME_PRESETS.dark
          : THEME_PRESETS.light
        : THEME_PRESETS[next];
    applySiteTheme(id);
    void storageSet('jarvis_site_theme_id', id);
    localStorage.setItem('jarvis_site_theme_id', id);
    try {
      const { api } = await import('../services/api');
      await api.updateUserTheme({ theme_id: id });
    } catch {
      // keep local selection when offline
    }
  }, []);

  return { theme: mode, isDark, setThemeMode };
}
