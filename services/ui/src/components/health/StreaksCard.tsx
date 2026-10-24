import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Flame } from 'lucide-react';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';

/**
 * Forgiving streaks (Phase 9). One missed day in seven is a rest day and keeps
 * a streak going; today never breaks it while it is still today. The copy
 * celebrates consistency and never calls a day failed.
 */
export default function StreaksCard() {
  const day = useMemo(() => localDay(), []);
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-streaks', day],
    queryFn: () => api.getHealthStreaks(day),
    staleTime: 60_000,
  });

  if (isLoading) return <div className="h-28 skeleton rounded-2xl" aria-hidden />;

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="streaks-heading" data-testid="streaks-card">
      <h3 id="streaks-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <Flame size={13} className="text-orange-300" />
        Streaks
      </h3>
      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3">
          <p className="text-xs text-amber-300">Streaks could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : (
        <>
          <ul className="mt-2 grid grid-cols-2 gap-2">
            {data.habits.map((habit) => (
              <li key={habit.key} className="rounded-xl border border-white/5 bg-white/[0.03] p-3" data-testid={`streak-${habit.key}`} title={habit.detail}>
                <p className="text-[11px] text-slate-400">{habit.title}</p>
                <p className="mt-0.5 text-xl font-bold tabular-nums text-white">
                  {habit.current}
                  <span className="ml-1 text-[11px] font-medium text-slate-500">day{habit.current === 1 ? '' : 's'}</span>
                </p>
                <p className="text-[10px] text-slate-500">
                  {habit.needs
                    ? habit.needs
                    : habit.current === 0
                      ? habit.best > 0
                        ? `Best ${habit.best}. Today is a fine day to start again.`
                        : 'Starts with your first log.'
                      : `${habit.today_done ? 'Today counted' : 'Today still open'}${habit.best > habit.current ? ` · best ${habit.best}` : ''}${
                          habit.rest_days ? ` · ${habit.rest_days} rest day${habit.rest_days === 1 ? '' : 's'}` : ''
                        }`}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] text-slate-500">{data.rule}</p>
        </>
      )}
    </section>
  );
}
