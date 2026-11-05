// How a screen-time lock is described to the member and their parents.
import type { ScreenTimeStatus } from '../types/api';

function untilText(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export function lockedLine(status: ScreenTimeStatus): string {
  const until = untilText(status.until);
  switch (status.reason) {
    case 'paused':
      return until ? `Paused by a parent until ${until}.` : 'Paused by a parent for now.';
    case 'bedtime':
      return `Bedtime. Back on at ${until}.`;
    case 'school':
      return `School time until ${until}.`;
    case 'chores': {
      const left = status.chores_left ?? 0;
      return left === 1
        ? 'Chores first: 1 chore left today. Tick it off on the chore chart to unlock.'
        : `Chores first: ${left} chores left today. Tick them off on the chore chart to unlock.`;
    }
    case 'limit':
      return "That's today's time. It resets at midnight.";
    default:
      return '';
  }
}

const PART_WORDS: Record<'steps' | 'exercise' | 'chores', (n: number) => string> = {
  steps: (n) => `${n.toLocaleString()} steps`,
  exercise: (n) => `${n} min exercise`,
  chores: (n) => `${n} approved ${n === 1 ? 'chore' : 'chores'}`,
};

/** "15 min from 3,000 steps and 1 approved chore", or '' when nothing earns. */
export function earnedLine(status: ScreenTimeStatus): string {
  const parts = status.earned?.parts || {};
  const from = (['steps', 'exercise', 'chores'] as const)
    .filter((k) => (parts[k]?.earned_min || 0) > 0)
    .map((k) => PART_WORDS[k](parts[k]!.count || 0));
  if (!status.earned_min) return '';
  const capped = status.earned && status.earned.max_min && status.earned.uncapped_min > status.earned.max_min
    ? ` (today's most is ${status.earned.max_min} min)` : '';
  return `Earned ${status.earned_min} min today${from.length ? ` from ${from.join(' and ')}` : ''}${capped}.`;
}

/** What to do to earn more, from the parents' rules: "every 1,000 steps earns 5 min". */
export function howToEarn(status: ScreenTimeStatus): string[] {
  const earn = status.rules.earn || {};
  const out: string[] = [];
  if (earn.steps_every && earn.steps_min) out.push(`every ${earn.steps_every.toLocaleString()} steps earns ${earn.steps_min} min`);
  if (earn.exercise_every && earn.exercise_min) out.push(`every ${earn.exercise_every} min of exercise earns ${earn.exercise_min} min`);
  if (earn.chore_min) out.push(`each chore a parent approves earns ${earn.chore_min} min`);
  return out;
}
