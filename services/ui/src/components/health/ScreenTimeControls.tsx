import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Hourglass } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { duration } from '../../lib/phoneUsageFormat';
import type { ScreenRules } from '../../types/api';
import { lockedLine } from '../../lib/screenTimeText';

/**
 * A parent's screen-time controls for one child (Phase 11 step 4): daily and
 * weekend limits, bedtime and school hours, pause, bonus time, and the
 * child's requests for more. The server refuses anyone else; this only renders
 * once the status says the viewer may manage the child.
 */

type Draft = {
  daily: string;
  weekend: string;
  bedtimeStart: string;
  bedtimeEnd: string;
  schoolStart: string;
  schoolEnd: string;
};

function draftFrom(rules: ScreenRules): Draft {
  return {
    daily: rules.daily_limit_min !== undefined ? String(rules.daily_limit_min) : '',
    weekend: rules.weekend_limit_min !== undefined ? String(rules.weekend_limit_min) : '',
    bedtimeStart: rules.bedtime_start || '',
    bedtimeEnd: rules.bedtime_end || '',
    schoolStart: rules.school_start || '',
    schoolEnd: rules.school_end || '',
  };
}

function rulesFrom(draft: Draft, current: ScreenRules): ScreenRules {
  const minutes = (value: string) => (value.trim() === '' ? undefined : Math.max(0, Math.min(1440, Math.round(Number(value)))));
  return {
    daily_limit_min: minutes(draft.daily),
    weekend_limit_min: minutes(draft.weekend),
    bedtime_start: draft.bedtimeStart && draft.bedtimeEnd ? draft.bedtimeStart : undefined,
    bedtime_end: draft.bedtimeStart && draft.bedtimeEnd ? draft.bedtimeEnd : undefined,
    school_start: draft.schoolStart && draft.schoolEnd ? draft.schoolStart : undefined,
    school_end: draft.schoolStart && draft.schoolEnd ? draft.schoolEnd : undefined,
    school_days: current.school_days,
    always_allowed: current.always_allowed,
  };
}

export default function ScreenTimeControls({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ['screen-time', username],
    queryFn: () => api.getScreenTimeStatus(username),
    refetchInterval: 60 * 1000,
    retry: false,
  });
  const { data: requests } = useQuery({
    queryKey: ['screen-time-requests', 'pending'],
    queryFn: () => api.getScreenTimeRequests('pending'),
    retry: false,
  });
  // The saved rules until the parent edits a field.
  const [edited, setDraft] = useState<Draft | null>(null);
  const draft = edited ?? (status ? draftFrom(status.rules) : null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    void queryClient.invalidateQueries({ queryKey: ['screen-time-requests'] });
  };
  const save = useMutation({
    mutationFn: (rules: ScreenRules) => api.setScreenRules(username, rules),
    onSuccess: () => {
      setDraft(null);
      refresh();
    },
  });
  const pause = useMutation({ mutationFn: (resume: boolean) => api.pauseScreenTime(username, resume ? { resume: true } : {}), onSuccess: refresh });
  const bonus = useMutation({ mutationFn: (minutes: number) => api.giveScreenTimeBonus(username, minutes), onSuccess: refresh });
  const decide = useMutation({
    mutationFn: ({ id, grant }: { id: number; grant: boolean }) => api.decideScreenTimeRequest(id, grant),
    onSuccess: refresh,
  });

  if (!status?.can_manage || !draft) return null;
  const theirs = (requests?.requests || []).filter((r) => r.username === username);
  const failure = [save.error, pause.error, bonus.error, decide.error].find(Boolean);
  const failureText = isAxiosError(failure) ? (failure.response?.data as { detail?: string })?.detail || 'That did not save.' : null;
  const field = (key: keyof Draft, label: string, type: 'number' | 'time') => (
    <label className="block text-[11px] text-slate-400">
      {label}
      <input
        type={type}
        min={type === 'number' ? 0 : undefined}
        max={type === 'number' ? 1440 : undefined}
        inputMode={type === 'number' ? 'numeric' : undefined}
        value={draft[key]}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
      />
    </label>
  );

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="screen-time-controls">
      <div className="flex items-center gap-2">
        <Hourglass size={16} className="text-amber-300" />
        <h3 className="text-sm font-semibold text-white">Screen time for {name}</h3>
      </div>

      <p className="text-xs text-slate-300" data-testid="screen-time-controls-state">
        {status.locked
          ? lockedLine(status)
          : status.remaining_min !== null
            ? `${duration(status.remaining_min * 60000)} left today (${status.used_min} min used${status.bonus_min ? `, ${status.bonus_min} bonus` : ''}).`
            : 'No daily limit set.'}
      </p>

      {theirs.map((r) => (
        <div key={r.id} className="rounded-xl border border-sky-400/30 bg-sky-400/5 p-3 space-y-2" data-testid="screen-time-request">
          <p className="text-xs text-slate-200">
            Asks for {r.minutes} more minutes{r.note ? `: “${r.note}”` : ''}
          </p>
          <div className="flex gap-2">
            <button type="button" className="glass-button px-3 py-1.5 text-xs min-h-11" onClick={() => decide.mutate({ id: r.id, grant: true })}>
              Grant
            </button>
            <button type="button" className="px-3 py-1.5 text-xs min-h-11 text-slate-400" onClick={() => decide.mutate({ id: r.id, grant: false })}>
              Not today
            </button>
          </div>
        </div>
      ))}

      <div className="flex flex-wrap gap-2">
        <button type="button" className="glass-button px-3 py-1.5 text-xs min-h-11" onClick={() => pause.mutate(status.paused)}>
          {status.paused ? 'Unpause phone' : 'Pause phone'}
        </button>
        {[15, 30].map((m) => (
          <button key={m} type="button" className="glass-button px-3 py-1.5 text-xs min-h-11" onClick={() => bonus.mutate(m)}>
            +{m} min today
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {field('daily', 'Daily limit (min)', 'number')}
        {field('weekend', 'Weekend limit (min)', 'number')}
        {field('bedtimeStart', 'Bedtime starts', 'time')}
        {field('bedtimeEnd', 'Bedtime ends', 'time')}
        {field('schoolStart', 'School starts', 'time')}
        {field('schoolEnd', 'School ends', 'time')}
      </div>
      <button
        type="button"
        disabled={save.isPending}
        onClick={() => save.mutate(rulesFrom(draft, status.rules))}
        className="glass-button px-3 py-1.5 text-xs font-semibold min-h-11"
        data-testid="screen-time-save"
      >
        Save rules
      </button>
      {failureText && <p className="text-xs text-amber-300">{failureText}</p>}
    </section>
  );
}
