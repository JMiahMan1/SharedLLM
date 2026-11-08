import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import NotificationLockCard from '../components/guardian/NotificationLockCard';
import { renderWithProviders } from './render';
import { server } from './setup';

describe('NotificationLockCard', () => {
  it('shows every non-safety category as always-on, and lets a parent allow one', async () => {
    let savedRules: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/screen-time/kid/status', () =>
        HttpResponse.json({
          username: 'kid',
          day: '2026-11-08',
          rules: {},
        }),
      ),
      http.put('/api/screen-time/kid/rules', async ({ request }) => {
        savedRules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', rules: savedRules });
      }),
    );

    renderWithProviders(<NotificationLockCard username="kid" name="Emma" />);

    // Safety notices are named and explained, not offered as a switch.
    expect(await screen.findByText('Family safety notices')).toBeInTheDocument();
    expect(screen.getByText(/cannot be changed/i)).toBeInTheDocument();

    // Arrivals start locked (Always on); tapping flips them to Can switch off.
    const arrivals = await screen.findByRole('button', { name: /Let Emma switch off Arrivals and departures/ });
    expect(arrivals).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(arrivals);

    await waitFor(() => expect(savedRules).not.toBeNull());
    expect(savedRules!.notification_toggle_allowed).toEqual(['family_presence']);
  });

  it('honours a saved allow-list and removes one on a second tap', async () => {
    let savedRules: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/screen-time/kid/status', () =>
        HttpResponse.json({
          username: 'kid',
          day: '2026-11-08',
          rules: { notification_toggle_allowed: ['bible', 'family_presence'] },
        }),
      ),
      http.put('/api/screen-time/kid/rules', async ({ request }) => {
        savedRules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', rules: savedRules });
      }),
    );

    renderWithProviders(<NotificationLockCard username="kid" name="Emma" />);

    const presence = await screen.findByRole('button', { name: /Prevent Emma switch off Arrivals and departures/ });
    expect(presence).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(presence);

    await waitFor(() => expect(savedRules).not.toBeNull());
    expect(savedRules!.notification_toggle_allowed).toEqual(['bible']);
  });
});
