import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Zap } from 'lucide-react';
import { api } from '../../services/api';
import { formatDayLabel, localDay } from '../../lib/healthHub';
import { activeMinutesLine, SOURCE_LABELS } from '../../lib/activeMinutes';
import PlanWorkoutForm from './PlanWorkoutForm';

const RING = 96;
const STROKE = 9;

/**
 * The weekly cardio target (Phase 3): Active-Zone-like minutes this week
 * against the person's own weekly target, with a bar per day.
 *
 * Minutes come from a watch's own totals, heart-rate zones, workouts and
 * brisk-walking hours, best source first (see services/common/active_minutes.py).
 * No target means no ring percentage, only the minutes, and a prompt to set one.
 */
export default function ActiveMinutesCard() {
  const day = useMemo(() => localDay(), []);
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-active-minutes', day],
    queryFn: () => api.getHealthActiveMinutes(day),
    staleTime: 120_000,
  });

  if (isLoading) return <div className="h-40 skeleton rounded-2xl" aria-hidden />;

  const radius = (RING - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;
  const percent = data?.percent ?? 0;
  const maxDay = Math.max(1, ...(data?.days ?? []).map((entry) => entry.minutes ?? 0));

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="active-minutes-heading" data-testid="active-minutes-card">
      <div className="flex items-center justify-between gap-2">
        <h3 id="active-minutes-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Zap size={13} className="text-amber-300" />
          Active minutes this week
        </h3>
        {data && <span className="text-[10px] text-slate-500">{formatDayLabel(data.week_start)} – {formatDayLabel(data.week_end)}</span>}
      </div>

      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3">
          <p className="text-xs text-amber-300">Active minutes could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : (
        <>
          <div className="mt-3 flex items-center gap-4">
            <div className="relative shrink-0" style={{ width: RING, height: RING }}>
              <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} role="img" aria-label={activeMinutesLine(data)}>
                <circle cx={RING / 2} cy={RING / 2} r={radius} fill="none" stroke="currentColor" strokeWidth={STROKE} className="text-white/[0.06]" />
                {data.percent !== null && (
                  <circle
                    cx={RING / 2}
                    cy={RING / 2}
                    r={radius}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={STROKE}
                    strokeLinecap="round"
                    strokeDasharray={`${(percent / 100) * circumference} ${circumference}`}
                    transform={`rotate(-90 ${RING / 2} ${RING / 2})`}
                    className="text-amber-400"
                  />
                )}
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-xl font-bold tabular-nums text-white">{data.total_minutes ?? '—'}</span>
                <span className="text-[9px] uppercase tracking-wider text-slate-500">min</span>
              </div>
            </div>
            <div className="min-w-0 text-xs">
              <p className="text-slate-200" data-testid="active-minutes-line">{activeMinutesLine(data)}</p>
              {data.target_minutes === null && (
                <p className="mt-1 text-[11px] text-slate-500">
                  Set a weekly target in Health → Health. Guidelines suggest 150 minutes a week for adults and
                  an hour a day for children.
                </p>
              )}
              {data.sources.length > 0 && (
                <p className="mt-1 text-[10px] text-slate-500">From {data.sources.map((source) => SOURCE_LABELS[source]).join(', ')}</p>
              )}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-7 gap-1" aria-label="Active minutes by day">
            {data.days.map((entry) => (
              <div key={entry.day} className="flex flex-col items-center gap-1">
                <div className="flex h-10 w-full items-end overflow-hidden rounded bg-white/[0.04]">
                  {entry.minutes !== null && (
                    <div className="w-full rounded bg-amber-400/80" style={{ height: `${Math.round((entry.minutes / maxDay) * 100)}%` }} />
                  )}
                </div>
                <span className="text-[9px] text-slate-500">
                  {new Date(`${entry.day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'narrow' })}
                </span>
              </div>
            ))}
          </div>
          {[...data.notes, ...data.unavailable.map((name) => `${name === 'steps' ? 'Steps' : 'Workouts'} could not be read just now.`)].map((note) => (
            <p key={note} className="mt-2 text-[10px] text-slate-500">
              {note}
            </p>
          ))}
        </>
      )}
      <PlanWorkoutForm />
    </section>
  );
}
