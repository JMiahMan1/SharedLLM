/**
 * The admin's per-person page (docs/PROFILE_STATS.md): the shape
 * `GET /api/profile/admin/{username}` answers with, and the small sums the
 * page draws from it. Admins only; the server refuses anyone else.
 */
import type { ProfileStats } from './profileStats';

export type Section<T> = { state: 'ok'; data: T } | { state: 'unavailable'; reason: string; data?: undefined };

export interface AdminDevice {
  label?: string; kind?: string; model?: string | null; manufacturer?: string | null; os_version?: string | null;
  app_version?: string | null; app_build?: string | null; last_seen_at?: string | null; archived?: boolean; revoked?: boolean;
}
export interface AdminUsage {
  devices: AdminDevice[];
  features: Array<{ feature: string; kind: string; uses: number; last_used_at?: string }>;
  capability_gaps: Array<{ capability: string; kind: string; request?: string; occurrences: number; last_seen_at?: string; resolved: boolean }>;
  conversations: { total: number; per_day: Array<{ day: string; count: number }>; features: Array<{ feature: string; count: number }> };
  events: Record<string, number>;
  app_opens_per_day: Array<{ day: string; count: number }>;
}
export interface CollectorProblem { kind?: 'permission' | 'worker' | 'collector' | string; what?: string; state?: string; outcome?: string; message?: string }

/** One collector problem in plain words. */
export function problemText(p: CollectorProblem): string {
  const what = (p.what || 'something').replace(/_/g, ' ');
  if (p.kind === 'permission') return `Permission off: ${what}`;
  if (p.kind === 'worker') return `Background job ${what} is ${String(p.state || 'stopped').toLowerCase()}`;
  return `${what}: ${p.message || p.outcome || 'last run failed'}`;
}
export interface AdminCollector {
  devices: Array<{ device_name?: string | null; app_version?: string | null; reported_at?: string | null; received_at?: string | null; problems: CollectorProblem[] }>;
  staleness: Record<string, { stale?: boolean; last_at?: string | null } | unknown>;
  problem_count: number;
}
export interface AdminScreen { days: Array<{ day: string; minutes: number; sessions: number }> }
export interface AdminGuardian { concerns: number; levels: Record<string, number>; recap_week?: string | null; recap_headline?: string | null }
export interface AdminLogs { by_level: Record<string, number>; recent: Array<{ level: string; service?: string; message: string; timestamp?: string | number }> }

/** Request outcomes (gateway voice_outcomes): filled once that recorder is in. */
export interface AdminVoice {
  days: number;
  total: number;
  outcomes: Record<string, number>;
  latency: { buckets: Record<string, number>; p50_ms?: number | null; p90_ms?: number | null };
  per_day: Array<{ day: string; total: number; ok: number; failed: number }>;
  tools: Array<{ tool: string; failures: number; calls?: number }>;
  slow_hours: Array<{ hour: number; slow: number; total: number }>;
}

export interface AdminFlag { area: string; level: 'watch' | 'fix'; text: string }

export interface AdminPersonStats {
  username: string;
  person: { username: string; display_name: string };
  profile: ProfileStats;
  flags: AdminFlag[];
  sections: {
    usage: Section<AdminUsage>;
    collector: Section<AdminCollector>;
    features: Section<{ since: string; counts: Record<string, number> }>;
    screen: Section<AdminScreen>;
    guardian: Section<AdminGuardian>;
    logs: Section<AdminLogs>;
    voice: Section<AdminVoice | null>;
  };
}

/** The admin page's path for a person (or the picker). */
export const adminPeoplePath = (username?: string) => (username ? `/admin/people/${encodeURIComponent(username)}` : '/admin/people');

/** The person a path names: `/admin/people/robin` gives "robin". */
export function personFromPath(pathname: string): string | null {
  const m = /^\/admin\/people\/([^/?#]+)/.exec(pathname);
  return m ? decodeURIComponent(m[1]).toLowerCase() : null;
}

/** "2 days ago", "3 h ago", or the date for anything older than a week. */
export function ago(iso?: string | null, now = Date.now()): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'unknown';
  const mins = Math.round((now - t) / 60000);
  if (mins < 2) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 8) return `${days} day${days === 1 ? '' : 's'} ago`;
  return new Date(t).toLocaleDateString();
}

/** The newest app version among a person's devices, to spot one running behind. */
export function newestVersion(devices: AdminDevice[]): string | null {
  const versions = devices.map((d) => d.app_version).filter((v): v is string => Boolean(v));
  if (!versions.length) return null;
  return versions.sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))[0];
}

/** Share as a whole percent, or a dash when there is nothing to divide. */
export function percent(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '-';
}
