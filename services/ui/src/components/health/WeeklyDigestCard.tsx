import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CalendarRange, TrendingDown, TrendingUp } from 'lucide-react';
import { api } from '../../services/api';
import { formatDayLabel, formatMinutes, localDay } from '../../lib/healthHub';
import type { HealthWeeklyMetric } from '../../types/api';

/**
 * The weekly digest (Health hub Phase 5): the last seven days' averages, the
 * best night's sleep and what changed from the week before. Averages cover
 * only the days something was logged; a metric nobody logged is not shown.
 */

const LABELS: Record<HealthWeeklyMetric, string> = {
  sleep_minutes: 'Sleep',
  sleep_score: 'Sleep score',
  calories: 'Calories',
  water_ml: 'Water',
  energy: 'Energy',
  mood: 'Mood',
};

function formatWeeklyValue(metric: HealthWeeklyMetric, value: number): string {
  switch (metric) {
    case 'sleep_minutes':
      return formatMinutes(value);
    case 'sleep_score':
      return `${Math.round(value)}/100`;
    case 'calories':
      return `${Math.round(value)} kcal`;
    case 'water_ml':
      return `${Math.round(value)} ml`;
    default:
      return `${value}/5`;
  }
}

export default function WeeklyDigestCard() {
  const end = useMemo(() => localDay(), []);
  // Under the summary's key, so every log that refreshes Today refreshes this too.
  const { data, isLoading, isError } = useQuery({
    queryKey: ['health-summary', 'weekly', end],
    queryFn: () => api.getHealthWeekly(end),
    retry: false,
  });

  if (isLoading) return <div className="h-32 skeleton rounded-2xl" aria-hidden />;

  return (
    <section
      className="glass-panel rounded-2xl border border-white/5 p-4 sm:p-5"
      aria-labelledby="weekly-digest-heading"
      data-testid="weekly-digest"
    >
      <div className="flex items-center justify-between gap-3">
        <h3
          id="weekly-digest-heading"
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
        >
          <CalendarRange size={13} className="text-sky-300" />
          This week
        </h3>
        {data && (
          <span className="text-[10px] text-slate-500">
            {formatDayLabel(data.start)} - {formatDayLabel(data.end)}
          </span>
        )}
      </div>

      {isError || !data ? (
        <p className="mt-2 text-xs text-amber-300" data-testid="weekly-digest-error">The weekly digest could not be loaded.</p>
      ) : data.days_logged === 0 ? (
        <p className="mt-2 text-xs text-slate-400" data-testid="weekly-digest-empty">
          Nothing logged in the last seven days yet. Sleep, food, water and check-ins add up here.
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          <p className="text-[11px] text-slate-500">
            Daily averages over the {data.days_logged} day{data.days_logged === 1 ? '' : 's'} with something logged.
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {data.metrics.map((metric) => {
              const value = data.averages[metric];
              if (value === null || value === undefined) return null;
              return (
                <div key={metric} className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
                  <p className="text-[10px] uppercase tracking-wider text-slate-500">{LABELS[metric]}</p>
                  <p className="text-sm font-semibold text-slate-100" data-testid={`weekly-avg-${metric}`}>
                    {formatWeeklyValue(metric, value)}
                  </p>
                </div>
              );
            })}
          </div>

          {data.best_sleep && (
            <p className="text-xs text-slate-300" data-testid="weekly-best-sleep">
              Best night: {formatDayLabel(data.best_sleep.day)}
              {data.best_sleep.minutes !== null && `, ${formatMinutes(data.best_sleep.minutes)}`}
              {data.best_sleep.score !== null && ` (score ${data.best_sleep.score})`}
            </p>
          )}

          {data.changes.length > 0 ? (
            <ul className="space-y-1" aria-label="What changed from last week" data-testid="weekly-changes">
              {data.changes.map((change) => {
                const Icon = change.direction === 'up' ? TrendingUp : TrendingDown;
                return (
                  <li key={change.metric} className="flex items-center gap-2 text-xs text-slate-300">
                    <Icon size={13} className="shrink-0 text-sky-300" />
                    {LABELS[change.metric]} {change.direction} from {formatWeeklyValue(change.metric, change.last_week)} to{' '}
                    {formatWeeklyValue(change.metric, change.this_week)}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-slate-500" data-testid="weekly-steady">No big changes from last week.</p>
          )}
        </div>
      )}
    </section>
  );
}
