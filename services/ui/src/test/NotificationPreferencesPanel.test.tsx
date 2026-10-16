import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import NotificationPreferencesPanel from '../components/settings/NotificationPreferencesPanel';
import { NOTIFICATION_CATEGORIES } from '../lib/notificationCategories';

describe('NotificationPreferencesPanel', () => {
  it('shows a switch per category, on unless saved off', async () => {
    server.use(
      http.get('/api/users/me/notification-preferences', () => HttpResponse.json({ jobs: false })),
    );
    renderWithProviders(<NotificationPreferencesPanel />);

    const jobs = await screen.findByRole('switch', { name: 'Finished jobs' });
    await waitFor(() => expect(jobs).toHaveAttribute('aria-checked', 'false'));
    expect(screen.getByRole('switch', { name: 'Family chat' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Time to leave' })).toHaveAttribute('aria-checked', 'true');
  });

  it('saves every category with the one that changed', async () => {
    let saved: Record<string, boolean> | null = null;
    server.use(
      http.get('/api/users/me/notification-preferences', () => HttpResponse.json({})),
      http.put('/api/users/me/notification-preferences', async ({ request }) => {
        saved = (await request.json()) as Record<string, boolean>;
        return HttpResponse.json(saved);
      }),
    );
    renderWithProviders(<NotificationPreferencesPanel />);

    const bible = await screen.findByRole('switch', { name: 'Bible reading' });
    await waitFor(() => expect(bible).not.toBeDisabled());
    fireEvent.click(bible);

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.bible).toBe(false);
    expect(saved!.chat).toBe(true);
    await waitFor(() => expect(bible).toHaveAttribute('aria-checked', 'false'));
  });

  it('accepts preferences wrapped in a preferences object', async () => {
    server.use(
      http.get('/api/users/me/notification-preferences', () =>
        HttpResponse.json({ preferences: { timers: false } })),
    );
    renderWithProviders(<NotificationPreferencesPanel />);
    const timers = await screen.findByRole('switch', { name: 'Timers and alarms' });
    await waitFor(() => expect(timers).toHaveAttribute('aria-checked', 'false'));
  });

  it('puts the switch back when saving fails', async () => {
    server.use(
      http.get('/api/users/me/notification-preferences', () => HttpResponse.json({})),
      http.put('/api/users/me/notification-preferences', () => new HttpResponse(null, { status: 500 })),
    );
    renderWithProviders(<NotificationPreferencesPanel />);
    const home = await screen.findByRole('switch', { name: 'Home' });
    await waitFor(() => expect(home).not.toBeDisabled());
    fireEvent.click(home);
    await waitFor(() => expect(home).toHaveAttribute('aria-checked', 'true'));
    await waitFor(() => expect(home).not.toBeDisabled());
  });

  it('says SOS cannot be turned off', async () => {
    renderWithProviders(<NotificationPreferencesPanel />);
    expect(await screen.findByText(/SOS alerts always come through/i)).toBeInTheDocument();
  });

  it('shows system alerts to admins only', async () => {
    renderWithProviders(<NotificationPreferencesPanel />);
    expect(await screen.findByRole('switch', { name: 'System alerts' })).toBeInTheDocument();
  });

  it('hides system alerts from family members', async () => {
    server.use(
      http.get('/api/users/me', () =>
        HttpResponse.json({ id: 2, username: 'michele', role: 'user', is_admin: false })),
    );
    renderWithProviders(<NotificationPreferencesPanel />);
    await screen.findByRole('switch', { name: 'Family chat' });
    await waitFor(() => expect(screen.queryByRole('switch', { name: 'System alerts' })).not.toBeInTheDocument());
    const visible = NOTIFICATION_CATEGORIES.filter((c) => !c.adminOnly).length;
    expect(screen.getAllByRole('switch')).toHaveLength(visible);
  });
});
