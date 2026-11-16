import { createElement } from 'react';
import { MoonStar, Sparkles } from 'lucide-react';
import { useWeather } from '../../../hooks/useWeather';
import { formatDay, type Ymd } from '../../../lib/beloved/dates';
import { nightSky } from '../../../lib/beloved/sky';
import { round, weatherIcon } from '../../../lib/weather';

/**
 * A day's weather in Beloved, from the family forecast (`/api/weather`, the
 * same query the Weather widget and the calendar share, so it costs nothing
 * more). The forecast covers about a week: a day past it shows nothing, and
 * with `night` only the moon, which is worked out on the phone.
 *
 * Asking for the weather says nothing about why: the request is the same one
 * every page makes, with no day or plan in it.
 */
export function BelovedWeather({ day, night = false, compact = false }: { day: Ymd; night?: boolean; compact?: boolean }) {
  const { data } = useWeather();
  const forecast = data?.daily?.find((d) => d.date === formatDay(day));
  const sky = night ? nightSky(day, forecast?.night_cloud) : null;
  if (!forecast && !sky) return null;
  const unit = data?.units?.temp ?? '°';

  if (compact) {
    if (!forecast) return null;
    const label = `${forecast.label}, high ${round(forecast.high)}${unit}, low ${round(forecast.low)}${unit}`;
    return (
      <span className="inline-flex shrink-0 items-center gap-1 text-xs text-slate-300" role="img" aria-label={label} title={label} data-testid="beloved-weather">
        {createElement(weatherIcon(forecast.icon), { size: 14, 'aria-hidden': true, className: 'text-amber-200' })}
        <span aria-hidden>{round(forecast.high)}°</span>
      </span>
    );
  }

  return (
    <span className="mt-1.5 block space-y-1 text-xs text-slate-300" data-testid="beloved-weather">
      {forecast && (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5" data-testid="beloved-weather-day">
          {createElement(weatherIcon(forecast.icon), { size: 15, 'aria-hidden': true, className: 'shrink-0 text-amber-200' })}
          <span>{forecast.label}</span>
          <span className="text-slate-100">{round(forecast.high)}{unit}</span>
          <span>/ {round(forecast.low)}{unit}</span>
          {(forecast.precip_prob ?? 0) >= 20 && <span className="text-sky-300">{round(forecast.precip_prob)}% rain</span>}
        </span>
      )}
      {sky && (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5" data-testid="beloved-weather-night" data-look={sky.look ?? undefined}>
          {sky.look === 'clear'
            ? <Sparkles size={15} aria-hidden className="shrink-0 text-indigo-200" />
            : <MoonStar size={15} aria-hidden className="shrink-0 text-indigo-200" />}
          {sky.cloud !== null && (
            <span className={sky.look === 'cloudy' ? 'text-amber-200' : 'text-indigo-100'}>
              Overnight {sky.cloud}% cloud · {sky.verdict}
            </span>
          )}
          <span data-testid="beloved-weather-moon">
            {sky.moon.name} ({Math.round(sky.moon.lit * 100)}% lit)
          </span>
        </span>
      )}
    </span>
  );
}
