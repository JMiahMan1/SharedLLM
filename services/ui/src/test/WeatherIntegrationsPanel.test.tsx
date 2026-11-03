import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { WeatherIntegrationsPanel } from '../components/admin/WeatherIntegrationsPanel';
import { renderWithProviders } from './render';
import { server } from './setup';

vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../components/ui/EntitySearchDropdown', () => ({
  default: ({ value, onChange, testId }: { value: string; onChange: (v: string) => void; testId?: string }) => (
    <input data-testid={testId} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

let stored: Record<string, string> = {};
let saves: Array<{ key: string; value: unknown }> = [];

beforeEach(() => {
  stored = { weather_metrics: '[]', weather_hooks: '[]' };
  saves = [];
  server.use(
    http.get('/api/settings', () => HttpResponse.json(Object.entries(stored).map(([key, value]) => ({ key, value })))),
    http.patch('/api/settings/:key', async ({ params, request }) => {
      const body = (await request.json()) as { value: string };
      stored[params.key as string] = body.value;
      saves.push({ key: params.key as string, value: JSON.parse(body.value) });
      return HttpResponse.json({ key: params.key, value: body.value });
    }),
    http.get('/api/weather/integrations', () => HttpResponse.json({
      events: ['forecast.updated', 'condition.changed', 'alert.started', 'day.completed'],
      services: ['geo', 'health', 'future_thing'], metric_modes: [], metric_aggs: [], kinds: [], stream: 'weather:events',
      metrics: [{ name: 'eggs', label: 'Eggs', unit: 'eggs', agg: 'sum', source: 'pushed' }],
    })),
    http.get('/api/weather/correlations', () => HttpResponse.json({
      units: '°F', today: '2026-11-03', insights: ['Tracked energy: about 0.80 kWh per degree-day of heating or cooling.'],
      metrics: [{ name: 'energy_kwh', label: 'Tracked energy', days: 12 }],
    })),
  );
});

describe('WeatherIntegrationsPanel', () => {
  it('shows insights, pushed metrics and the services the server lists', async () => {
    renderWithProviders(<WeatherIntegrationsPanel />);
    expect(await screen.findByTestId('weather-insights')).toHaveTextContent('0.80 kWh per degree-day');
    expect(await screen.findByText(/Sent by other services: Eggs/)).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'future_thing' })).toBeInTheDocument();
    expect(screen.getByText(/weather:events/)).toBeInTheDocument();
  });

  it('tracks a sensor and connects a service', async () => {
    renderWithProviders(<WeatherIntegrationsPanel />);
    await screen.findByRole('option', { name: 'geo' });
    fireEvent.change(screen.getByLabelText('Tracked metric name'), { target: { value: 'Solar panels' } });
    fireEvent.change(screen.getByTestId('weather-metric-entity'), { target: { value: 'sensor.solar_power' } });
    fireEvent.click(screen.getByRole('button', { name: 'Track' }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual({ key: 'weather_metrics', value: [{ name: 'solar_panels', label: 'Solar panels', entity_id: 'sensor.solar_power', mode: 'power' }] });

    fireEvent.change(screen.getByLabelText('Service to send to'), { target: { value: 'health' } });
    fireEvent.change(screen.getByLabelText('Path on that service'), { target: { value: '/api/hooks/weather' } });
    fireEvent.click(screen.getByLabelText('Rain'));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(saves).toHaveLength(2));
    expect(saves[1].key).toBe('weather_hooks');
    expect(saves[1].value).toMatchObject([{ events: ['alert.started'], service: 'health', path: '/api/hooks/weather', filter: { kinds: ['rain'] } }]);
  });

  it('says so in one line when the weather service is away', async () => {
    server.use(
      http.get('/api/weather/integrations', () => HttpResponse.json({}, { status: 502 })),
      http.get('/api/weather/correlations', () => HttpResponse.json({}, { status: 502 })),
    );
    renderWithProviders(<WeatherIntegrationsPanel />);
    expect(await screen.findByRole('status')).toHaveTextContent('not answering');
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
  });
});
