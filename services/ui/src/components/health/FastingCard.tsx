import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Hourglass, Loader2, Square, Timer } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { FastKind } from '../../types/api';
import { formatMinutes } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import { fastClock, formatCountdown, localWhen, startedAtFromClock } from '../../lib/fastingClock';
import FastingGuide from './FastingGuide';

/** An instant as a `datetime-local` value in this device's time. */
function localInput(iso: string): string {
  const at = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** A clock that ticks once a second while something is counting. */
function useNow(running: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return now;
}

function patternLabel(kind: FastKind): string {
  const p = kind.pattern;
  if (p.type === 'until') return `until ${new Date(`2000-01-01T${p.clock}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
  if (p.type === 'days') return `${p.days} days`;
  return `${p.hours} h`;
}

/**
 * Fasting (adults only, off until switched on in Cards).
 *
 * Choose one of the fasts the early church kept (or your own window), say
 * what it is for, and whether those you share it with are asked to pray. A
 * running fast counts down to its planned end every second, with Scripture,
 * the Fathers and the hours of prayer underneath.
 */
export default function FastingCard() {
  const queryClient = useQueryClient();
  const [kindKey, setKindKey] = useState<string>('station');
  const [hours, setHours] = useState<number | null>(null);
  const [until, setUntil] = useState('');
  const [days, setDays] = useState<number | null>(null);
  const [intention, setIntention] = useState('');
  const [notifyShared, setNotifyShared] = useState(true);
  const [prayerReminders, setPrayerReminders] = useState(true);
  const [startClock, setStartClock] = useState('');
  /** The running fast's start being corrected, as a `datetime-local` value. */
  const [correcting, setCorrecting] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-fasting'],
    queryFn: () => api.getHealthFasting(),
    refetchInterval: 60_000,
  });
  const traditions = useQuery({
    queryKey: ['fasting-traditions'],
    queryFn: () => api.getFastingTraditions(),
    staleTime: Infinity,
    enabled: Boolean(data?.allowed),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['health-fasting'] });
  const kinds = traditions.data?.kinds ?? [];
  const chosen = kinds.find((k) => k.key === kindKey) ?? null;
  const active = data?.active ?? null;
  const activeKind = active?.kind ? kinds.find((k) => k.key === active.kind) ?? null : null;
  const now = useNow(Boolean(active));
  const clock = useMemo(() => (active ? fastClock(active, now) : null), [active, now]);

  // Choosing a kind resets the length to that kind's own.
  const choose = (key: string) => {
    setKindKey(key);
    setHours(null);
    setUntil('');
    setDays(null);
  };

  const start = useMutation({
    mutationFn: () => {
      const p = chosen?.pattern;
      return api.startHealthFast({
        kind: kindKey,
        target_hours: p?.type === 'hours' ? hours ?? p.hours : null,
        until: p?.type === 'until' ? until || p.clock : null,
        days: p?.type === 'days' ? days ?? p.days : null,
        intention: intention.trim() || null,
        notify_shared: notifyShared,
        prayer_reminders: prayerReminders,
        ...(startClock ? { started_at: startedAtFromClock(startClock) } : {}),
      });
    },
    onSuccess: () => {
      setStartClock('');
      setIntention('');
      refresh();
    },
    onError: (error) => toast.error(errorText(error, 'Could not start the fast')),
  });
  const adjust = useMutation({
    mutationFn: (value: string) => api.adjustHealthFast(new Date(value).toISOString()),
    onSuccess: () => {
      setCorrecting(null);
      toast.success('Start time corrected');
      refresh();
    },
    onError: (error) => toast.error(errorText(error, 'Could not change the start time')),
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

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="fasting-heading" data-testid="fasting-card">
      <h3 id="fasting-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <Hourglass size={13} className="text-amber-300" />
        Fasting
      </h3>
      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3">
          <p className="text-xs text-amber-300">Fasting could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : active && clock ? (
        <div className="mt-2" data-testid="fasting-active">
          <p className="text-xs font-semibold text-amber-200">{active.kind_name ?? 'Fast'}</p>
          {clock.remainingSeconds !== null ? (
            <>
              <p className="text-3xl font-bold tabular-nums text-white" data-testid="fasting-remaining">
                {clock.reached ? 'Complete' : formatCountdown(clock.remainingSeconds)}
              </p>
              <p className="text-[11px] text-slate-400">
                {clock.reached ? 'Thanks be to God. Break it gently, then end the fast.' : 'left'}
                {clock.day && active.days ? ` · day ${Math.min(clock.day, active.days)} of ${active.days}` : ''}
              </p>
            </>
          ) : (
            <p className="text-3xl font-bold tabular-nums text-white" data-testid="fasting-elapsed">{formatCountdown(clock.elapsedSeconds)}</p>
          )}
          <p className="mt-1 text-[11px] text-slate-500" data-testid="fasting-times">
            Began {localWhen(active.started_at, now)}
            {clock.ends ? ` · ends ${localWhen(clock.ends, now)}` : ' · no end set'}
            {clock.remainingSeconds !== null ? ` · ${formatCountdown(clock.elapsedSeconds)} so far` : ''}
            {correcting === null && (
              <button
                type="button"
                onClick={() => setCorrecting(localInput(active.started_at))}
                className="ml-1.5 inline-flex min-h-11 items-center text-amber-300 hover:text-amber-200"
                data-testid="fasting-correct-start"
              >
                Change
              </button>
            )}
          </p>
          {correcting !== null && (
            <form
              className="mt-2 flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (correcting) adjust.mutate(correcting);
              }}
            >
              <label className="block min-w-0 flex-1 text-[11px] text-slate-500">
                Began
                <input
                  type="datetime-local"
                  value={correcting}
                  max={localInput(new Date().toISOString())}
                  onChange={(e) => setCorrecting(e.target.value)}
                  className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
                  data-testid="fasting-correct-input"
                />
              </label>
              <button type="submit" disabled={adjust.isPending || !correcting} className="glass-button min-h-11 px-3 text-xs disabled:opacity-50">
                Save
              </button>
              <button type="button" onClick={() => setCorrecting(null)} className="min-h-11 px-2 text-xs text-slate-400">
                Cancel
              </button>
            </form>
          )}
          {activeKind?.pattern.type === 'days' ? <p className="mt-1 text-[11px] text-slate-400">Each day: {activeKind.pattern.daily}</p> : null}
          {active.intention ? <p className="mt-1 text-[12px] text-slate-300">Praying for: {active.intention}</p> : null}
          {clock.percent !== null && (
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]" role="progressbar" aria-valuenow={clock.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Fasting progress">
              <div className="h-full rounded-full bg-amber-400 transition-[width]" style={{ width: `${clock.percent}%` }} />
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
          {traditions.data && <FastingGuide traditions={traditions.data} kind={activeKind} active />}
        </div>
      ) : (
        <div className="mt-2 space-y-3">
          <div role="radiogroup" aria-label="Kind of fast" className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {kinds.map((kind) => (
              <button
                key={kind.key}
                type="button"
                role="radio"
                aria-checked={kindKey === kind.key}
                onClick={() => choose(kind.key)}
                data-testid={`fast-kind-${kind.key}`}
                className={`rounded-xl border px-3 py-2 text-left ${
                  kindKey === kind.key ? 'border-amber-500/40 bg-amber-500/15' : 'border-white/10 bg-white/[0.03] hover:border-white/20'
                }`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-semibold text-slate-100">{kind.name}</span>
                  <span className="shrink-0 text-[10px] text-slate-500">{patternLabel(kind)}</span>
                </span>
                {kind.era ? <span className="block text-[10px] text-slate-500">{kind.era}</span> : null}
              </button>
            ))}
            {traditions.isLoading && <Loader2 size={14} className="animate-spin text-slate-500" />}
          </div>

          {chosen && (
            <div className="space-y-2 rounded-xl border border-white/5 bg-black/10 p-3">
              <p className="text-[12px] leading-relaxed text-slate-300">{chosen.summary}</p>
              {chosen.rule ? <p className="text-[12px] text-slate-400"><span className="font-semibold text-slate-200">The rule:</span> {chosen.rule}</p> : null}
              {chosen.pattern.type === 'hours' && (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Length in hours">
                  {[...new Set([...data.presets, chosen.pattern.hours, 24])].sort((a, b) => a - b).map((h) => {
                    const on = (hours ?? (chosen.pattern.type === 'hours' ? chosen.pattern.hours : null)) === h;
                    return (
                      <button
                        key={h}
                        type="button"
                        aria-pressed={on}
                        onClick={() => setHours(h)}
                        className={`min-h-11 rounded-xl border px-3 text-xs font-semibold ${on ? 'border-amber-500/40 bg-amber-500/20 text-amber-200' : 'border-white/10 bg-white/[0.03] text-slate-300'}`}
                      >
                        {h} h
                      </button>
                    );
                  })}
                </div>
              )}
              {chosen.pattern.type === 'until' && (
                <label className="block text-[11px] text-slate-500">
                  Until
                  <input type="time" value={until || chosen.pattern.clock} onChange={(e) => setUntil(e.target.value)} className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm" />
                </label>
              )}
              {chosen.pattern.type === 'days' && (
                <label className="block text-[11px] text-slate-500">
                  Days
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={days ?? chosen.pattern.days}
                    onChange={(e) => setDays(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                    className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
                  />
                </label>
              )}
            </div>
          )}

          <label className="block text-[11px] text-slate-500">
            Praying for (optional, shown to those you share with)
            <input
              value={intention}
              onChange={(e) => setIntention(e.target.value.slice(0, 280))}
              placeholder="e.g. guidance for our family"
              className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
              data-testid="fasting-intention"
            />
          </label>
          <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={notifyShared} onChange={(e) => setNotifyShared(e.target.checked)} className="h-4 w-4 accent-amber-400" />
            Ask those I share fasting with to pray with me
          </label>
          <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={prayerReminders} onChange={(e) => setPrayerReminders(e.target.checked)} className="h-4 w-4 accent-amber-400" />
            Remind me at the hours of prayer (9, 12, 3)
          </label>
          <label className="block text-[11px] text-slate-500">
            Started at (leave empty for now)
            <input type="time" value={startClock} onChange={(e) => setStartClock(e.target.value)} className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm" />
            {startClock && (
              <span className="mt-1 block text-[11px] text-amber-200" data-testid="fasting-start-preview">
                Begins {localWhen(startedAtFromClock(startClock), new Date())}
              </span>
            )}
          </label>
          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={start.isPending}
            data-testid="fasting-start"
            className="glass-button flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
          >
            {start.isPending ? <Loader2 size={13} className="animate-spin" /> : <Timer size={13} />}
            Begin the fast
          </button>
          {traditions.data && <FastingGuide traditions={traditions.data} kind={chosen} active={false} />}
        </div>
      )}
      {data && data.recent.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-white/5 pt-2 text-[11px] text-slate-400" aria-label="Recent fasts">
          {data.recent.map((fast) => (
            <li key={fast.id} className="flex justify-between gap-2">
              <span>
                {fast.kind_name ? `${fast.kind_name} · ` : ''}
                {localWhen(fast.started_at, now)} → {fast.ended_at ? localWhen(fast.ended_at, now) : ''}
              </span>
              <span className="tabular-nums">{formatMinutes(fast.elapsed_minutes)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
