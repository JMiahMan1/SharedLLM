import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CloudOff, CloudSun, Droplets, Wind, Zap } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { useWeather } from '../../hooks/useWeather';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { dayLabel, hourLabel, round, type WeatherHeadsUp } from '../../lib/weather';
import { WeatherGlyph } from './WeatherGlyph';

/**
 * Weather on the dashboard (docs/WEATHER.md).
 *
 * One `/api/weather` call: the household's Open-Meteo forecast plus the
 * caller's own "heads up" (the weather reminders that name them). The same
 * query key feeds the Climate widget's outdoor reading, so the two never
 * disagree and the dashboard pays for one request.
 *
 * Sizing follows the grid's ~280px column and 200px rows: `small` is the
 * current reading and today's range, `medium` adds the heads-up and the next
 * twelve hours, `wide` adds the week, and `tall` adds, for admins, how the
 * weather is driving the household's energy use.
 */

function HeadsUp({ items }: { items: WeatherHeadsUp[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-1.5" data-testid="weather-heads-up">
      {items.map((item) => (
        <li key={item.id} className="flex gap-2 rounded-xl border border-amber-400/20 bg-amber-500/10 px-2.5 py-1.5">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold text-amber-100">{item.title}</p>
            <p className="line-clamp-2 text-[11px] text-amber-100/80">{item.body}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function EnergyLine() {
  const { data } = useQuery({
    queryKey: ['weather', 'energy'],
    queryFn: () => api.getWeatherEnergy(14),
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });
  if (!data) return null;
  const next = data.projection.slice(1, 4).filter((d) => d.kwh != null);
  if (!data.insights.length && !next.length) {
    return (
      <p className="text-[11px] text-slate-500">
        Weather and energy are being recorded; a summary appears after the first full day.
      </p>
    );
  }
  return (
    <div className="space-y-1" data-testid="weather-energy">
      <p className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-slate-500">
        <Zap size={11} aria-hidden="true" /> Weather and energy
      </p>
      {data.insights.map((line) => (
        <p key={line} className="text-[11px] text-slate-300">{line}</p>
      ))}
      {next.length > 0 && (
        <p className="text-[11px] text-slate-400">
          Expected:{' '}
          {next.map((d, i) => `${dayLabel(d.date, i + 1)} ~${round(d.kwh)} kWh`).join(' · ')}
        </p>
      )}
    </div>
  );
}

export default function WeatherWidget({ settingsButton, userSettings }: IWidgetProps) {
  const size = userSettings.size;
  const isAdmin = useAuth().role === 'admin';
  const { data, isLoading, error } = useWeather();

  const header = (
    <div className="flex shrink-0 items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <CloudSun size={16} className="shrink-0 text-sky-300" aria-hidden="true" />
        <h3 className="truncate text-sm font-bold text-white">
          Weather{data?.location?.name && data.location.name !== 'Home' ? ` · ${data.location.name}` : ''}
        </h3>
      </div>
      {settingsButton}
    </div>
  );

  if (isLoading) {
    return (
      <div className="glass-panel flex h-full flex-col rounded-2xl p-4">
        {header}
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center" aria-busy="true">
          <CloudSun size={28} className="animate-pulse text-slate-600" aria-hidden="true" />
          <p className="text-xs text-slate-500">Reading the forecast…</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    const msg = error instanceof Error ? error.message : '';
    const noPlace = /home location/i.test(msg) || /\b404\b/.test(msg);
    return (
      <div className="glass-panel flex h-full flex-col rounded-2xl p-4">
        {header}
        {/* A calm, centred empty state: the card keeps its place on the board
            and reads as "nothing to show", not as something broken. */}
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-2 text-center">
          <CloudOff size={28} className="text-slate-600" aria-hidden="true" />
          <p className="max-w-[16rem] text-xs text-slate-400" role="status">
            {noPlace
              ? 'No home location for the forecast yet. An admin can set one in Admin > Weather reminders.'
              : 'The forecast is unavailable right now. It will try again shortly.'}
          </p>
        </div>
      </div>
    );
  }

  const { current, units } = data;
  const today = data.daily[0];
  const hours = data.hourly.slice(1, 13);
  const roomy = size !== 'small';
  const showWeek = size === 'wide' || size === 'tall';
  const heads = data.heads_up ?? [];

  return (
    <div className="glass-panel flex h-full flex-col gap-3 overflow-hidden rounded-2xl p-4" data-testid="weather-widget">
      {header}

      <div className="flex shrink-0 items-center gap-3">
        <WeatherGlyph icon={current.icon} isDay={current.is_day} size={roomy ? 40 : 32} />
        <div className="min-w-0">
          <p className="text-3xl font-bold leading-none text-white" data-testid="weather-now">
            {round(current.temp)}{units.temp}
          </p>
          <p className="mt-1 truncate text-xs text-slate-300">
            {current.label}
            {today ? ` · H ${round(today.high)}° L ${round(today.low)}°` : ''}
          </p>
        </div>
        {roomy && (
          <div className="ml-auto shrink-0 space-y-0.5 text-right text-[11px] text-slate-400">
            <p>Feels {round(current.feels_like)}°</p>
            <p className="flex items-center justify-end gap-1">
              <Wind size={11} aria-hidden="true" />
              <span><span className="sr-only">Wind </span>{round(current.wind)} {units.wind}</span>
            </p>
            <p className="flex items-center justify-end gap-1">
              <Droplets size={11} aria-hidden="true" />
              <span><span className="sr-only">Humidity </span>{round(current.humidity)}%</span>
            </p>
          </div>
        )}
      </div>

      {!roomy && heads[0] && (
        <p className="truncate text-[11px] font-semibold text-amber-200">{heads[0].title}</p>
      )}

      {roomy && (
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          <HeadsUp items={heads} />

          {hours.length > 0 && (
            <ol className="flex touch-pan-x gap-3 overflow-x-auto pb-1" aria-label="Next 12 hours">
              {hours.map((h) => {
                return (
                  <li key={h.ts} className="flex w-10 shrink-0 flex-col items-center gap-1 text-center">
                    <span className="text-[10px] text-slate-500">{hourLabel(h.ts, data.utc_offset_seconds)}</span>
                    <WeatherGlyph icon={h.icon} label={h.label} />
                    <span className="text-xs font-semibold text-white">{round(h.temp)}°</span>
                    <span className={`text-[10px] ${(h.precip_prob ?? 0) >= 50 ? 'text-sky-300' : 'text-slate-500'}`}>
                      {round(h.precip_prob)}%
                    </span>
                  </li>
                );
              })}
            </ol>
          )}

          {showWeek && (
            <ul className="space-y-1" aria-label="This week">
              {data.daily.map((d, i) => {
                return (
                  <li key={d.date} className="flex items-center gap-2 text-xs">
                    <span className="w-10 shrink-0 text-slate-400">{dayLabel(d.date, i)}</span>
                    <WeatherGlyph icon={d.icon} size={14} label={d.label} />
                    <span className="w-9 shrink-0 text-right text-sky-300">
                      {(d.precip_prob ?? 0) >= 20 ? `${round(d.precip_prob)}%` : ''}
                    </span>
                    <span className="ml-auto text-slate-400">{round(d.low)}°</span>
                    <span className="w-8 text-right font-semibold text-white">{round(d.high)}°</span>
                  </li>
                );
              })}
            </ul>
          )}

          {size === 'tall' && isAdmin && <EnergyLine />}
        </div>
      )}

      {data.stale && (
        <p className="shrink-0 text-[10px] text-slate-500">Showing the last forecast; Open-Meteo is not answering.</p>
      )}
    </div>
  );
}
