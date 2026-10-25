/**
 * The family member page (docs/FAMILY_MEMBER_PAGE.md): its sections, its
 * shared queries, and the pure arithmetic behind its charts. Every number
 * here comes from what the server returned; a day, an hour or a night with no
 * report stays empty and is never filled in.
 */
import { api } from '../services/api';
import { localDay, shiftDay } from './healthHub';
import type { ZonePlace } from './wanderQueries';
import { distanceMeters } from '../components/geo/liveLocations';
import type { ActivityEvent, ScreenRules, ScreenTimeStatus, UsageDay, Weekday } from '../types/api';

export type SectionId = 'overview' | 'time' | 'apps' | 'activity' | 'location' | 'health' | 'faith' | 'devices';

export interface MemberSection {
  id: SectionId;
  label: string;
  /** Technical data: shown only when the server says the viewer may see it. */
  technical?: boolean;
}

export const MEMBER_SECTIONS: MemberSection[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'time', label: 'Time' },
  { id: 'apps', label: 'Apps & web' },
  { id: 'activity', label: 'Activity' },
  { id: 'location', label: 'Location' },
  { id: 'health', label: 'Health' },
  { id: 'faith', label: 'Faith & growth' },
  { id: 'devices', label: 'Devices', technical: true },
];

/** The member page's address (the old `/guardian?child=` opens it). */
export const memberPath = (username: string, section?: SectionId) =>
  `/family/member/${encodeURIComponent(username)}${section && section !== 'overview' ? `?section=${section}` : ''}`;

/** Days of usage the page reads: the 14-day heatmap is the longest view. */
export const USAGE_DAYS = 14;

/** One cache for a member's usage, shared by the strip, the charts and app controls. */
export const usageQuery = (username: string) => ({
  queryKey: ['usage', username] as const,
  queryFn: () => api.getUsage(username, USAGE_DAYS),
  retry: false,
  staleTime: 60_000,
});

/** One cache for a member's screen-time status (the same key the controls invalidate). */
export const statusQuery = (username: string) => ({
  queryKey: ['screen-time', username] as const,
  queryFn: () => api.getScreenTimeStatus(username),
  refetchInterval: 60_000,
  retry: false,
});

/** The most events the server keeps per member (the activity feed is capped there). */
export const ACTIVITY_LIMIT = 200;

export const activityQuery = (username: string) => ({
  queryKey: ['guardian-activity', username] as const,
  queryFn: () => api.getGuardianActivity(username, ACTIVITY_LIMIT),
  retry: false,
});

/** A child's requests (more time; apps, permissions and sites), open or answered: one cache each. */
export const timeRequestsQuery = (history: boolean) => ({
  queryKey: ['screen-time-requests', history ? 'history' : 'pending'] as const,
  queryFn: async () =>
    history
      ? [...(await api.getScreenTimeRequests('granted')).requests, ...(await api.getScreenTimeRequests('declined')).requests]
      : (await api.getScreenTimeRequests('pending')).requests,
  refetchInterval: history ? (false as const) : 60_000,
  retry: false,
});

export const guardRequestsQuery = (history: boolean) => ({
  queryKey: ['guard-requests', history ? 'history' : 'pending'] as const,
  queryFn: async () =>
    history
      ? [...(await api.getGuardRequests('approved')).requests, ...(await api.getGuardRequests('denied')).requests]
      : (await api.getGuardRequests('pending')).requests,
  refetchInterval: history ? (false as const) : 60_000,
  retry: false,
});

export const WEEKDAYS: Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
export const WEEKDAY_LABELS: Record<Weekday, string> = {
  mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday',
};
const SCHOOL_DAYS_DEFAULT: Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri'];

export function weekdayOf(day: string): Weekday {
  const index = new Date(`${day}T12:00:00`).getDay(); // 0 = Sunday
  return WEEKDAYS[(index + 6) % 7];
}

/** A day's limit in minutes, the way the server decides it (common/screen_time.py). */
export function limitFor(rules: ScreenRules | undefined, weekday: Weekday): number | null {
  if (!rules) return null;
  const own = rules.day_limits?.[weekday];
  if (own !== undefined && own !== null) return own;
  if ((weekday === 'sat' || weekday === 'sun') && rules.weekend_limit_min !== undefined) return rules.weekend_limit_min;
  return rules.daily_limit_min ?? null;
}

export function schoolDays(rules: ScreenRules | undefined): Weekday[] {
  const days = (rules?.school_days || []).filter((d): d is Weekday => (WEEKDAYS as string[]).includes(d));
  return days.length ? days : SCHOOL_DAYS_DEFAULT;
}

export interface DayBar {
  day: string;
  label: string;
  /** Minutes of screen time, or null when the phone reported nothing that day. */
  minutes: number | null;
  limit: number | null;
  over: boolean;
}

/** The last `count` days ending today, oldest first, from what the phone reported. */
export function dayBars(days: UsageDay[], rules: ScreenRules | undefined, count = 7, today = localDay()): DayBar[] {
  const byDay = new Map(days.map((d) => [d.day, d]));
  return Array.from({ length: count }, (_, i) => {
    const day = shiftDay(today, i - count + 1);
    const reported = byDay.get(day);
    const minutes = reported ? Math.round((reported.millis || 0) / 60000) : null;
    const limit = limitFor(rules, weekdayOf(day));
    return {
      day,
      label: new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short' }),
      minutes,
      limit,
      over: minutes !== null && limit !== null && minutes > limit,
    };
  });
}

/** The average of the days that were reported (never counting a silent day as zero). */
export function averageMinutes(bars: DayBar[]): number | null {
  const reported = bars.filter((b) => b.minutes !== null);
  if (!reported.length) return null;
  return Math.round(reported.reduce((n, b) => n + (b.minutes || 0), 0) / reported.length);
}

export type MemberState = 'paused' | 'bedtime' | 'locked' | 'in_use' | 'idle' | 'offline';

export const STATE_LABELS: Record<MemberState, string> = {
  paused: 'Paused',
  bedtime: 'Bedtime',
  locked: 'Locked',
  in_use: 'On the phone',
  idle: 'Not on the phone',
  offline: 'No report today',
};

/** On the phone: used within the last few minutes (the phone reports every five). */
export const IN_USE_WINDOW_MS = 10 * 60 * 1000;

export function memberState(status: ScreenTimeStatus | undefined, today: UsageDay | undefined, now = Date.now()): MemberState {
  if (status?.paused) return 'paused';
  if (status?.bedtime_active) return 'bedtime';
  if (status?.locked) return 'locked';
  if (!today) return 'offline';
  return today.last_use && now - today.last_use <= IN_USE_WINDOW_MS ? 'in_use' : 'idle';
}

/** "Bedtime in 2 h 10 min", from the server's next lock. */
export function nextRoutineLine(status: ScreenTimeStatus | undefined, now = Date.now()): string | null {
  if (!status?.next_lock_at || !status.next_lock_reason) return null;
  const minutes = Math.max(0, Math.round((new Date(status.next_lock_at).getTime() - now) / 60000));
  const what = { bedtime: 'Bedtime', school: 'School time', limit: "Today's time runs out" }[status.next_lock_reason];
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const span = h ? `${h} h${m ? ` ${m} min` : ''}` : `${m} min`;
  return `${what} in ${span}`;
}

export interface HeatCell {
  day: string;
  hour: number;
  count: number;
}

/**
 * Recorded activity by hour of day for the last `count` days (oldest first):
 * how many things happened in each hour, from the activity feed. Hours with
 * nothing recorded are zero; days before the oldest event the server still
 * keeps are marked so the chart can say the record does not reach back that far.
 */
export function hourHeatmap(events: ActivityEvent[], count = 14, today = localDay()): {
  days: string[];
  cells: HeatCell[];
  max: number;
  since: string | null;
} {
  const days = Array.from({ length: count }, (_, i) => shiftDay(today, i - count + 1));
  const wanted = new Set(days);
  const counts = new Map<string, number>();
  let since: string | null = null;
  for (const e of events) {
    const at = new Date(e.at);
    if (Number.isNaN(at.getTime())) continue;
    const day = localDay(at);
    if (!since || day < since) since = day;
    if (!wanted.has(day)) continue;
    const key = `${day}|${at.getHours()}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const cells = days.flatMap((day) =>
    Array.from({ length: 24 }, (_, hour) => ({ day, hour, count: counts.get(`${day}|${hour}`) || 0 })),
  );
  return { days, cells, max: Math.max(0, ...cells.map((c) => c.count)), since };
}

/** The activity timeline's kinds, in the order the filter shows them. */
export const ACTIVITY_KINDS: Array<{ id: string; label: string; features: string[] }> = [
  { id: 'media', label: 'Media', features: ['media', 'music', 'video', 'podcast', 'audiobook'] },
  { id: 'bible', label: 'Bible', features: ['bible'] },
  { id: 'games', label: 'Games', features: ['game', 'trivia'] },
  { id: 'comms', label: 'Calls & texts', features: ['sms', 'calls'] },
  { id: 'web', label: 'Web', features: ['web'] },
  { id: 'apps', label: 'App', features: ['page'] },
];

export function kindOf(feature: string): string {
  return ACTIVITY_KINDS.find((k) => k.features.includes(feature))?.id || 'other';
}

/** How many events of each kind in the last `days` days (from the events the server keeps). */
export function kindTotals(events: ActivityEvent[], days: number, today = localDay()): Record<string, number> {
  const from = shiftDay(today, -(days - 1));
  const out: Record<string, number> = {};
  for (const e of events) {
    const at = new Date(e.at);
    if (Number.isNaN(at.getTime())) continue;
    const day = localDay(at);
    if (day < from || day > today) continue;
    const kind = kindOf(e.feature);
    out[kind] = (out[kind] || 0) + 1;
  }
  return out;
}

/** The family place a position is inside, if any (the nearest when places overlap). */
export function placeAt(lat: number, lon: number, places: ZonePlace[]): string | null {
  let best: { name: string; d: number } | null = null;
  for (const p of places) {
    const d = distanceMeters(lat, lon, p.lat, p.lon);
    if (d <= p.radius && (!best || d < best.d)) best = { name: p.name, d };
  }
  return best?.name ?? null;
}
