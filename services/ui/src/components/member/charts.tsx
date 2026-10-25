import { Bar, BarChart, Cell, PolarAngleAxis, RadialBar, RadialBarChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { duration } from '../../lib/phoneUsageFormat';
import type { HeatCell } from '../../lib/memberPage';

/*
 * The family member page's charts, one visual system (docs/FAMILY_MEMBER_PAGE.md):
 * one sequential hue (sky) for the measure, amber with a word for "over the
 * limit" (a status, never colour alone), a dashed ink line for a limit and a
 * dotted one for an average, recessive axes, a tooltip on every mark, and a
 * text summary beside each chart for anyone who cannot see it.
 */
const MEASURE = '#38bdf8'; // sky-400
const OVER = '#f59e0b'; // amber-500, always paired with the word "over"
const INK = '#cbd5e1'; // slate-300
const MUTED = '#64748b'; // slate-500
const TRACK = 'rgba(148, 163, 184, 0.18)';

/** Today's screen time against the limit, as a ring (a small one for the member strip). */
export function TimeRing({ used, limit, size = 120, label = true }: { used: number; limit: number | null; size?: number; label?: boolean }) {
  const over = limit !== null && used > limit;
  const share = limit ? Math.min(100, Math.round((used / Math.max(1, limit)) * 100)) : used > 0 ? 100 : 0;
  const summary = limit === null ? `${duration(used * 60000)} today, no limit` : `${duration(used * 60000)} of ${duration(limit * 60000)}${over ? ', over the limit' : ''}`;
  const thickness = Math.max(4, Math.round(size / 9));
  return (
    <figure className="relative shrink-0" style={{ width: size, height: size }} aria-label={summary} role="img" data-testid="time-ring">
      <RadialBarChart
        width={size}
        height={size}
        innerRadius={size / 2 - thickness - 2}
        outerRadius={size / 2 - 2}
        startAngle={90}
        endAngle={-270}
        data={[{ value: share }]}
      >
        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} axisLine={false} />
        <RadialBar dataKey="value" cornerRadius={thickness} background={{ fill: TRACK }} fill={over ? OVER : MEASURE} isAnimationActive={false} />
      </RadialBarChart>
      {label && (
        <figcaption className="absolute inset-0 flex flex-col items-center justify-center text-center leading-tight">
          <span className="text-sm font-semibold text-white">{duration(used * 60000)}</span>
          <span className="text-[10px] text-slate-400">{limit === null ? 'no limit' : over ? 'over the limit' : `of ${duration(limit * 60000)}`}</span>
        </figcaption>
      )}
    </figure>
  );
}

export interface DailyPoint {
  key: string;
  label: string;
  /** Null when nothing was reported: drawn as a gap, never as zero. */
  value: number | null;
  over?: boolean;
}

interface DailyBarsProps {
  data: DailyPoint[];
  /** How a value reads in the tooltip and summary. */
  format: (value: number) => string;
  limit?: number | null;
  /** A shorter form for the axis ticks (defaults to format). */
  tickFormat?: (value: number) => string;
  /** What the line is called: a limit for screen time, a goal for steps. */
  limitLabel?: string;
  average?: number | null;
  testId: string;
  /** The chart's own accessible name, e.g. "Screen time, last 7 days". */
  title: string;
  height?: number;
}

/** Day-by-day bars with an optional limit line and average line. */
export function DailyBars({ data, format, tickFormat = format, limit = null, limitLabel = 'limit', average = null, testId, title, height = 160 }: DailyBarsProps) {
  const reported = data.filter((d) => d.value !== null);
  const rows = data.map((d) => ({ ...d, shown: d.value ?? 0 }));
  return (
    <figure className="space-y-2" data-testid={testId}>
      <div style={{ height }} role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: -18 }} barCategoryGap="22%">
            <XAxis dataKey="label" tick={{ fill: MUTED, fontSize: 10 }} axisLine={false} tickLine={false} interval={0} />
            <YAxis tick={{ fill: MUTED, fontSize: 10 }} axisLine={false} tickLine={false} width={48} allowDecimals={false} tickFormatter={(v: number) => tickFormat(v)} />
            <Tooltip
              cursor={{ fill: 'rgba(148,163,184,0.08)' }}
              content={({ active, payload }) => {
                const point = active ? (payload?.[0]?.payload as DailyPoint | undefined) : undefined;
                if (!point) return null;
                return (
                  <div className="rounded-lg border border-white/10 bg-slate-900/95 px-2 py-1 text-[11px] text-slate-100">
                    {point.label}: {point.value === null ? 'nothing reported' : `${format(point.value)}${point.over ? ' (over the limit)' : ''}`}
                  </div>
                );
              }}
            />
            <Bar dataKey="shown" radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {rows.map((d) => (
                <Cell key={d.key} fill={d.over ? OVER : MEASURE} fillOpacity={d.value === null ? 0 : 1} />
              ))}
            </Bar>
            {limit !== null && <ReferenceLine y={limit} stroke={INK} strokeDasharray="4 3" strokeWidth={1.5} label={{ value: limitLabel, position: 'insideTopRight', fill: INK, fontSize: 10 }} />}
            {average !== null && <ReferenceLine y={average} stroke={MUTED} strokeDasharray="1 3" strokeWidth={2} label={{ value: 'avg', position: 'insideBottomRight', fill: MUTED, fontSize: 10 }} />}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400" data-testid={`${testId}-summary`}>
        {average !== null && <span>Average {format(average)}</span>}
        {limit !== null && <span>{limitLabel.charAt(0).toUpperCase() + limitLabel.slice(1)} {format(limit)}</span>}
        <span>{reported.length} of {data.length} days reported</span>
        {data.some((d) => d.over) && <span className="text-amber-300">Over the limit on {data.filter((d) => d.over).length}</span>}
      </figcaption>
      <ul className="sr-only">
        {data.map((d) => (
          <li key={d.key}>{d.label}: {d.value === null ? 'nothing reported' : format(d.value)}</li>
        ))}
      </ul>
    </figure>
  );
}

const HOUR_TICKS = [0, 6, 12, 18];

/** An hour-by-day grid (rows are days, oldest first), shaded by how much happened. */
export function HourHeatmap({ days, cells, max, title, testId, dayLabel }: {
  days: string[];
  cells: HeatCell[];
  max: number;
  title: string;
  testId: string;
  dayLabel: (day: string) => string;
}) {
  const at = new Map(cells.map((c) => [`${c.day}|${c.hour}`, c.count]));
  const hour = (h: number) => new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: 'numeric' });
  return (
    <figure className="space-y-1" data-testid={testId} aria-label={title}>
      <div className="grid gap-[2px]" style={{ gridTemplateColumns: 'minmax(2.5rem, auto) repeat(24, minmax(0, 1fr))' }}>
        {days.map((day) => (
          <div key={day} className="contents">
            <span className="pr-1 text-[9px] leading-3 text-slate-500 truncate">{dayLabel(day)}</span>
            {Array.from({ length: 24 }, (_, h) => {
              const count = at.get(`${day}|${h}`) || 0;
              const shade = max ? 0.15 + 0.85 * (count / max) : 0;
              return (
                <span
                  key={h}
                  title={`${dayLabel(day)} ${hour(h)}: ${count} recorded`}
                  className={`h-3 rounded-[2px] ${count ? 'bg-sky-400' : 'bg-white/5'}`}
                  style={count ? { opacity: shade } : undefined}
                  data-count={count}
                />
              );
            })}
          </div>
        ))}
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="text-[9px] text-slate-500">{HOUR_TICKS.includes(h) ? hour(h) : ''}</span>
        ))}
      </div>
      <figcaption className="text-[11px] text-slate-400">{title}</figcaption>
    </figure>
  );
}
