import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Hourglass, Loader2, Square, Timer } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatClockTime, formatMinutes } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';

/** `HH:MM` today (or yesterday, when that time has not come yet today) as an ISO instant. */
function startedAtFromClock(clock: string, now = new Date()): string {
  const [hours, minutes] = clock.split(':').map(Number);
  const when = new Date(now);
  when.setHours(hours, minutes, 0, 0);
  if (when > now) when.setDate(when.getDate() - 1);
  return when.toISOString();
}

/**
 * The fasting window (adults only, off until switched on in Cards).
 *
 * The person starts and stops a fast; nothing is inferred from food logs,
 * whose times are when they were typed rather than eaten. The card shows the
 * elapsed time against the person's own target, nothing more.
 */
export default function FastingCard() {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<number | null>(null);
  const [startClock, setStartClock] = useState('');

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-fasting'],
    queryFn: () => api.getHealthFasting(),
    refetchInterval: 60_000,
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['health-fasting'] });

  const start = useMutation({
    mutationFn: () =>
      api.startHealthFast({
        target_hours: target,
        ...(startClock ? { started_at: startedAtFromClock(startClock) } : {}),
      }),
    onSuccess: () => {
      setStartClock('');
      refresh();
    },
    onError: (error) => toast.error(errorText(error, 'Could not start the fast')),
  });
  const stop = useMutation({
    mutationFn: () => api.stopHealthFast(),
    onSuccess: (fast) => {
      toast.success(`Fast ended after ${formatMinutes(fast.elapsed_minutes)}`);
      refresh();
    },
    onError: (error) => toast.error(errorText(error, 'Could not stop the fast')),
  });

  if (isLoading) return <div className="h-28 skeleton rounded-2xl" aria-hidden />;
  // A minor's profile answers allowed: false; the card is simply not theirs.
  if (data && !data.allowed) return null;

  const active = data?.active ?? null;
  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="fasting-heading" data-testid="fasting-card">
      <h3 id="fasting-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <Hourglass size={13} className="text-amber-300" />
        Fasting window
      </h3>
      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3">
          <p className="text-xs text-amber-300">The fasting window could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : active ? (
        <div className="mt-2" data-testid="fasting-active">
          <p className="text-2xl font-bold tabular-nums text-white">{formatMinutes(active.elapsed_minutes)}</p>
          <p className="text-[11px] text-slate-500">
            Since {formatClockTime(active.started_at)}
            {active.target_hours
              ? active.reached
                ? ` · your ${active.target_hours} h target is reached`
                : ` · ${formatMinutes(active.remaining_minutes)} to your ${active.target_hours} h target`
              : ' · no target set'}
          </p>
          {active.percent !== null && (
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]" role="progressbar" aria-valuenow={active.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Fasting progress">
              <div className="h-full rounded-full bg-amber-400" style={{ width: `${active.percent}%` }} />
            </div>
          )}
          <button
            type="button"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            data-testid="fasting-stop"
            className="glass-button mt-3 flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
          >
            {stop.isPending ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />}
            End fast
          </button>
        </div>
      ) : (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-slate-400">No fast running. Pick a target if you want one.</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Fasting target">
            {data.presets.map((hours) => (
              <button
                key={hours}
                type="button"
                aria-pressed={target === hours}
                onClick={() => setTarget(target === hours ? null : hours)}
                className={`min-h-11 rounded-xl border px-3 text-xs font-semibold ${
                  target === hours
                    ? 'border-amber-500/40 bg-amber-500/20 text-amber-200'
                    : 'border-white/10 bg-white/[0.03] text-slate-300'
                }`}
              >
                {hours} h
              </button>
            ))}
          </div>
          <label className="block text-[11px] text-slate-500">
            Started at (leave empty for now)
            <input
              type="time"
              value={startClock}
              onChange={(event) => setStartClock(event.target.value)}
              className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={start.isPending}
            data-testid="fasting-start"
            className="glass-button flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
          >
            {start.isPending ? <Loader2 size={13} className="animate-spin" /> : <Timer size={13} />}
            Start fast
          </button>
        </div>
      )}
      {data && data.recent.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-white/5 pt-2 text-[11px] text-slate-400" aria-label="Recent fasts">
          {data.recent.map((fast) => (
            <li key={fast.id} className="flex justify-between gap-2">
              <span>{formatClockTime(fast.started_at)} → {fast.ended_at ? formatClockTime(fast.ended_at) : ''}</span>
              <span className="tabular-nums">{formatMinutes(fast.elapsed_minutes)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
