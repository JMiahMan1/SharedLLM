import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AppCleanupPanel from '../components/settings/AppCleanupPanel';
import { renderWithProviders } from './render';
import { MOBILE_NAV_STORAGE_KEY, writeMobileNavPaths } from '../lib/mobileNav';

describe('AppCleanupPanel', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    Reflect.deleteProperty(window, 'caches');
  });

  it('clears React Query and Cache Storage but leaves credentials and settings alone', async () => {
    const removeCache = vi.fn(async () => true);
    Object.defineProperty(window, 'caches', {
      configurable: true,
      value: {
        keys: vi.fn(async () => ['app-shell', 'media']),
        delete: removeCache,
      },
    });
    localStorage.setItem('jarvis_api_key', 'test-token');
    localStorage.setItem('jarvis_user', '{"username":"default"}');
    localStorage.setItem('user-setting', 'keep-me');

    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<AppCleanupPanel />);
    queryClient.setQueryData(['probe'], { cached: true });

    await user.click(screen.getByRole('button', { name: /clear app cache/i }));

    await waitFor(() => expect(queryClient.getQueryData(['probe'])).toBeUndefined());
    expect(removeCache).toHaveBeenCalledWith('app-shell');
    expect(removeCache).toHaveBeenCalledWith('media');
    expect(localStorage.getItem('jarvis_api_key')).toBe('test-token');
    expect(localStorage.getItem('jarvis_user')).not.toBeNull();
    expect(localStorage.getItem('user-setting')).toBe('keep-me');
  });

  it('resets the mobile menu from the cleanup panel', async () => {
    writeMobileNavPaths(['/notes'], true);
    const user = userEvent.setup();
    renderWithProviders(<AppCleanupPanel />);

    await user.click(screen.getByRole('button', { name: /reset mobile menu/i }));

    await waitFor(() => expect(localStorage.getItem(MOBILE_NAV_STORAGE_KEY)).toBeNull());
  });
});
