import { createElement } from 'react';
import { useWeather } from '../../hooks/useWeather';
import { round, weatherIcon, type WeatherDay, type WeatherIcon } from '../../lib/weather';

/**
 * A day's weather on the calendar: a small icon and the high/low.
 *
 * Reads the shared forecast query (the person's own place, else the
 * household's; docs/WEATHER.md), so every day on screen costs nothing extra.
 * The forecast covers about a week: any other day, or no forecast at all
 * (weather not set up, Open-Meteo away), renders nothing, and the calendar
 * looks exactly as it did before weather existed.
 *
 * Colours are mid-tones that read on the calendar's light paper and in dark
 * mode alike; cloud and fog take the surrounding ink.
 */

const TONE: Partial<Record<WeatherIcon, string>> = {
  clear: '#d97706',
  partly_cloudy: '#d97706',
  drizzle: '#0284c7',
  rain: '#0284c7',
  sleet: '#0891b2',
  snow: '#0891b2',
  storm: '#7c3aed',
};

export type DayWeatherVariant = 'icon' | 'compact' | 'full';

function useForecastDay(dateKey: string): WeatherDay | null {
  const { data } = useWeather();
  return data?.daily?.find((d) => d.date === dateKey) ?? null;
}

export function DayWeather({ dateKey, variant = 'compact', className = '' }: {
  dateKey: string;
  variant?: DayWeatherVariant;
  className?: string;
}) {
  const day = useForecastDay(dateKey);
  if (!day) return null;
  const label = `${day.label}, high ${round(day.high)}°, low ${round(day.low)}°${
    (day.precip_prob ?? 0) >= 20 ? `, ${round(day.precip_prob)}% chance of rain` : ''}`;
  const icon = createElement(weatherIcon(day.icon), {
    size: variant === 'full' ? 18 : variant === 'icon' ? 11 : 13,
    'aria-hidden': true,
    className: 'shrink-0',
    style: { color: TONE[day.icon] ?? 'var(--os-ink-soft)' },
  });

  if (variant === 'icon') {
    return (
      <span className={`inline-flex ${className}`} role="img" aria-label={label} title={label} data-testid="day-weather">
        {icon}
      </span>
    );
  }
  if (variant === 'full') {
    return (
      <span className={`inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-bold ${className}`}
        style={{ color: 'var(--os-ink-soft)' }} data-testid="day-weather" title={label}>
        {icon}
        <span>{day.label}</span>
        <span style={{ color: 'var(--os-ink)' }}>{round(day.high)}°</span>
        <span>/ {round(day.low)}°</span>
        {(day.precip_prob ?? 0) >= 20 && <span style={{ color: TONE.rain }}>{round(day.precip_prob)}% rain</span>}
      </span>
    );
  }
  // Wraps rather than spills: in a narrow week column the low drops under
  // the high instead of running into the next day.
  return (
    <span className={`inline-flex max-w-full flex-wrap items-center gap-x-1 gap-y-0.5 text-[10px] font-bold leading-none ${className}`}
      style={{ color: 'var(--os-ink-soft)' }} role="img" aria-label={label} title={label} data-testid="day-weather">
      {icon}
      <span aria-hidden="true" className="whitespace-nowrap" style={{ color: 'var(--os-ink)' }}>{round(day.high)}°</span>
      <span aria-hidden="true" className="whitespace-nowrap">/{round(day.low)}°</span>
    </span>
  );
}

export default DayWeather;
