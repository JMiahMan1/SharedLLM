import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, Check, Loader2, MessageCircle, Target, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatClockTime, localDay } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import { HEALTH_REMINDERS, existingReminders, reminderPayload, type HealthReminderKind } from '../../lib/healthReminders';
import type { HealthChallengeKind } from '../../types/api';

/**
 * The coach (Phase 6): gentle weekly goals, daily reminders, and a pointer to
 * asking Jarvis in chat, which answers from the person's own data.
 *
 * Two goals a week at most; the suggestion is one small step up from last
 * week. Reminders are ordinary daily reminders in the timers pipeline.
 */
export default function CoachCard() {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  const [kind, setKind] = useState<HealthChallengeKind | ''>('');
  const [target, setTarget] = useState(3);
  const [clocks, setClocks] = useState<Partial<Record<HealthReminderKind, string>>>({});

  const goalsQuery = useQuery({ queryKey: ['health-goals', day], queryFn: () => api.getHealthGoals(day) });
  const timersQuery = useQuery({ queryKey: ['timers'], queryFn: () => api.getTimers(), retry: false });
  const reminders = existingReminders(Array.isArray(timersQuery.data) ? timersQuery.data : undefined);

  const refreshGoals = () => void queryClient.invalidateQueries({ queryKey: ['health-goals'] });
  const addGoal = useMutation({
    mutationFn: (body: { kind: HealthChallengeKind; target_days: number }) => api.createHealthGoal({ ...body, day }),
    onSuccess: (week) => {
      queryClient.setQueryData(['health-goals', day], week);
      setKind('');
    },
    onError: (error) => toast.error(errorText(error, 'Could not set that goal')),
  });
  const removeGoal = useMutation({
    mutationFn: (id: number) => api.deleteHealthGoal(id),
    onSuccess: refreshGoals,
    onError: (error) => toast.error(errorText(error, 'Could not remove that goal')),
  });
  const setReminder = useMutation({
    mutationFn: async ({ which, clock }: { which: HealthReminderKind; clock: string }) => {
      const def = HEALTH_REMINDERS.find((entry) => entry.kind === which);
      if (!def) throw new Error('Unknown reminder');
      const existing = reminders[which];
      if (existing) await api.deleteTimer(existing.title, 'reminder', existing.id);
      const result = await api.createTimer(reminderPayload(def, clock));
      if (result.status && result.status !== 'SUCCESS') throw new Error(result.message || 'The reminder was not set');
      return def;
    },
    onSuccess: (def) => {
      toast.success(`${def.label} reminder set for every day`);
      void queryClient.invalidateQueries({ queryKey: ['timers'] });
    },
    onError: (error) => toast.error(errorText(error, 'Could not set the reminder')),
  });
  const clearReminder = useMutation({
    mutationFn: async (which: HealthReminderKind) => {
      const existing = reminders[which];
      if (existing) await api.deleteTimer(existing.title, 'reminder', existing.id);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['timers'] }),
    onError: (error) => toast.error(errorText(error, 'Could not remove the reminder')),
  });

  const week = goalsQuery.data;
  const open = week?.kinds.filter((option) => !option.taken) ?? [];
  const chosenKind = kind || open[0]?.kind || '';
  const room = week ? week.goals.length < week.max_goals : false;

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="coach-heading" data-testid="coach-card">
      <h3 id="coach-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <Target size={13} className="text-emerald-300" />
        Coach
      </h3>

      <div className="mt-2">
        <p className="text-xs font-semibold text-slate-200">This week&apos;s goals</p>
        {goalsQuery.isLoading ? (
          <div className="mt-2 h-14 skeleton rounded-xl" aria-hidden />
        ) : goalsQuery.isError || !week ? (
          <div className="mt-1 flex items-center gap-3">
            <p className="text-xs text-amber-300">Goals could not be loaded.</p>
            <button type="button" onClick={() => void goalsQuery.refetch()} className="glass-button min-h-11 px-3 text-xs">
              Try again
            </button>
          </div>
        ) : (
          <div className="mt-1 space-y-2">
            {week.goals.length === 0 && <p className="text-xs text-slate-500">No goal this week yet. One small one is plenty.</p>}
            {week.goals.map((goal) => (
              <div key={goal.id} className="flex items-center gap-2 text-xs" data-testid={`goal-${goal.kind}`}>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-1 text-slate-200">
                    {goal.title}: {goal.count} of {goal.target_days} {goal.unit}
                    {goal.reached && <Check size={12} className="text-emerald-300" aria-label="reached" />}
                  </p>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                    <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, (goal.count / goal.target_days) * 100)}%` }} />
                  </div>
                  {goal.guideline_goal && <p className="mt-0.5 text-[10px] text-slate-500">Measured against the guideline goal until you set your own.</p>}
                </div>
                <button
                  type="button"
                  onClick={() => removeGoal.mutate(goal.id)}
                  aria-label={`Remove ${goal.title} goal`}
                  className="flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:text-red-300"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            {week.suggestion && (
              <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] p-3 text-xs" data-testid="goal-suggestion">
                <p className="text-emerald-100">
                  Try {week.suggestion.title.toLowerCase()}: {week.suggestion.target_days} {week.suggestion.unit} this week.
                </p>
                <p className="mt-0.5 text-[11px] text-slate-400">{week.suggestion.reason}</p>
                <button
                  type="button"
                  onClick={() => week.suggestion && addGoal.mutate({ kind: week.suggestion.kind, target_days: week.suggestion.target_days })}
                  disabled={addGoal.isPending}
                  className="glass-button mt-2 min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
                >
                  Set this goal
                </button>
              </div>
            )}
            {room && open.length > 0 && (
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-[11px] text-slate-500">
                  Or choose
                  <select
                    value={chosenKind}
                    onChange={(event) => setKind(event.target.value as HealthChallengeKind)}
                    className="glass-input mt-1 min-h-11 px-2 text-base sm:text-sm"
                  >
                    {open.map((option) => (
                      <option key={option.kind} value={option.kind}>
                        {option.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-[11px] text-slate-500">
                  Days
                  <select value={target} onChange={(event) => setTarget(Number(event.target.value))} className="glass-input mt-1 min-h-11 px-2 text-base sm:text-sm">
                    {[1, 2, 3, 4, 5, 6, 7].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => chosenKind && addGoal.mutate({ kind: chosenKind, target_days: target })}
                  disabled={!chosenKind || addGoal.isPending}
                  className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
                >
                  Add goal
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
          <Bell size={12} className="text-amber-300" /> Daily reminders
        </p>
        {timersQuery.isError && <p className="mt-1 text-[11px] text-amber-300">Your reminders could not be loaded; setting one still works.</p>}
        <ul className="mt-1 space-y-2">
          {HEALTH_REMINDERS.map((def) => {
            const existing = reminders[def.kind];
            const clock = clocks[def.kind] ?? '';
            return (
              <li key={def.kind} className="flex flex-wrap items-end gap-2 text-xs" data-testid={`reminder-${def.kind}`}>
                <div className="min-w-0 flex-1">
                  <p className="text-slate-300">
                    {def.emoji} {def.label}
                  </p>
                  <p className="text-[10px] text-slate-500">
                    {existing ? `Every day at ${formatClockTime(existing.expires_at)}` : def.hint}
                  </p>
                </div>
                <input
                  type="time"
                  value={clock}
                  onChange={(event) => setClocks((current) => ({ ...current, [def.kind]: event.target.value }))}
                  aria-label={`${def.label} reminder time`}
                  className="glass-input min-h-11 px-2 text-base sm:text-sm"
                />
                <button
                  type="button"
                  onClick={() => setReminder.mutate({ which: def.kind, clock })}
                  disabled={!clock || setReminder.isPending}
                  className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
                >
                  {setReminder.isPending ? <Loader2 size={12} className="animate-spin" /> : existing ? 'Change' : 'Remind me'}
                </button>
                {existing && (
                  <button
                    type="button"
                    onClick={() => clearReminder.mutate(def.kind)}
                    aria-label={`Stop the ${def.label} reminder`}
                    className="flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:text-red-300"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <p className="mt-4 flex items-start gap-1.5 border-t border-white/5 pt-3 text-[11px] text-slate-400">
        <MessageCircle size={12} className="mt-0.5 shrink-0 text-purple-300" />
        Ask Jarvis in chat: “How did I sleep?”, “How much protein is left?”, “How was my week?” or “How are my
        goals?” It answers from your own logs only.
      </p>
    </section>
  );
}
