import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import WeatherSettingsPanel from './WeatherSettingsPanel';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

let puts: unknown[] = [];
let stored: Record<string, unknown> = { location: null, units: null };

beforeEach(() => {
  puts = [];
  stored = { location: null, units: null };
  server.use(
    http.get('/api/users/me/weather', () => HttpResponse.json({ status: 'SUCCESS', ...stored })),
    http.put('/api/users/me/weather', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      puts.push(body);
      stored = { ...stored, ...body };
      return HttpResponse.json({ status: 'SUCCESS', ...stored });
    }),
    http.get('/api/geo/saved-places', () => HttpResponse.json({
      places: [{ id: 'g', entity_id: 'place.g', name: "Grandma's", lat: 35.1, lon: -85.3, radius: 50, scope: 'family', owner: 'x' }],
    })),
  );
});

describe('WeatherSettingsPanel', () => {
  it('starts on the household default and saves a saved place as my own', async () => {
    renderWithProviders(<WeatherSettingsPanel />);
    const household = await screen.findByRole('radio', { name: 'Household default' });
    await waitFor(() => expect(household).toHaveAttribute('aria-checked', 'true'));
    fireEvent.click(screen.getByRole('radio', { name: 'A place I choose' }));
    await screen.findByRole('option', { name: "Grandma's" });
    fireEvent.change(screen.getByLabelText('Use a saved place'), { target: { value: 'g' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save my place' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ location: { name: "Grandma's", latitude: 35.1, longitude: -85.3 } });
  });

  it('goes back to the household default and sets units', async () => {
    stored = { location: { name: 'Lake', latitude: 1, longitude: 2 }, units: null };
    renderWithProviders(<WeatherSettingsPanel />);
    await waitFor(() => expect(screen.getByRole('radio', { name: 'A place I choose' })).toHaveAttribute('aria-checked', 'true'));
    expect(screen.getByLabelText('My weather place name')).toHaveValue('Lake');
    fireEvent.click(screen.getByRole('radio', { name: 'Household default' }));
    await waitFor(() => expect(puts).toEqual([{ location: null, follow: false }]));
    fireEvent.change(screen.getByLabelText('My weather units'), { target: { value: 'metric' } });
    await waitFor(() => expect(puts[1]).toEqual({ units: 'metric' }));
  });

  it('follows the phone, and says so honestly when it has no recent location', async () => {
    stored = { location: { name: 'Barn', latitude: 1, longitude: 2 }, units: null, follow: false, live: false };
    renderWithProviders(<WeatherSettingsPanel />);
    await screen.findByLabelText('My weather place name');
    fireEvent.click(screen.getByRole('radio', { name: 'Follow my phone' }));
    await waitFor(() => expect(puts).toEqual([{ follow: true }]));
    expect(await screen.findByTestId('weather-follow-status')).toHaveTextContent('so this uses Barn until it does');

    stored = { ...stored, live: true };
    fireEvent.click(screen.getByRole('radio', { name: 'A place I choose' }));
    await waitFor(() => expect(puts[1]).toEqual({ follow: false }));
  });

  it('shows when the phone location is in use', async () => {
    stored = { location: null, units: null, follow: true, live: true };
    renderWithProviders(<WeatherSettingsPanel />);
    expect(await screen.findByTestId('weather-follow-status')).toHaveTextContent('Using where your phone last shared');
    expect(screen.getByRole('radio', { name: 'Follow my phone' })).toHaveAttribute('aria-checked', 'true');
  });

  it('stays calm when weather settings are unavailable', async () => {
    server.use(http.get('/api/users/me/weather', () => HttpResponse.json({}, { status: 502 })));
    renderWithProviders(<WeatherSettingsPanel />);
    expect(await screen.findByRole('status')).toHaveTextContent('unavailable right now');
  });
});
