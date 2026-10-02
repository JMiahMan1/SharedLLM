import { bucketAriaLabel, bucketHeightPercent, isGap, peakBucket } from '../../lib/healthRanges';
import type { StepRangeBucket } from '../../types/api';

interface Props {
  buckets: StepRangeBucket[];
  /** Highlights today, which is still in progress. */
  partial?: number;
  className?: string;
}

// A gap is drawn faintly and hatched rather than as a zero-height bar. The
// colour is a literal rather than a theme token so a gap is legible on any of
// the site's themes without a token per theme per surface.
const GAP_STROKE = 'rgba(251, 191, 36, 0.5)';
const GAP_EDGE = 'rgba(251, 191, 36, 0.3)';

/**
 * Step history as a bar chart.
 *
 * The distinction that matters: a bar drawn as **zero** and a bar drawn as
 * **empty/hatched** mean different things. The first is a day the phone
 * reported no steps; the second is a day we never heard from, which is missing
 * data, not inactivity. Collapsing the two would let a silent sensor look like
 * a lazy user -- which is the exact failure the self-healing sensor work exists
 * to prevent being invisible.
 */
export default function StepHistoryChart({ buckets, partial = 0, className = '' }: Props) {
  // A single bucket is not a trend -- it would render as one bar stretched
  // across the full width, which reads as a solid block rather than history.
  // The caller shows the number itself, so there is nothing to add here.
  if (buckets.length < 2) return null;
  const peak = Math.max(peakBucket(buckets), partial, 1);
  const gapCount = buckets.filter(isGap).length;

  return (
    <figure data-testid="step-history-chart" className={className}>
      <div
        className="flex items-end gap-[2px] h-24 sm:h-32"
        role="group"
        aria-label={`Daily steps for ${buckets.length} ${buckets.length === 1 ? 'day' : 'days'}`}
      >
        {buckets.map((b) => {
          const gap = isGap(b);
          const height = gap ? 100 : bucketHeightPercent(b, peak);
          return (
            <div
              key={b.start}
              data-testid={`bar-${b.start}`}
              data-gap={gap ? 'true' : 'false'}
              className="flex-1 min-w-[3px] flex flex-col justify-end h-full"
              title={bucketAriaLabel(b)}
            >
              <div
                className={
                  gap
                    ? 'w-full rounded-sm opacity-40'
                    : 'w-full rounded-sm bg-gradient-to-t from-purple-500/70 to-purple-400'
                }
                style={
                  gap
                    ? {
                        height: '100%',
                        backgroundImage: `repeating-linear-gradient(135deg, ${GAP_STROKE} 0 2px, transparent 2px 5px)`,
                        border: `1px solid ${GAP_EDGE}`,
                      }
                    : { height: `${Math.max(height, 3)}%` }
                }
              />
            </div>
          );
        })}
        {partial > 0 && (
          <div
            data-testid="bar-partial"
            className="flex-1 min-w-[3px] flex flex-col justify-end h-full"
            title="Today, still counting"
          >
            <div
              className="w-full rounded-sm bg-gradient-to-t from-amber-500/60 to-amber-400 border border-dashed border-amber-300/40"
              style={{ height: `${Math.max(4, Math.round((partial / peak) * 100))}%` }}
            />
          </div>
        )}
      </div>

      <figcaption className="flex items-center justify-between mt-2 text-[10px] text-slate-500">
        <span>{buckets[0]?.label}</span>
        {gapCount > 0 ? (
          <span data-testid="chart-gap-note" className="text-amber-300/80">
            {gapCount} {gapCount === 1 ? 'day has' : 'days have'} no reading
          </span>
        ) : (
          <span>Every day has a reading</span>
        )}
        <span>{buckets[buckets.length - 1]?.label}</span>
      </figcaption>

      {/* The bars are decorative; the facts they draw are listed for anyone
          who cannot see them, including the gaps. */}
      <ul className="sr-only">
        {buckets.map((b) => (
          <li key={`sr-${b.start}`}>{bucketAriaLabel(b)}</li>
        ))}
      </ul>
    </figure>
  );
}
