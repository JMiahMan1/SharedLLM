/**
 * The sharing picker must work for a NON-ADMIN.
 *
 * This is the test that was missing. The panel used to call the admin-only
 * `GET /api/users`, which returned 403 for an ordinary user, so `users` came
 * back empty, zero chips rendered, and "Everyone" was the only selectable
 * audience -- precisely inverting the consent model the app is built on.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ActivitySharingPanel from '../components/settings/ActivitySharingPanel';
import { renderWithProviders } from './render';
import { server, userActivitySharing } from './setup';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const JEREMIAH = { username: 'jeremiah', display_name: 'Jeremiah' };
const MICHELE = { username: 'michele', display_name: 'Michele' };

describe('ActivitySharingPanel as a non-admin', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    userActivitySharing.enabled = false;
    userActivitySharing.audience = 'circle';
    userActivitySharing.user_ids = [];
    userActivitySharing.share = ['totals'];
  });

  it('renders recipient chips from the narrow endpoint, not the admin user list', async () => {
    let calledNarrowEndpoint = false;
    server.use(
      http.get('/api/users/sharing-recipients', () => {
        calledNarrowEndpoint = true;
        return HttpResponse.json([JEREMIAH, MICHELE]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');

    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));

    expect(await screen.findByTestId('sharing-user-jeremiah')).toBeInTheDocument();
    expect(screen.getByTestId('sharing-user-michele')).toBeInTheDocument();
    expect(calledNarrowEndpoint).toBe(true);
  });

  it('never asks for the admin-only user list', async () => {
    let adminRouteCalled = false;
    server.use(
      http.get('/api/users', () => {
        adminRouteCalled = true;
        return HttpResponse.json([], { status: 401 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));
    await screen.findByTestId('sharing-user-jeremiah');

    // If the panel regressed to `getUsers()` this would be true.
    expect(adminRouteCalled).toBe(false);
  });

  it('survives an empty recipient list instead of silently offering only Everyone', async () => {
    server.use(http.get('/api/users/sharing-recipients', () => HttpResponse.json([])));

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));

    // An empty household must explain itself rather than present a blank box
    // that reads like "you have nobody to share with" for no stated reason.
    expect(
      await screen.findByText(/Add someone in Admin → Users/i),
    ).toBeInTheDocument();
  });

  it('excludes the privileged `default` account from the chips', async () => {
    server.use(
      http.get('/api/users/sharing-recipients', () =>
        HttpResponse.json([JEREMIAH, { username: 'default', display_name: 'Shared' }]),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));

    expect(await screen.findByTestId('sharing-user-jeremiah')).toBeInTheDocument();
    // `default` is a privileged viewer that reads everything anyway, so a
    // grant naming it would imply a control that does not exist.
    expect(screen.queryByTestId('sharing-user-default')).not.toBeInTheDocument();
  });

  it('saves the chosen person as an explicit, non-Everyone audience', async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/users/sharing-recipients', () => HttpResponse.json([JEREMIAH, MICHELE])),
      http.put('/api/users/me/activity-sharing', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', ...userActivitySharing });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));
    await user.click(await screen.findByTestId('sharing-user-michele'));
    await user.click(screen.getByTestId('save-sharing'));

    await waitFor(() => expect(sent).not.toBeNull());
    expect(sent).toMatchObject({ enabled: true, audience: 'users', user_ids: ['michele'] });
  });

  it('keeps a 44px touch target on each recipient chip', async () => {
    server.use(http.get('/api/users/sharing-recipients', () => HttpResponse.json([JEREMIAH])));

    const user = userEvent.setup();
    renderWithProviders(<ActivitySharingPanel />);
    await screen.findByTestId('activity-sharing-panel');
    await user.click(screen.getByTestId('sharing-toggle'));
    await user.click(screen.getByTestId('audience-users'));

    const chip = await screen.findByTestId('sharing-user-jeremiah');
    // Mobile parity (AGENTS.md): the chip is a primary tap target on a phone.
    expect(chip.className).toMatch(/min-h-11/);
  });
});