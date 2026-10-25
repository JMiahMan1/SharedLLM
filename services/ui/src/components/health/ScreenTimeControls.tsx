import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { duration } from '../../lib/phoneUsageFormat';
import { WEEKDAYS, WEEKDAY_LABELS, limitFor, schoolDays, statusQuery } from '../../lib/memberPage';
import MemberCard from '../member/MemberCard';
import type { ScreenRules, Weekday } from '../../types/api';

/**
 * A parent's screen-time schedule for one child (Phase 11 step 4, Family
 * Link's weekly view): a row per day with that day's limit and whether it is
 * a school day, the everyday and weekend limits the rows fall back to, and
 * the bedtime and school routines. Pause, bonus time and requests sit at the
 * top of the family member page; app and web rules in Apps & web.
 */

type Draft = {
  daily: string;
  weekend: string;
  days: Partial<Record<Weekday, string>>;
  school: Weekday[];
  bedtimeStart: string;
  bedtimeEnd: string;
  schoolStart: string;
  schoolEnd: string;
};

function draftFrom(rules: ScreenRules): Draft {
  return {
    daily: rules.daily_limit_min !== undefined ? String(rules.daily_limit_min) : '',
    weekend: rules.weekend_limit_min !== undefined ? String(rules.weekend_limit_min) : '',
    days: Object.fromEntries(Object.entries(rules.day_limits || {}).map(([d, m]) => [d, String(m)])),
    school: schoolDays(rules),
    bedtimeStart: rules.bedtime_start || '',
    bedtimeEnd: rules.bedtime_end || '',
    schoolStart: rules.school_start || '',
    schoolEnd: rules.school_end || '',
  };
}

const minutes = (value: string | undefined) =>
  value === undefined || value.trim() === '' ? undefined : Math.max(0, Math.min(1440, Math.round(Number(value))));

function rulesFrom(draft: Draft, current: ScreenRules): ScreenRules {
  // The full rules go back each time: the server replaces them as a whole.
  const rest: ScreenRules = { ...current };
  delete rest.paused;
  delete rest.paused_until;
  const days = Object.fromEntries(
    WEEKDAYS.map((d) => [d, minutes(draft.days[d])]).filter(([, m]) => m !== undefined),
  ) as Partial<Record<Weekday, number>>;
  return {
    ...rest,
    daily_limit_min: minutes(draft.daily),
    weekend_limit_min: minutes(draft.weekend),
    day_limits: Object.keys(days).length ? days : undefined,
    school_days: draft.school,
    bedtime_start: draft.bedtimeStart && draft.bedtimeEnd ? draft.bedtimeStart : undefined,
    bedtime_end: draft.bedtimeStart && draft.bedtimeEnd ? draft.bedtimeEnd : undefined,
    school_start: draft.schoolStart && draft.schoolEnd ? draft.schoolStart : undefined,
    school_end: draft.schoolStart && draft.schoolEnd ? draft.schoolEnd : undefined,
  };
}

const input = 'mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100';

export default function ScreenTimeControls({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
  // The saved rules until the parent edits a field.
  const [edited, setDraft] = useState<Draft | null>(null);
  const draft = edited ?? (status ? draftFrom(status.rules) : null);
  const save = useMutation({
    mutationFn: (rules: ScreenRules) => api.setScreenRules(username, rules),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    },
  });
  const failure = isAxiosError(save.error) ? (save.error.response?.data as { detail?: string })?.detail || 'That did not save.' : save.error ? 'That did not save.' : null;

  // What each row would be with the draft applied, so a blank day shows what it falls back to.
  const preview = draft && status ? rulesFrom(draft, status.rules) : undefined;
  const field = (key: 'daily' | 'weekend' | 'bedtimeStart' | 'bedtimeEnd' | 'schoolStart' | 'schoolEnd', label: string, type: 'number' | 'time') =>
    draft && (
      <label className="block text-[11px] text-slate-400">
        {label}
        <input
          type={type}
          min={type === 'number' ? 0 : undefined}
          max={type === 'number' ? 1440 : undefined}
          inputMode={type === 'number' ? 'numeric' : undefined}
          value={draft[key]}
          onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          className={input}
        />
      </label>
    );

  return (
    <MemberCard
      title={`${name}'s week`}
      icon={CalendarClock}
      tone="text-amber-300"
      testId="screen-time-controls"
      wide
      isLoading={isLoading}
      error={isError ? 'The schedule could not be loaded.' : null}
      empty={status && !status.can_manage ? 'Only their parents can change this.' : null}
      aside={status?.bonus_min ? `+${status.bonus_min} min bonus today` : undefined}
    >
      {draft && status && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            {field('daily', 'Every day (min)', 'number')}
            {field('weekend', 'Weekends (min)', 'number')}
          </div>
          <table className="w-full text-xs" data-testid="weekly-schedule">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500">
                <th className="py-1 font-normal">Day</th>
                <th className="py-1 font-normal">Limit (min)</th>
                <th className="py-1 font-normal">School</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {WEEKDAYS.map((d) => {
                const effective = limitFor(preview, d);
                const school = draft.school.includes(d);
                return (
                  <tr key={d} data-testid={`schedule-${d}`}>
                    <th scope="row" className="py-1.5 pr-2 text-left font-normal text-slate-200">{WEEKDAY_LABELS[d]}</th>
                    <td className="py-1.5 pr-2">
                      <input
                        type="number"
                        min={0}
                        max={1440}
                        inputMode="numeric"
                        aria-label={`${WEEKDAY_LABELS[d]} limit in minutes`}
                        placeholder={effective === null ? 'no limit' : `${effective} (${duration(effective * 60000)})`}
                        value={draft.days[d] ?? ''}
                        onChange={(e) => setDraft({ ...draft, days: { ...draft.days, [d]: e.target.value } })}
                        className="min-h-11 w-full rounded-lg border border-white/10 bg-white/5 px-2 text-sm text-slate-100"
                      />
                    </td>
                    <td className="py-1.5">
                      <button
                        type="button"
                        aria-pressed={school}
                        aria-label={`${WEEKDAY_LABELS[d]} is a school day`}
                        onClick={() => setDraft({ ...draft, school: school ? draft.school.filter((x) => x !== d) : [...draft.school, d] })}
                        className={`min-h-11 min-w-14 rounded-lg border px-2 text-[11px] ${school ? 'border-sky-400/40 bg-sky-400/10 text-white' : 'border-white/10 bg-white/5 text-slate-400'}`}
                      >
                        {school ? 'Yes' : 'No'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="grid grid-cols-2 gap-2">
            {field('bedtimeStart', 'Bedtime starts', 'time')}
            {field('bedtimeEnd', 'Bedtime ends', 'time')}
            {field('schoolStart', 'School starts', 'time')}
            {field('schoolEnd', 'School ends', 'time')}
          </div>
          <button
            type="button"
            disabled={save.isPending || !edited}
            onClick={() => save.mutate(rulesFrom(draft, status.rules))}
            className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
            data-testid="screen-time-save"
          >
            Save schedule
          </button>
          {failure && <p className="text-xs text-amber-300">{failure}</p>}
        </div>
      )}
    </MemberCard>
  );
}
