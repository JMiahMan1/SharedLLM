import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ActivitySharingPanel from '../components/settings/ActivitySharingPanel';
import { renderWithProviders } from './render';
import { server, userActivitySharing } from './setup';

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const sam = {
  id: 2,
  username: 'sam',
  display_name: 'Sam',
  is_admin: false,
  role: 'member',
};

describe('ActivitySharingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    userActivitySharing.enabled = false;
    userActivitySharing.audience = 'circle';
    userActivitySharing.user_ids = [];
    userActivitySharing.share = ['totals'];
    // The picker reads the narrow recipient list, NOT the admin-only
    // `GET /api/users` -- which is what left a non-admin with an empty picker.
    server.use(
      http.get('/api/users/sharing-recipients', () =>
        HttpResponse.json([{ username: sam.username, display_name: sam.display_name }]),
      ),
    );
  });

  it('defaults to private and explains it', async () => {
    renderWithProviders(<ActivitySharingPanel />);

    expect(await screen.findByTestId('activity-sharing-panel')).toBeInTheDocument();
    expect(screen.getByTestId('sharing-toggle')).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/Private — nothing is visible/i)).toBeInTheDocument();
    // controls only appear once sharing is on
    expect(screen.queryByTestId('audience-circle')).not.toBeInTheDocument();
  });

  it('enabling and choosing people sends audience and scopes', async () => {
    const user = userEvent.setup();
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.put('/api/users/me/activity-sharing', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          status: 'SUCCESS',
          enabled: true,
          audience: 'users',
          user_ids: ['sam'],
          share: ['totals', 'achievements'],
        });
      })
    );

    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');

    await user.click(screen.getByTestId('sharing-toggle'));
    expect(screen.getByTestId('sharing-toggle')).toHaveAttribute('aria-checked', 'true');

    await user.click(screen.getByTestId('audience-users'));
    await user.click(await screen.findByTestId('sharing-user-sam'));
    await user.click(screen.getByTestId('scope-achievements'));
    await user.click(screen.getByTestId('save-sharing'));

    await waitFor(() => {
      expect(sent).not.toBeNull();
    });
    expect(sent).toMatchObject({
      enabled: true,
      audience: 'users',
      user_ids: ['sam'],
      share: ['totals', 'achievements'],
    });
  });

  it('turning sharing off sends enabled false', async () => {
    const user = userEvent.setup();
    userActivitySharing.enabled = true;
    userActivitySharing.audience = 'circle';
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.put('/api/users/me/activity-sharing', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', ...userActivitySharing, enabled: false });
      })
    );

    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await waitFor(() => {
      expect(screen.getByTestId('sharing-toggle')).toHaveAttribute('aria-checked', 'true');
    });

    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('save-sharing'));

    await waitFor(() => {
      expect(sent).not.toBeNull();
    });
    expect(sent).toMatchObject({ enabled: false });
  });

  it('refuses to save with nothing selected', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');

    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('scope-totals')); // uncheck the only scope
    await user.click(screen.getByTestId('save-sharing'));

    const toast = (await import('react-hot-toast')).default;
    expect(toast.error).toHaveBeenCalledWith('Pick at least one thing to share');
  });
});
