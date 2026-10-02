import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Dumbbell, Clock, Route } from 'lucide-react';
import { api } from '../../services/api';
import {
  formatMetricValue,
  metricAverageLabel,
  metricSummary,
  type MetricFormat,
} from '../../lib/healthMetricsRanges';
import MetricHistoryChart from './MetricHistoryChart';
import type { StepRange } from '../../types/api';

interface Props {
  range: StepRange;
  userId?: string;
}

/** Per-metric presentation. The keys must match the server's `available` list. */
const CARDS: {
  key: string;
  title: string;
  icon: typeof Dumbbell;
  accent: string;
}[] = [
  { key: 'workouts', title: 'Workouts', icon: Dumbbell, accent: 'text-emerald-300' },
  { key: 'workout_minutes', title: 'Workout time', icon: Clock, accent: 'text-purple-300' },
  { key: 'workout_miles', title: 'Workout distance', icon: Route, accent: 'text-sky-300' },
];

/**
 * Metrics the server records but this panel does not show, because they belong
 * to another surface. Driving distance moved to Wander, next to fuel and cost,
 * which is where a person looks for miles-per-gallon -- showing the same number
 * in two places made it read as two different quantities.
 */
const SHOWN_ELSEWHERE = new Set(['drive_miles']);

/**
 * One card per recorded event metric, each with its own chart.
 *
 * Only metrics the server actually records are drawn. Calories and the rest are
 * listed at the bottom *with the reason they are absent*, because "Calories"
 * simply missing looks like a bug, whereas "not recorded: nothing writes the
 * field" tells the reader something true.
 */
export default function MetricDetailCards({ range, userId }: Props) {
  // 'all' means the server figures out the viewer, so it is passed through
  // rather than turned into an explicit target.
  const target = userId && userId !== 'all' ? userId : undefined;

  const catalog = useQuery({
    queryKey: ['metric-catalog'],
    queryFn: () => api.getMetricCatalog(),
    staleTime: 60 * 60 * 1000,
  });

  return (
    <section data-testid="metric-detail-cards" className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      {(catalog.data?.available ?? [])
        .filter((key: string) => !SHOWN_ELSEWHERE.has(key))
        .map((key: string) => {
          const card = CARDS.find((c) => c.key === key);
          if (!card) return null;
          return (
            <MetricCard
              key={key}
              metricKey={key}
              title={card.title}
              icon={card.icon}
              accent={card.accent}
              range={range}
              userId={target}
            />
          );
        })}

      {catalog.data?.unavailable && Object.keys(catalog.data.unavailable).length > 0 && (
        <UnavailableMetrics reasons={catalog.data.unavailable} />
      )}

      {SHOWN_ELSEWHERE.size > 0 && (
        <p
          data-testid="metric-moved-note"
          className="sm:col-span-2 xl:col-span-4 text-[11px] text-slate-500"
        >
          Driving distance is on the Wander page, next to fuel and cost.
        </p>
      )}
    </section>
  );
}

function MetricCard({
  metricKey,
  title,
  icon: Icon,
  accent,
  range,
  userId,
}: {
  metricKey: string;
  title: string;
  icon: typeof Dumbbell;
  accent: string;
  range: StepRange;
  userId?: string;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['metric-ranges', metricKey, range, userId ?? 'self'],
    queryFn: () => api.getMetricRanges(metricKey, userId, range),
  });

  if (isLoading) {
    return (
      <div data-testid={`metric-card-${metricKey}`} className="glass-panel rounded-2xl p-4 h-32 animate-pulse" />
    );
  }
  if (!data) return null;

  const format = (data.format || 'count') as MetricFormat;
  const summary = metricSummary(data);

  return (
    <article
      data-testid={`metric-card-${metricKey}`}
      data-empty={data.empty ? 'true' : 'false'}
      className="glass-panel rounded-2xl p-4 space-y-2"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
          <Icon size={14} className={accent} />
          {title}
        </h3>
      </div>

      {data.empty ? (
        // An empty window is stated, not charted as a row of zeros.
        <p data-testid={`metric-empty-${metricKey}`} className="text-2xl font-bold text-slate-600">
          None yet
        </p>
      ) : (
        <>
          <p data-testid={`metric-total-${metricKey}`} className="text-2xl font-bold text-white tabular-nums">
            {formatMetricValue(data.total, format)}
          </p>
          <p className="text-[11px] text-slate-500">{metricAverageLabel(data)}</p>
          <MetricHistoryChart
            buckets={data.buckets}
            format={format}
            metricLabel={title}
            className="pt-1"
          />
          {data.best && (
            <p className="text-[10px] text-slate-500">
              Best: {formatMetricValue(data.best.value, format)} on {data.best.label}
            </p>
          )}
        </>
      )}
      {summary && <p className="sr-only">{summary}</p>}
    </article>
  );
}

/**
 * Metrics a reader would expect, with the reason each is missing.
 *
 * Shown rather than hidden: an absent Calories card reads as a broken feature,
 * and this turns it into an accurate statement about the data we hold.
 */
function UnavailableMetrics({ reasons }: { reasons: Record<string, string> }) {
  const entries = Object.entries(reasons);
  return (
    <div
      data-testid="metric-unavailable"
      className="sm:col-span-2 xl:col-span-4 rounded-2xl border border-white/10 bg-white/5 p-3.5"
    >
      <p className="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
        <AlertTriangle size={14} className="text-slate-500" />
        Not tracked yet
      </p>
      <ul className="mt-2 space-y-1">
        {entries.map(([key, reason]) => (
          <li key={key} className="text-[11px] text-slate-500">
            <span className="text-slate-400 font-medium capitalize">{key.replace(/_/g, ' ')}</span> — {reason}
          </li>
        ))}
      </ul>
    </div>
  );
}
