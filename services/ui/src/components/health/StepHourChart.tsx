import { formatCount, hourOfDay } from '../../lib/healthMetrics';
import type { StepHourBucket } from '../../types/api';

interface Props {
  /**
   * The phone's recorded hours, or undefined when it has never reported any.
   * `undefined` is meaningful and is not the same as `[]`: nothing recorded
   * yet means we cannot describe the day, while an empty list would mean a day
   * measured as genuinely empty.
   */
  hours?: StepHourBucket[];
  /** The phone's current local hour, emphasised as "still counting". */
  currentHour?: number;
  className?: string;
}

function barAriaLabel(b: StepHourBucket): string {
  return `${hourOfDay(b.hour)}: ${formatCount(b.steps)} steps`;
}

/**
 * Today's steps, one bar per hour.
 *
 * The phone records hours only once it has seen a pedometer delta, so a day
 * with no hours is *no detail*, not a sedentary day. This renders that as an
 * honest message naming the cause, because a flat empty chart would read as
 * "you did not move" and be believed.
 *
 * Hours past the current hour are drawn as empty slots with a dashed edge: the
 * day is not over, and drawing them as zero would claim the future already
 * happened.
 */
export default function StepHourChart({ hours, currentHour, className = '' }: Props) {
  if (!hours?.length) {
    return (
      <p data-testid="hour-chart-missing" className="text-xs text-slate-400 leading-relaxed">
        No hourly detail yet. Your phone starts recording steps by the hour once it
        has the current app build — until then only the daily total is available.
      </p>
    );
  }

  const byHour = new Map(hours.map((h) => [h.hour, h]));
  const now = typeof currentHour === 'number' ? currentHour : new Date().getHours();
  const peak = Math.max(1, ...hours.map((h) => h.steps));
  const slots = Array.from({ length: 24 }, (_, hour) => byHour.get(hour) ?? null);
  const busiest = hours.reduce((a, b) => (b.steps > a.steps ? b : a));

  return (
    <figure data-testid="step-hour-chart" className={className}>
      <div
        className="flex items-end gap-[2px] h-20"
        role="group"
        aria-label="Steps by hour today"
      >
        {slots.map((bucket, hour) => {
          const future = hour > now;
          const height = bucket ? Math.max(3, Math.round((bucket.steps / peak) * 100)) : 0;
          return (
            <div
              key={hour}
              data-testid={`hour-bar-${hour}`}
              data-state={future ? 'ahead' : bucket ? 'recorded' : 'quiet'}
              data-current={hour === now ? 'true' : 'false'}
              className="flex-1 min-w-[2px] flex flex-col justify-end h-full"
              title={bucket ? barAriaLabel(bucket) : future ? 'Later today' : 'No steps recorded'}
            >
              {/* Only a recorded hour gets height. An hour with no reading and an
                  hour that has not happened yet are both drawn as a flat stub at
                  the baseline, so nothing can be misread as a quieter-than-it-is
                  walk just by comparing bar heights. */}
              <div
                className={
                  bucket
                    ? 'w-full rounded-sm bg-gradient-to-t from-cyan-500/70 to-cyan-400'
                    : future
                      ? 'w-full rounded-sm border-b border-dashed border-white/20'
                      : // The hour you are in right now is the one still filling up,
                        // so it gets a brighter mark than the hours already behind it.
                        `w-full rounded-sm ${hour === now ? 'bg-white/40' : 'bg-white/[0.08]'}`
                }
                style={{ height: bucket ? `${height}%` : '3px' }}
              />
            </div>
          );
        })}
      </div>

      <figcaption
        className="flex items-center justify-between mt-2 text-[10px] text-slate-500"
        data-testid="hour-chart-caption"
      >
        <span>{hourOfDay(0)}</span>
        <span data-testid="hour-chart-busiest">
          Busiest {hourOfDay(busiest.hour)} · {formatCount(busiest.steps)} steps
        </span>
        <span>{hourOfDay(23)}</span>
      </figcaption>

      {/* The bars are decorative; the hours they draw are listed for anyone who
          cannot see them. Only recorded hours are listed — an hour with no
          reading is not a fact worth announcing. */}
      <ul className="sr-only">
        {hours.map((b) => (
          <li key={`sr-${b.hour}`}>{barAriaLabel(b)}</li>
        ))}
      </ul>
    </figure>
  );
}