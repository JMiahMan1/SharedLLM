import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import MobileMenuPanel from '../components/settings/MobileMenuPanel';
import { server } from './setup';
import { renderWithProviders } from './render';
import {
  MOBILE_NAV_STORAGE_KEY,
  defaultMobileNavPaths,
  readMobileNavPaths,
} from '../lib/mobileNav';

describe('MobileMenuPanel', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('starts from the shipped five and refuses a sixth pin', async () => {
    renderWithProviders(<MobileMenuPanel />);

    for (const path of defaultMobileNavPaths(true)) {
      expect(await screen.findByTestId(`mobile-pin-${path}`)).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Pin Notes' })).toBeDisabled();
    expect(screen.getByText('Pinned · 5/5')).toBeInTheDocument();
  });

  it('unpins, pins, reorders and resets the per-device layout', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MobileMenuPanel />);

    await user.click(await screen.findByRole('button', { name: 'Unpin Health' }));
    expect(readMobileNavPaths(true)).toEqual(['/', '/wander', '/family', '/media']);

    await user.click(screen.getByRole('button', { name: 'Pin Notes' }));
    expect(readMobileNavPaths(true)).toEqual(['/', '/wander', '/family', '/media', '/notes']);

    await user.click(screen.getByRole('button', { name: 'Move Notes up' }));
    expect(readMobileNavPaths(true)).toEqual(['/', '/wander', '/family', '/notes', '/media']);

    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    await waitFor(() => expect(localStorage.getItem(MOBILE_NAV_STORAGE_KEY)).toBeNull());
    expect(readMobileNavPaths(true)).toEqual(defaultMobileNavPaths(true));
  });

  it('does not offer admin-only pages to non-admins or keep them pinned', async () => {
    server.use(
      http.get('/api/users/me', () =>
        HttpResponse.json({ id: 2, username: 'michele', role: 'user', is_admin: false })),
    );
    localStorage.setItem(MOBILE_NAV_STORAGE_KEY, JSON.stringify(['/admin', '/notes', '/calendar']));

    renderWithProviders(<MobileMenuPanel />);

    expect(await screen.findByTestId('mobile-pin-/notes')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-pin-/calendar')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-pin-/admin')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pin Admin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pin Lab' })).not.toBeInTheDocument();
  });
});
