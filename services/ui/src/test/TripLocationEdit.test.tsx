import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Wander from '../pages/Wander';

const baseTrip = {
  id: 'trip-1',
  user_id: 'default',
  user_name: 'Default',
  vehicle_id: 'v1',
  vehicle_name: 'Truck',
  fuel_type: 'gasoline',
  mpg: 24,
  cost_per_gallon: 3.65,
  start_time: 1700000000,
  end_time: 1700001800,
  duration_seconds: 1800,
  distance_miles: 12.5,
  top_speed_mph: 55,
  avg_speed_mph: 25,
  start_location: { name: 'Home', latitude: 33.1667, longitude: -111.5646, address: '1 Main St, Phoenix, AZ' },
  end_location: { name: 'Mall', latitude: 33.25, longitude: -111.63 },
  fuel_used_gal: 0.52,
  trip_cost_usd: 1.9,
  activity_type: 'driving',
  status: 'completed' as const,
};

let patchBody: Record<string, unknown> | null = null;

function useTripHandlers() {
  server.use(
    http.get('/api/geo/trips', () =>
      HttpResponse.json({ trips: [baseTrip], total_trips: 1 })
    ),
    http.get('/api/geo/vehicles', () =>
      HttpResponse.json({ vehicles: [{ id: 'v1', name: 'Truck', mpg: 24, cost_per_gallon: 3.65 }] })
    ),
    http.get('/api/geo/people', () => HttpResponse.json({ features: [] })),
    http.get('/api/geo/zones', () => HttpResponse.json({ type: 'FeatureCollection', features: [] })),
    http.get('/api/users/location/all', () => HttpResponse.json({})),
    http.get('/api/geo/locations/suggestions', () =>
      HttpResponse.json({
        status: 'ok',
        latitude: 33.1667,
        longitude: -111.5646,
        current: { name: 'Home', address: '1 Main St, Phoenix, AZ', source: 'ha_zone' },
        candidates: [
          { name: 'Whole Foods Market', kind: 'shop', distance_m: 60 },
          { name: 'Pizzeria Bianco', kind: 'restaurant', distance_m: 120, address: '623 E Adams St' },
        ],
      })
    ),
    http.patch('/api/geo/trips/:id', async ({ request }) => {
      patchBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...baseTrip, ...patchBody });
    })
  );
}

describe('Wander trip location editing', () => {
  beforeEach(() => {
    patchBody = null;
    localStorage.setItem('jarvis_api_key', 'test-key');
    useTripHandlers();
  });

  afterEach(() => {
    server.resetHandlers();
  });

  it('picks a nearby place and saves it as the display name', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Wander />);

    const editButton = await screen.findByRole('button', { name: /edit vehicle & fuel/i });
    await user.click(editButton);

    expect(await screen.findByRole('heading', { name: /edit trip details/i })).toBeInTheDocument();
    const startInput = screen.getByLabelText('Starting Place');
    expect(startInput).toHaveValue('Home');

    // Debris check: stored address is shown, not the raw coordinates
    expect(screen.getByText('1 Main St, Phoenix, AZ')).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: /pick nearby place/i })[0]);

    const suggestions = await screen.findByTestId('suggestions-start');
    expect(suggestions).toHaveTextContent('Whole Foods Market');

    await user.click(screen.getByRole('button', { name: /whole foods market/i }));
    expect(startInput).toHaveValue('Whole Foods Market');

    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(patchBody).not.toBeNull());
    expect(patchBody?.start_name).toBe('Whole Foods Market');
    expect(patchBody?.end_name).toBe('Mall');
    // The stored address is still preserved on save
    expect(patchBody?.start_address).toBe('1 Main St, Phoenix, AZ');
  });

  it('leaves the destination untouched when only the start is renamed', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Wander />);

    await user.click(await screen.findByRole('button', { name: /edit vehicle & fuel/i }));
    const endInput = await screen.findByLabelText('Destination');
    await user.clear(endInput);
    await user.type(endInput, 'Pizzeria Bianco');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(patchBody).not.toBeNull());
    expect(patchBody?.end_name).toBe('Pizzeria Bianco');
    expect(patchBody?.start_name).toBe('Home');
  });
});
