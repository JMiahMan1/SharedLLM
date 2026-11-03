import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { DayWeather } from './DayWeather';
import { server } from '../../test/setup';

const day = (date: string, icon: string, high: number, low: number, precip_prob = 0) => ({
  date, ts: 0, high, low, precip: 0, precip_prob, snow: 0, gust: 0, code: 0, label: icon === 'rain' ? 'Rain' : 'Clear',
  icon, sunrise: null, sunset: null,
});

function show(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('DayWeather on the calendar', () => {
  it('shows an icon and the high/low for a forecast day', async () => {
    server.use(http.get('/api/weather', () => HttpResponse.json({
      units: { temp: '°F' }, current: {}, hourly: [],
      daily: [day('2026-11-03', 'rain', 78.4, 57), day('2026-11-04', 'clear', 68, 56)],
    })));
    show(<><DayWeather dateKey="2026-11-03" /><DayWeather dateKey="2026-11-04" variant="full" /><DayWeather dateKey="2026-11-04" variant="icon" /></>);
    const [compact, full, icon] = await screen.findAllByTestId('day-weather');
    expect(compact).toHaveAccessibleName('Rain, high 78°, low 57°');
    expect(compact).toHaveTextContent('78°/57°');
    expect(full).toHaveTextContent('Clear68°/ 56°');
    expect(icon).toHaveTextContent('');
    expect(icon).toHaveAttribute('title', 'Clear, high 68°, low 56°');
  });

  it('says rain chances only when they matter', async () => {
    server.use(http.get('/api/weather', () => HttpResponse.json({
      units: { temp: '°F' }, current: {}, hourly: [], daily: [day('2026-11-03', 'rain', 60, 50, 80)],
    })));
    show(<DayWeather dateKey="2026-11-03" variant="full" />);
    expect(await screen.findByText('80% rain')).toBeInTheDocument();
  });

  it('renders nothing without a forecast for the day, or with no weather at all', async () => {
    const { container } = show(<DayWeather dateKey="2026-11-03" />);  // default handler: 404
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
  });
});
