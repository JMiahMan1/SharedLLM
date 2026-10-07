import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import PresenceAlertsToggle from '../components/settings/PresenceAlertsToggle';

describe('PresenceAlertsToggle', () => {
  it('shows the saved state and switches it', async () => {
    let saved: unknown = null;
    server.use(
      http.get('/api/geo/presence-alerts', () => HttpResponse.json({ enabled: true })),
      http.put('/api/geo/presence-alerts', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ enabled: false });
      }),
    );
    renderWithProviders(<PresenceAlertsToggle />);
    const toggle = await screen.findByRole('switch', { name: 'Arrival and departure alerts' });
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
    fireEvent.click(toggle);
    await waitFor(() => expect(saved).toEqual({ enabled: false }));
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'false'));
  });
});
