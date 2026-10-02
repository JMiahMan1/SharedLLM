import { ringSpec, stepZone, ZONE_STYLES } from '../../lib/healthRanges';
import type { RingSpec as ComputedRing } from '../../lib/healthRanges';
import { DEFAULT_STEP_GOAL } from '../../lib/healthMetrics';

export interface RingInput {
  id: string;
  label: string;
  actual: number;
  goal: number | null | undefined;
  unit: string;
  /** Overrides the zone-derived ring colour (e.g. for distance). */
  tone?: 'under' | 'low' | 'on' | 'high';
}

interface Props {
  rings: RingInput[];
  /** The user's own median daily steps, when there is enough history. */
  baseline?: number | null;
  defaultGoal?: number;
  /** True when there is too little history for the zone labels to mean much. */
  thin?: boolean;
  /** How many days a fair comparison needs. From the server, never assumed. */
  baselineMinDays?: number | null;
}

const SIZE = 132;
const STROKE = 10;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** One ring. The arc is drawn in SVG so it scales with its container. */
function Ring({ spec, color, unit }: { spec: ComputedRing; color: string; unit: string }) {
  const dash = (spec.percent / 100) * CIRCUMFERENCE;
  return (
    <div className="relative" style={{ width: SIZE, height: SIZE }}>
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="-rotate-90"
        role="presentation"
        aria-hidden="true"
      >
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          className="stroke-white/10"
        />
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${CIRCUMFERENCE - dash}`}
          className={color}
          style={{ transition: 'stroke-dasharray 400ms ease-out' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold text-white tabular-nums leading-none">
          {spec.actual.toLocaleString()}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-slate-400 mt-1">{unit}</span>
      </div>
    </div>
  );
}

/**
 * Activity rings, the Fitbit pattern.
 *
 * The visual is a bonus, not the message: the percentage and the remainder are
 * always present as text, because a ring alone is unreadable to a screen
 * reader and ambiguous at a glance. The `aria-label` on the group states the
 * same fact the rings draw, so a screen reader is not told less than a sighted
 * user is shown.
 */
export default function ActivityRings({ rings, baseline = null, defaultGoal = DEFAULT_STEP_GOAL, thin = false, baselineMinDays = null }: Props) {
  if (!rings.length) return null;

  const specs = rings.map((r) => {
    const ring = ringSpec(r.actual, r.goal, defaultGoal);
    const zone = r.tone ?? stepZone(r.actual, baseline);
    return { ring, color: ZONE_STYLES[zone].ring, zone, input: r };
  });

  return (
    <div
      data-testid="activity-rings"
      className="flex flex-wrap items-center justify-center gap-6"
      role="group"
      aria-label={specs
        .map((s) => `${s.input.label}: ${s.ring.actual.toLocaleString()} of ${s.ring.goal.toLocaleString()} ${s.input.unit}`)
        .join('; ')}
    >
      {specs.map((s) => (
        <div
          key={s.input.id}
          data-testid={`ring-${s.input.id}`}
          className="flex flex-col items-center gap-2"
        >
          <Ring spec={s.ring} color={s.color} unit={s.input.unit} />
          <div className="text-center">
            <p className="text-xs font-medium text-slate-300">{s.input.label}</p>
            <p className={`text-[11px] font-semibold ${ZONE_STYLES[s.zone].text}`} data-testid={`ring-zone-${s.input.id}`}>
              {ZONE_STYLES[s.zone].label}
            </p>
            {!thin && (
              <p className="text-[10px] text-slate-500 tabular-nums">
                {s.ring.reached
                  ? 'Goal met'
                  : `${s.ring.remaining.toLocaleString()} ${s.input.unit} to go`}
              </p>
            )}
          </div>
        </div>
      ))}

      {thin && (
        <p
          data-testid="rings-thin"
          className="text-[11px] text-slate-400 max-w-[10rem] text-left"
        >
          {baselineMinDays
            ? `Needs ${baselineMinDays} days of history for a fair comparison`
            : 'Not enough history yet for a fair comparison'}
        </p>
      )}
    </div>
  );
}
