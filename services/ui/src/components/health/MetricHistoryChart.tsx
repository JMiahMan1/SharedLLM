import {
  metricBarPercent,
  metricBucketAriaLabel,
  metricMax,
  type MetricFormat,
} from '../../lib/healthMetricsRanges';
import type { MetricRangeBucket } from '../../types/api';

interface Props {
  buckets: MetricRangeBucket[];
  format: MetricFormat;
  /** Used for the chart's own accessible name, e.g. "Workouts". */
  metricLabel: string;
  className?: string;
}

/**
 * Event-metric history as a bar chart.
 *
 * The counterpart to `StepHistoryChart`, and deliberately *simpler*: a day with
 * no workout is a real zero, not a gap, so there is no hatching and no
 * "no reading" caption. A quiet month is a row of flat bars, which is the truth
 * of it -- and borrowing the steps treatment would paint "sensor stopped
 * reporting" across a fortnight in which nothing happened.
 */
export default function MetricHistoryChart({ buckets, format, metricLabel, className = '' }: Props) {
  // One bar stretched full width is a block, not a trend. The caller shows the
  // total itself, so there is nothing to add.
  if (buckets.length < 2) return null;
  const max = metricMax(buckets);
  const activeDays = buckets.reduce((n, b) => n + b.active_days, 0);

  return (
    <figure data-testid="metric-history-chart" className={className}>
      <div
        className="flex items-end gap-[2px] h-20 sm:h-24"
        role="group"
        aria-label={`${metricLabel} across ${buckets.length} buckets`}
      >
        {buckets.map((b) => (
          <div
            key={b.start}
            data-testid={`metric-bar-${b.start}`}
            data-quiet={b.quiet ? 'true' : 'false'}
            className="flex-1 min-w-[3px] flex flex-col justify-end h-full"
            title={metricBucketAriaLabel(b, format)}
          >
            {b.quiet ? (
              // A flat baseline rule: present, and visibly zero. Not hidden --
              // hiding it would imply the day is unknown.
              <div className="w-full rounded-sm bg-white/10" style={{ height: '2px' }} />
            ) : (
              <div
                className="w-full rounded-sm bg-gradient-to-t from-emerald-500/70 to-emerald-400"
                style={{ height: `${metricBarPercent(b.value, max)}%` }}
              />
            )}
          </div>
        ))}
      </div>

      <figcaption className="flex items-center justify-between mt-2 text-[10px] text-slate-500">
        <span>{buckets[0]?.label}</span>
        <span data-testid="metric-chart-days">
          {activeDays} active {activeDays === 1 ? 'day' : 'days'}
        </span>
        <span>{buckets[buckets.length - 1]?.label}</span>
      </figcaption>

      {/* The bars are decorative; the facts they draw are listed for anyone who
          cannot see them. */}
      <ul className="sr-only">
        {buckets.map((b) => (
          <li key={`sr-${b.start}`}>{metricBucketAriaLabel(b, format)}</li>
        ))}
      </ul>
    </figure>
  );
}
