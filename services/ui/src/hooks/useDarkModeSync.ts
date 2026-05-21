import { useState, useEffect, useCallback } from 'react';
import { storageGet, storageSet } from '../lib/storage';

type ThemeMode = 'light' | 'dark' | 'system';

function getSystemPreference(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function useDarkModeSync() {
  const [theme, setTheme] = useState<ThemeMode>('dark');
  const [isDark, setIsDark] = useState(true);

  const applyTheme = useCallback((mode: ThemeMode) => {
    let dark = mode === 'dark';
    if (mode === 'system') {
      dark = getSystemPreference();
    }
    setIsDark(dark);
    document.documentElement.classList.toggle('dark', dark);
  }, []);

  useEffect(() => {
    const loadTheme = async () => {
      const saved = await storageGet('jarvis_theme');
      const initialTheme = (saved as ThemeMode) || 'dark';
      setTheme(initialTheme);
      applyTheme(initialTheme);
    };
    loadTheme();
  }, [applyTheme]);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      if (theme === 'system') {
        applyTheme('system');
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [theme, applyTheme]);

  const setThemeMode = async (mode: ThemeMode) => {
    setTheme(mode);
    applyTheme(mode);
    await storageSet('jarvis_theme', mode);
  };

  return { theme, isDark, setThemeMode };
}
