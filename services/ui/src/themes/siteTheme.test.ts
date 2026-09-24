import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import {
  applySiteTheme,
  clearSiteTheme,
  siteThemeCssVars,
  useSiteTheme,
} from './siteTheme';
import { themeRegistry } from './registry';
import { createThemePackage, createThemePack, serializeThemePack } from './types';

vi.mock('../services/api', () => {
  let themeId = 'bloom';
  let packs: unknown[] = [];
  return {
    api: {
      getUserTheme: vi.fn(async () => ({
        theme_id: themeId,
        packs,
      })),
      updateUserTheme: vi.fn(async (body: { theme_id?: string; packs?: unknown[] }) => {
        if (body?.theme_id) themeId = body.theme_id;
        if (body?.packs) packs = body.packs;
        return {
          status: 'SUCCESS',
          theme_id: themeId,
          packs,
        };
      }),
      __reset: () => {
        themeId = 'bloom';
        packs = [];
      },
    },
  };
});

// Import after mock so useSiteTheme sees mocked api
import { api } from '../services/api';

describe('site theme application', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-token');
    document.documentElement.removeAttribute('data-theme-id');
    document.documentElement.removeAttribute('data-site-theme-active');
    document.documentElement.removeAttribute('style');
    vi.mocked(api.getUserTheme).mockResolvedValue({ theme_id: 'bloom', packs: [] });
    vi.mocked(api.updateUserTheme).mockClear();
  });

  it('maps pack tokens to site CSS vars (shared with widgets)', () => {
    const theme = themeRegistry.resolveTheme('bloom');
    const vars = siteThemeCssVars(theme.tokens);
    expect(vars['--ht-bg']).toBe(theme.tokens.bg);
    expect(vars['--color-bg-base']).toBe(theme.tokens.bg);
    expect(vars['--site-accent']).toBe(theme.tokens.accent);
    expect(vars['--color-border-strong']).toBe(theme.tokens.accent);
  });

  it('applySiteTheme sets root CSS vars and data-theme-id', () => {
    const resolved = applySiteTheme('neon');
    expect(resolved).toBe('neon');
    const root = document.documentElement;
    expect(root.getAttribute('data-theme-id')).toBe('neon');
    expect(root.getAttribute('data-site-theme-active')).toBe('1');
    const neon = themeRegistry.resolveTheme('neon');
    expect(root.style.getPropertyValue('--color-bg-base')).toBe(neon.tokens.bg);
    expect(root.style.getPropertyValue('--ht-accent')).toBe(neon.tokens.accent);
  });

  it('falls back to aurora when theme id is unknown', () => {
    const resolved = applySiteTheme('missing-theme-xyz');
    expect(resolved).toBe('aurora');
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('aurora');
  });

  it('clearSiteTheme removes dynamic vars', () => {
    applySiteTheme('tron');
    clearSiteTheme();
    expect(document.documentElement.style.getPropertyValue('--color-bg-base')).toBe('');
    expect(document.documentElement.hasAttribute('data-site-theme-active')).toBe(false);
  });
});

describe('useSiteTheme', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-token');
    document.documentElement.removeAttribute('data-theme-id');
    document.documentElement.removeAttribute('data-site-theme-active');
    document.documentElement.removeAttribute('style');
    vi.mocked(api.getUserTheme).mockResolvedValue({ theme_id: 'bloom', packs: [] });
    vi.mocked(api.updateUserTheme).mockClear();
  });

  it('loads theme from server and applies it', async () => {
    const { result } = renderHook(() => useSiteTheme());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.themeId).toBe('bloom');
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('bloom');
    expect(localStorage.getItem('jarvis_site_theme_id')).toBe('bloom');
  });

  it('setSiteTheme applies locally and persists to server', async () => {
    const { result } = renderHook(() => useSiteTheme());
    await waitFor(() => expect(result.current.ready).toBe(true));

    await act(async () => {
      const id = await result.current.setSiteTheme('clean-athletic');
      expect(id).toBe('clean-athletic');
    });

    expect(result.current.themeId).toBe('clean-athletic');
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('clean-athletic');
    expect(api.updateUserTheme).toHaveBeenCalledWith({ theme_id: 'clean-athletic' });
  });

  it('does not call the theme API when there is no session', async () => {
    localStorage.removeItem('jarvis_api_key');
    vi.mocked(api.getUserTheme).mockClear();

    const { result } = renderHook(() => useSiteTheme());
    await waitFor(() => expect(result.current.ready).toBe(true));

    // A pre-auth 401 from this call would otherwise clear the session and
    // bounce the app to /login on every cold start.
    expect(api.getUserTheme).not.toHaveBeenCalled();
    expect(localStorage.getItem('jarvis_api_key')).toBeNull();
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('aurora');
  });

  it('continues when server theme fetch fails (local-only)', async () => {    vi.mocked(api.getUserTheme).mockRejectedValueOnce(new Error('offline'));
    localStorage.setItem('jarvis_site_theme_id', 'iron');
    const { result } = renderHook(() => useSiteTheme());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.themeId).toBe('iron');
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('iron');
  });

  it('hydrates user packs from server and syncs local packs back', async () => {
    const pack = createThemePack('server-pack', 'Server Pack');
    pack.themes = [
      createThemePackage('server-teal', 'Teal', {
        bg: '#042F2E',
        text: '#ECFEFF',
        accent: '#14B8A6',
      }),
    ];
    vi.mocked(api.getUserTheme).mockResolvedValue({
      theme_id: 'server-teal',
      packs: [JSON.parse(serializeThemePack(pack))],
    });

    const { result } = renderHook(() => useSiteTheme());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(themeRegistry.getPack('server-pack')).toBeTruthy();
    expect(result.current.themeId).toBe('server-teal');
    expect(document.documentElement.getAttribute('data-theme-id')).toBe('server-teal');

    const localPack = createThemePack('local-sync-pack', 'Local Sync');
    localPack.themes = [
      createThemePackage('local-sync-theme', 'Local', {
        bg: '#111111',
        text: '#EEEEEE',
        accent: '#00FFCC',
      }),
    ];
    themeRegistry.importPackJson(serializeThemePack(localPack));

    await act(async () => {
      const ok = await result.current.syncPacksToServer();
      expect(ok).toBe(true);
    });

    expect(api.updateUserTheme).toHaveBeenCalledWith(
      expect.objectContaining({
        theme_id: 'server-teal',
        packs: expect.arrayContaining([
          expect.objectContaining({ id: 'local-sync-pack' }),
        ]),
      })
    );
  });
});
