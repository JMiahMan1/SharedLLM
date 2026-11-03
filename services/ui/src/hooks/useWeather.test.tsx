import { describe, expect, it } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { useHouseholdWeather } from './useWeather';
import { server } from '../test/setup';

const reply = (temp: number, personal: boolean) => ({
  source: 'open-meteo', location: { name: personal ? "Grandma's" : 'Home', latitude: 1, longitude: 1 },
  timezone: null, utc_offset_seconds: 0, units: { system: 'imperial', temp: '°F', wind: 'mph', precip: 'in' },
  current: { temp }, hourly: [], daily: [], fetched_at: 0, personal, heads_up: [],
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useHouseholdWeather', () => {
  it('is the home forecast when the person has their own place elsewhere', async () => {
    const asked: string[] = [];
    server.use(http.get('*/api/weather', ({ request }) => {
      const household = new URL(request.url).searchParams.get('household') === 'true';
      asked.push(household ? 'home' : 'mine');
      return HttpResponse.json(household ? reply(61, false) : reply(41, true));
    }));
    const { result } = renderHook(() => useHouseholdWeather(), { wrapper });
    await waitFor(() => expect(result.current.data?.current.temp).toBe(61));
    expect(asked).toEqual(['mine', 'home']);
  });

  it('shares the person\'s query when they keep the household default', async () => {
    const asked: string[] = [];
    server.use(http.get('*/api/weather', ({ request }) => {
      asked.push(new URL(request.url).search);
      return HttpResponse.json(reply(61, false));
    }));
    const { result } = renderHook(() => useHouseholdWeather(), { wrapper });
    await waitFor(() => expect(result.current.data?.current.temp).toBe(61));
    expect(asked).toEqual(['']);
  });
});
