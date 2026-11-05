import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Footprints } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import MemberCard from '../member/MemberCard';
import type { EarnRules, ScreenRules } from '../../types/api';

/**
 * Earned time, set by a parent (docs/HEALTH_HUB.md, "Earning time and chores first"): how
 * many minutes steps, exercise and chores are worth, the most a day can earn,
 * and "chores first", which locks the phone until today's chores are ticked
 * off. A chore earns only once a parent says it was done right; earned
 * minutes left over carry to the next day until the week starts again on Monday.
 */

type Key = keyof EarnRules;
type Draft = Record<Key, string> & { choresFirst: boolean };
const KEYS: Key[] = ['steps_every', 'steps_min', 'exercise_every', 'exercise_min', 'chore_min', 'max_min'];

function draftFrom(rules: ScreenRules): Draft {
  const earn = rules.earn || {};
  return {
    ...(Object.fromEntries(KEYS.map((k) => [k, earn[k] !== undefined ? String(earn[k]) : ''])) as Record<Key, string>),
    choresFirst: Boolean(rules.chores_first),
  };
}

function rulesFrom(draft: Draft, current: ScreenRules): ScreenRules {
  // The full rules go back each time: the server replaces them as a whole.
  const rest: ScreenRules = { ...current };
  delete rest.paused;
  delete rest.paused_until;
  const earn: EarnRules = {};
  for (const k of KEYS) {
    const value = draft[k].trim();
    if (value !== '') earn[k] = Math.max(0, Math.round(Number(value)));
  }
  return { ...rest, earn: Object.keys(earn).length ? earn : undefined, chores_first: draft.choresFirst || undefined };
}

/** A pair left half filled in ("every 1000 steps earns ... min"). */
function halfDone(draft: Draft): string | null {
  if ((draft.steps_every.trim() === '') !== (draft.steps_min.trim() === '')) return 'Fill in both numbers for steps, or neither.';
  if ((draft.exercise_every.trim() === '') !== (draft.exercise_min.trim() === '')) return 'Fill in both numbers for exercise, or neither.';
  return null;
}

const box = 'min-h-11 w-16 shrink-0 rounded-lg border border-white/10 bg-white/5 px-2 text-sm text-slate-100';

export default function EarnTimeControls({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
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
  const problem = draft ? halfDone(draft) : null;

  const num = (key: Key, label: string, max: number, min = 1) =>
    draft && (
      <input
        type="number"
        min={min}
        max={max}
        inputMode="numeric"
        aria-label={label}
        value={draft[key]}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        className={box}
        data-testid={`earn-${key}`}
      />
    );

  const row = (label: string, body: ReactNode) => (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <div className="mt-1 flex flex-wrap items-center gap-2">{body}</div>
    </div>
  );

  const summary = status && (status.earned_min || status.banked_min)
    ? `${status.earned_min || 0} min earned today${status.banked_min ? `, ${status.banked_min} saved` : ''}`
    : undefined;

  return (
    <MemberCard
      title={`${name} earns time`}
      icon={Footprints}
      tone="text-emerald-300"
      testId="earn-time-controls"
      isLoading={isLoading}
      error={isError ? 'Earned time could not be loaded.' : null}
      empty={status && !status.can_manage ? 'Only their parents can change this.' : null}
      aside={summary}
    >
      {draft && status && (
        <div className="space-y-3 text-sm text-slate-200">
          <p className="text-xs text-slate-400">
            Minutes earned add to today's limit. A chore counts once you say it was done right. Unused minutes carry over
            until Monday. Leave a line blank to switch it off.
          </p>
          {row('Steps', <>{num('steps_every', 'Steps for each block', 50000, 100)}<span>steps earns</span>{num('steps_min', 'Minutes each block of steps earns', 240)}<span>min</span></>)}
          {row('Exercise', <>{num('exercise_every', 'Minutes of exercise for each block', 240)}<span>min exercise earns</span>{num('exercise_min', 'Minutes each block of exercise earns', 240)}<span>min</span></>)}
          {row('Chores', <><span>Each approved one earns</span>{num('chore_min', 'Minutes each approved chore earns', 240)}<span>min</span></>)}
          {row('Most a day', <><span>Earn up to</span>{num('max_min', 'Most minutes a day can earn', 720)}<span>min a day</span></>)}
          <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/5 p-3">
            <input
              type="checkbox"
              checked={draft.choresFirst}
              onChange={(e) => setDraft({ ...draft, choresFirst: e.target.checked })}
              className="mt-0.5 h-5 w-5 shrink-0 accent-emerald-400"
              data-testid="earn-chores-first"
            />
            <span>
              <span className="block font-medium">Chores first</span>
              <span className="block text-xs text-slate-400">
                Their {status.device_kinds?.includes('tablet') && !status.device_kinds?.includes('phone') ? 'tablet' : 'phone'} stays
                locked until today's chores are ticked off. Calls, texts and Jarvis still work, and time you give still opens it.
              </span>
            </span>
          </label>
          <button
            type="button"
            disabled={save.isPending || !edited || Boolean(problem)}
            onClick={() => save.mutate(rulesFrom(draft, status.rules))}
            className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
            data-testid="earn-save"
          >
            Save earning
          </button>
          {(problem || failure) && <p className="text-xs text-amber-300" role="alert">{problem || failure}</p>}
        </div>
      )}
    </MemberCard>
  );
}
