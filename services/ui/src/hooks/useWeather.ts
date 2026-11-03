import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import type { WeatherResponse } from '../lib/weather';

/** One cache key for every reader of the person's forecast (Weather widget, calendar). */
export const WEATHER_QUERY_KEY = ['weather'] as const;

const OPTIONS = { staleTime: 10 * 60 * 1000, refetchInterval: 15 * 60 * 1000, retry: 1 } as const;

/**
 * The signed-in person's forecast from `/api/weather` (docs/WEATHER.md): their
 * own place from Settings > Weather, else the household's. The server
 * refreshes Open-Meteo every 15 minutes, so asking more often only re-reads
 * its cache.
 */
export function useWeather(enabled = true) {
  return useQuery<WeatherResponse>({
    queryKey: WEATHER_QUERY_KEY,
    queryFn: () => api.getWeather(),
    enabled,
    ...OPTIONS,
  });
}

/**
 * Always the home forecast, for readings about the house itself (the Climate
 * widget's outdoor temperature). Shares the person's query when they keep
 * the household default, so it costs a request only for someone whose own
 * place is elsewhere.
 */
export function useHouseholdWeather() {
  const mine = useWeather();
  const own = mine.data?.personal === true;
  const home = useQuery<WeatherResponse>({
    queryKey: [...WEATHER_QUERY_KEY, 'household'],
    queryFn: () => api.getWeather(true),
    enabled: own,
    ...OPTIONS,
  });
  return own ? home : mine;
}
