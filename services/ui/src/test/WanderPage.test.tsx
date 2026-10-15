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

  it("shows each person's live app position on their card, not HA's stale person", async () => {
    const fresh = Date.now() / 1000 - 30;
    server.use(
      http.get('/api/geo/people', () =>
        HttpResponse.json({
          features: [
            { properties: { entity_id: 'person.michele', friendly_name: 'Michele', state: 'home', last_updated: '2026-10-14T18:00:00Z' } },
          ],
        }),
      ),
      http.get('/api/geo/zones', () =>
        HttpResponse.json({
          features: [{ properties: { entity_id: 'zone.work', friendly_name: 'Work', latitude: 33.3, longitude: -111.9, radius: 150 } }],
        }),
      ),
      http.get('/api/users/location/all', () =>
        HttpResponse.json({ michele: { latitude: 33.3001, longitude: -111.9001, accuracy: 12, timestamp: fresh, updated_at: fresh } }),
      ),
    );
    renderWithProviders(<Wander />);
    const name = await screen.findAllByText('Michele');
    const card = name[0].closest('[role="button"]') ?? name[0].parentElement!.parentElement!;
    await waitFor(() => expect(card.textContent).toContain('Work'));
    expect(card.textContent).not.toContain('Home');
  });

  it('lists the five most recent trips, then each period on its own tab', async () => {
    const day = 86_400;
    const now = new Date();
    const noon = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12).getTime() / 1000;
    // One trip today, the rest a week or more apart.
    const many = Array.from({ length: 8 }, (_, i) =>
      trip({ id: `t${i}`, start_time: noon - i * day * 8, end_time: noon - i * day * 8 + 600 }),
    );
    useWanderHandlers({ trips: () => HttpResponse.json({ trips: many, total_trips: many.length }) });
    const user = userEvent.setup();
    renderWithProviders(<Wander />);

    const seeAll = await screen.findByTestId('see-all-trips');
    expect(seeAll).toHaveTextContent('See all 8 trips');
    expect(screen.getAllByTestId('trip-card')).toHaveLength(5);

    await user.click(seeAll);
    expect(await screen.findByTestId('all-trips')).toBeInTheDocument();
    // Week tab: only this week's trip.
    expect(screen.getByTestId('period-title')).toHaveTextContent('This week');
    expect(screen.getAllByTestId('trip-card')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Next week' })).toBeDisabled();

    // A week back: no trips (they are 8 days apart and today is in this week).
    await user.click(screen.getByRole('button', { name: 'Previous week' }));
    expect(screen.getByTestId('period-title')).toHaveTextContent(/Last week/);

    // Day tab keeps the date: back to today via next, then only today's trip.
    await user.click(screen.getByRole('radio', { name: 'day' }));
    await user.click(screen.getByRole('radio', { name: 'year' }));
    expect(screen.getByTestId('period-title')).toHaveTextContent(String(now.getFullYear()));
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