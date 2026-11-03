import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactElement } from 'react';
import WeatherWidget from './WeatherWidget';
import { server } from '../../test/setup';
import type { UserWidgetSettings, WidgetSize } from '../../types/widget';
import type { WeatherResponse } from '../../lib/weather';

const auth = vi.hoisted(() => ({ role: 'user' }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }));

const T0 = Date.UTC(2026, 10, 3, 13) / 1000; // 7 AM at UTC-6

const forecast: WeatherResponse = {
  source: 'open-meteo',
  location: { name: 'Home', latitude: 36, longitude: -86.8 },
  timezone: 'America/Chicago',
  utc_offset_seconds: -6 * 3600,
  units: { system: 'imperial', temp: '°F', wind: 'mph', precip: 'in' },
  current: {
    ts: T0, temp: 59.9, feels_like: 58, humidity: 83, precip: 0, code: 2, label: 'Partly cloudy',
    icon: 'partly_cloudy', wind: 3.5, gust: 14, is_day: true,
  },
  hourly: Array.from({ length: 14 }, (_, i) => ({
    time: '', ts: T0 + i * 3600, temp: 60 + i, precip_prob: i === 8 ? 80 : 5, precip: 0, snow: 0,
    code: i === 8 ? 63 : 1, label: i === 8 ? 'Rain' : 'Mostly clear', icon: i === 8 ? 'rain' : 'clear', wind: 5, gust: 10,
  })),
  daily: [
    { date: '2026-11-03', ts: T0, high: 78, low: 57, precip: 0.2, precip_prob: 80, snow: 0, gust: 30, code: 63,
      label: 'Rain', icon: 'rain', sunrise: null, sunset: null },
    { date: '2026-11-04', ts: T0 + 86400, high: 68, low: 56, precip: 0, precip_prob: 5, snow: 0, gust: 20, code: 0,
      label: 'Clear', icon: 'clear', sunrise: null, sunset: null },
  ],
  fetched_at: T0,
  heads_up: [{ id: 'hay', kind: 'rain', title: 'Cover the hay', body: 'Rain likely from 3 PM (80% chance).', starts_at: T0 }],
};

const settings = (size: WidgetSize): UserWidgetSettings => ({
  widget_key: 'weather', visibility: 'visible', order_index: 0, size, is_pinned: false,
  sort_mode: null, pinned_devices: [], config: {}, updated_at: 0,
});

function renderWidget(size: WidgetSize): ReturnType<typeof render> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={client}>
      <WeatherWidget settingsButton={null} userSettings={settings(size)} onTogglePin={() => {}} />
    </QueryClientProvider>
  );
  return render(ui);
}

describe('WeatherWidget', () => {
  it('shows now, the heads-up and the next hours at medium size', async () => {
    server.use(http.get('/api/weather', () => HttpResponse.json(forecast)));
    renderWidget('medium');
    expect(await screen.findByTestId('weather-now')).toHaveTextContent('60°F');
    expect(screen.getByText(/Partly cloudy · H 78° L 57°/)).toBeInTheDocument();
    expect(screen.getByTestId('weather-heads-up')).toHaveTextContent('Cover the hay');
    expect(screen.getByRole('list', { name: 'Next 12 hours' }).children).toHaveLength(12);
    expect(screen.getByText('3 PM')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'This week' })).not.toBeInTheDocument();
  });

  it('adds the week when wide, and keeps small to the reading', async () => {
    server.use(http.get('/api/weather', () => HttpResponse.json(forecast)));
    const { unmount } = renderWidget('wide');
    expect(await screen.findByRole('list', { name: 'This week' })).toHaveTextContent('Today');
    unmount();
    renderWidget('small');
    await screen.findByTestId('weather-now');
    expect(screen.queryByRole('list', { name: 'Next 12 hours' })).not.toBeInTheDocument();
    expect(screen.getByText('Cover the hay')).toBeInTheDocument();
  });

  it('shows admins how the weather is driving energy use when tall', async () => {
    auth.role = 'admin';
    server.use(
      http.get('/api/weather', () => HttpResponse.json(forecast)),
      http.get('/api/weather/energy', () => HttpResponse.json({
        units: '°F', days: [], model: null, insights: ['Yesterday: 30.0 kWh measured, heat ran 6.0 h.'],
        projection: [{ date: '2026-11-03', hdd: 0, cdd: 0, kwh: 20 }, { date: '2026-11-04', hdd: 5, cdd: 0, kwh: 24 }],
      })),
    );
    renderWidget('tall');
    const energy = await screen.findByTestId('weather-energy');
    expect(energy).toHaveTextContent('Yesterday: 30.0 kWh measured');
    expect(energy).toHaveTextContent(/Expected: .* ~24 kWh/);
    auth.role = 'user';
  });

  it('explains a missing home location', async () => {
    renderWidget('medium');
    // The hook retries once (about a second) before showing the error.
    expect(await screen.findByRole('status', {}, { timeout: 4000 })).toHaveTextContent(/No home location for the forecast/);
  });
});
