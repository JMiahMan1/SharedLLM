import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import type { Trip } from '../types/api';

vi.mock('../context/LocationContext', async () => {
  const actual = await vi.importActual<typeof import('../context/LocationContext')>(
    '../context/LocationContext',
  );
  return {
    ...actual,
    useLocation: () => ({
      sensors: {
        location: { enabled: false, permission: 'unknown', recovering: false, message: null },
        steps: { enabled: false, permission: 'unknown', recovering: false, message: null },
      },
      latitude: null,
      longitude: null,
      accuracy: null,
      speed: null,
      timestamp: null,
      isTracking: false,
      error: null,
      interval: 'stationary',
      enableSensor: vi.fn().mockResolvedValue(true),
      disableSensor: vi.fn().mockResolvedValue(undefined),
      openSensorSettings: vi.fn().mockResolvedValue(undefined),
      startTracking: vi.fn().mockResolvedValue(undefined),
      stopTracking: vi.fn(),
    }),
  };
});

import Wander from '../pages/Wander';

const trip = (over: Partial<Trip> = {}): Trip => ({
  id: 't1',
  user_id: 'person.jeremiah',
  user_name: 'Jeremiah',
  mpg: 25,
  cost_per_gallon: 3.65,
  start_time: 1750000000,
  end_time: 1750003600,
  duration_seconds: 3600,
  distance_miles: 18.4,
  top_speed_mph: 62,
  fuel_used_gal: 0.7,
  trip_cost_usd: 2.6,
  activity_type: 'driving',
  status: 'completed',
  ...over,
});

/** A geo handler returning a JSON body; both the seeded and the failing
 * variants below produce this shape. */
type Handler = () => ReturnType<typeof HttpResponse.json>;

interface Handlers {
  trips?: Handler;
}

function useWanderHandlers({ trips }: Handlers = {}) {
  server.use(
    http.get('/api/geo/trips', () => trips?.() ?? HttpResponse.json({ trips: [], total_trips: 0 })),
    http.get('/api/geo/vehicles', () => HttpResponse.json({ vehicles: [] })),
    http.get('/api/geo/people', () => HttpResponse.json({ features: [] })),
    http.get('/api/geo/zones', () => HttpResponse.json({ features: [] })),
  );
}

describe('Wander page', () => {
  beforeEach(() => {
    useWanderHandlers();
  });

  afterEach(() => {
    server.resetHandlers();
  });

  it('says so when the load fails instead of showing an empty trips list', async () => {
    // The default identity is an admin, but a failed fetch must be visible --
    // an empty state would read as "nobody drove anywhere".
    server.use(
      http.get('/api/geo/trips', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    renderWithProviders(<Wander />);

    const alert = await screen.findByTestId('wander-load-error');
    expect(within(alert).getByText(/could not load trips/i)).toBeInTheDocument();
    expect(screen.queryByText(/no recorded trips found/i)).not.toBeInTheDocument();
  });

  it('renders a trip returned by the server without client-side permission guessing', async () => {
    // The server is now the authority on what a caller may see. This trip is
    // owned by `jeremiah` while the test identity is `default`, and it must
    // still render -- the old page re-derived permission from a client-side
    // assignment list and would have greyed this out.
    useWanderHandlers({ trips: () => HttpResponse.json({ trips: [trip()], total_trips: 1 }) });
    renderWithProviders(<Wander />);

    expect(await screen.findByText(/all wander trips/i)).toBeInTheDocument();
    expect(screen.getAllByText('Jeremiah').length).toBeGreaterThan(0);
  });

  it('labels the trip owner by name rather than a raw entity id', async () => {
    useWanderHandlers({
      trips: () =>
        HttpResponse.json({
          trips: [trip({ user_id: 'person.michele', user_name: '' })],
          total_trips: 1,
        }),
    });
    renderWithProviders(<Wander />);

    expect(await screen.findByText('Michele')).toBeInTheDocument();
    expect(screen.queryByText('person.michele')).not.toBeInTheDocument();
  });

  it('gives filter tabs a touch-sized target and an accessible pressed state', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Wander />);

    const all = await screen.findByRole('button', { name: /all wander trips/i });
    const mine = screen.getByRole('button', { name: /my trips/i });

    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(mine).toHaveAttribute('aria-pressed', 'false');
    expect(all.className).toContain('min-h-11');
    expect(mine.className).toContain('min-h-11');

    await user.click(mine);
    await waitFor(() => expect(mine).toHaveAttribute('aria-pressed', 'true'));
    expect(all).toHaveAttribute('aria-pressed', 'false');
  });

  it('filters to the caller’s own trips on the My Trips tab', async () => {
    const user = userEvent.setup();
    useWanderHandlers({
      trips: () =>
        HttpResponse.json({
          trips: [
            trip({ id: 'a', user_id: 'person.default', user_name: 'Default' }),
            trip({ id: 'b', user_id: 'person.someone-else', user_name: 'Someone Else' }),
          ],
          total_trips: 2,
        }),
    });
    renderWithProviders(<Wander />);

    expect(await screen.findByText('Default')).toBeInTheDocument();
    expect(screen.getByText('Someone Else')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /my trips/i }));

    await waitFor(() => expect(screen.queryByText('Someone Else')).not.toBeInTheDocument());
    expect(screen.getByText('Default')).toBeInTheDocument();
  });

  it('does not offer editing on a trip the caller does not own', async () => {
    // The old `isTripOwner` also returned true for a user named `admin`; since
    // no such account exists the affordance was dead and always 403'd. An
    // admin still does not own someone else's trip.
    useWanderHandlers({
      trips: () =>
        HttpResponse.json({
          trips: [trip({ user_id: 'person.someone-else', user_name: 'Someone Else' })],
          total_trips: 1,
        }),
    });
    renderWithProviders(<Wander />);

    await screen.findByText('Someone Else');
    expect(screen.queryByRole('button', { name: /edit vehicle & fuel/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^share$/i })).not.toBeInTheDocument();
  });
});