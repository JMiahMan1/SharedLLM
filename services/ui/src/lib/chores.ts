/**
 * Managing Skylight chores in the app (docs/ACHIEVEMENTS.md, "Managing chores").
 * The gateway decides who may; these mirror its rule so the controls only show
 * for the people they will work for.
 */
import type { QueryClient } from '@tanstack/react-query';

/** Family roles that run the chore chart, besides admins (gateway CHORE_MANAGER_ROLES). */
const MANAGER_ROLES = new Set(['father', 'mother']);

export function canManageChores(user: { is_admin?: boolean; family_role?: string | null } | null | undefined): boolean {
  return Boolean(user && (user.is_admin || MANAGER_ROLES.has(String(user.family_role ?? ''))));
}

/** How often a chore repeats: the server's plain words (services/execution/skylight_chores.py). */
export const CHORE_REPEATS: ReadonlyArray<readonly [string, string]> = [
  ['once', 'Just once'],
  ['daily', 'Every day'],
  ['weekdays', 'Weekdays'],
  ['weekends', 'Weekends'],
  ['weekly', 'Every week'],
];

/** "07:00" as "7 am", "16:30" as "4:30 pm". */
export function clockLabel(time?: string | null): string {
  if (!time) return '';
  const [h, m] = time.split(':').map(Number);
  if (Number.isNaN(h)) return time;
  return `${h % 12 || 12}${m ? `:${String(m).padStart(2, '0')}` : ''} ${h < 12 ? 'am' : 'pm'}`;
}

/** Every chore list in the app (the dashboard widget, the Guardian and member cards) shares this prefix. */
export const CHORES_QUERY_PREFIX = ['skylight-chores'] as const;

export function refreshChores(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: CHORES_QUERY_PREFIX });
}
