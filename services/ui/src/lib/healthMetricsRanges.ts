import type { MetricRangeBucket, MetricRangeResponse } from '../types/api';

/**
 * Formatting and chart scaling for event metrics (workouts, distances).
 *
 * The rule that shapes everything here: for steps, "no reading" and "zero" are
 * different facts, so a missing day is drawn as a gap. For an event metric
 * there is nothing to miss -- a day with no workout simply had no workout, and
 * that is a real zero. So there is no gap vocabulary at all, and a quiet month
 * renders as a row of empty bars rather than as a broken sensor.
 */

export type MetricFormat = 'count' | 'duration' | 'distance';

/** How a value reads, per the server's `format` field. */
export function formatMetricValue(value: number, format: MetricFormat): string {
  if (!Number.isFinite(value)) return '—';
  if (format === 'duration') {
    const mins = Math.round(value);
    if (mins < 60) return `${mins} min`;
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  if (format === 'distance') return `${value.toFixed(1)} mi`;
  return String(Math.round(value));
}

/**
 * The average line, labelled for humans.
 *
 * Averaged over days something happened, never across the calendar: "0.4
 * workouts a day" is a number nobody wants to read.
 */
export function metricAverageLabel(series: MetricRangeResponse): string {
  if (series.empty) return 'No activity yet';
  return `${formatMetricValue(series.per_active_day, series.format as MetricFormat)} per active day`;
}

/**
 * Bar height as a percentage of the tallest bar in the window.
 *
 * Floored at 4% so a single small day is still a visible mark rather than
 * invisible, and returns 0 for a genuinely quiet bucket so quiet and tiny stay
 * distinguishable.
 */
export function metricBarPercent(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.max(4, Math.round((value / max) * 100));
}

/** The tallest value in the window, ignoring quiet buckets. */
export function metricMax(buckets: MetricRangeBucket[]): number {
  return buckets.reduce((m, b) => Math.max(m, b.value), 0);
}

/**
 * One bucket's description.
 *
 * A quiet bucket says "no activity", not "0" -- "0 workouts" reads like a
 * measurement that came back empty, which is a different and more worrying
 * claim than a day nothing happened on.
 */
export function metricBucketAriaLabel(bucket: MetricRangeBucket, format: MetricFormat): string {
  if (bucket.quiet) return `${bucket.label}: no activity`;
  return `${bucket.label}: ${formatMetricValue(bucket.value, format)}`;
}

/** A one-line summary of the window, or null when there is nothing to say. */
export function metricSummary(series: MetricRangeResponse): string | null {
  if (series.empty) return null;
  const total = formatMetricValue(series.total, series.format as MetricFormat);
  const days = series.active_days === 1 ? '1 active day' : `${series.active_days} active days`;
  return `${total} across ${days}`;
}

/** True when the metric has data anywhere in the window. */
export function hasMetricActivity(series: MetricRangeResponse | undefined): boolean {
  return !!series && !series.empty;
}
