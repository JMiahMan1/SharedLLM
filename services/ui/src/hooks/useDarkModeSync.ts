import { useState, useEffect, useCallback } from 'react';
import { storageGet, storageSet } from '../lib/storage';

type ThemeMode = 'light' | 'dark' | 'system';

function getSystemPreference(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function useDarkModeSync() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem('jarvis_dark_mode') || localStorage.getItem('jarvis_theme');
    return (saved as ThemeMode) || 'dark';
  });
  const [isDark, setIsDark] = useState<boolean>(() => {
    const saved = localStorage.getItem('jarvis_dark_mode') || localStorage.getItem('jarvis_theme') || 'dark';
    if (saved === 'system') {
      return getSystemPreference();
    }
    return saved === 'dark';
  });

  const applyTheme = useCallback((mode: ThemeMode) => {
    let dark = mode === 'dark';
    if (mode === 'system') {
      dark = getSystemPreference();
    }
    setIsDark(dark);
    document.documentElement.classList.toggle('dark', dark);
    if (dark) {
      document.body.classList.add('night-mode');
      document.body.classList.remove('day-mode');
    } else {
      document.body.classList.add('day-mode');
      document.body.classList.remove('night-mode');
    }
  }, []);

  useEffect(() => {
    const loadTheme = async () => {
      const saved = await storageGet('jarvis_dark_mode') || await storageGet('jarvis_theme');
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
    await storageSet('jarvis_dark_mode', mode);
    await storageSet('jarvis_theme', mode);
  };

  return { theme, isDark, setThemeMode };
}
