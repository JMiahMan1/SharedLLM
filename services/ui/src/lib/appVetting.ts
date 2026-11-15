// App vetting in plain words: the Apps card, request cards and the device's app report.
import type { AppVerdict, ConcernLevel, RequestVetting, UnusedApp } from '../types/api';

export const LEVEL_WORDS: Record<ConcernLevel, string> = { severe: 'Severe', high: 'High', medium: 'Worth a look' };

export const LEVEL_STYLES: Record<ConcernLevel, string> = {
  severe: 'border-red-400/40 bg-red-400/10 text-red-200',
  high: 'border-amber-400/40 bg-amber-400/10 text-amber-200',
  medium: 'border-white/15 bg-white/5 text-slate-200',
};

const LEVEL_ORDER: ConcernLevel[] = ['medium', 'high', 'severe'];

/** The most serious of some levels, or null. */
export function worstLevel(levels: Array<ConcernLevel | null | undefined>): ConcernLevel | null {
  let worst: ConcernLevel | null = null;
  for (const level of levels) {
    if (level && (!worst || LEVEL_ORDER.indexOf(level) > LEVEL_ORDER.indexOf(worst))) worst = level;
  }
  return worst;
}

/** What the lookup and review say about where an app stands, when there is no verdict yet. */
export function lookupWords(app: Pick<AppVerdict, 'lookup' | 'review_state' | 'lookup_reason' | 'review_reason'>): string | null {
  if (app.lookup === 'checking' || app.lookup === 'pending') return 'Checking its Play listing and reviews';
  if (app.lookup === 'not_on_play') return 'Not on Google Play: judged on its permissions';
  if (app.lookup === 'off') return 'Play lookups are switched off on the server';
  if (app.lookup === 'error') return `Its Play listing could not be read${app.lookup_reason ? ` (${app.lookup_reason})` : ''}`;
  if (app.review_state === 'checking') return 'The local review is on its way';
  if (app.review_state === 'no_model') return 'No local model is set up to review it';
  if (app.review_state === 'failed') return 'The local review could not be made; it will try again';
  return null;
}

/** The short facts beside an app: rating, ads, purchases, chat. */
export function factChips(app: Pick<AppVerdict, 'rating' | 'ads' | 'in_app_purchases' | 'interactive' | 'store'>): string[] {
  const chips: string[] = [];
  if (app.rating) chips.push(app.rating);
  if (app.ads) chips.push('Ads');
  if (app.in_app_purchases) chips.push('In-app purchases');
  for (const word of app.interactive || []) {
    if (!/purchase/i.test(word)) chips.push(word);
  }
  if (app.store && app.store !== 'Google Play') chips.push(app.store);
  return chips;
}

const DAY_MS = 86_400_000;

/** "today", "3 days ago", "5 months ago" for a time in epoch milliseconds. */
export function sinceWords(ms: number | null | undefined, now: number = Date.now()): string {
  if (!ms) return 'never';
  const days = Math.floor((now - ms) / DAY_MS);
  if (days < 1) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 60) return `${days} days ago`;
  const months = Math.round(days / 30);
  if (months < 24) return `${months} months ago`;
  return `${Math.round(days / 365)} years ago`;
}

/** "120 MB" for a byte count; empty when it is not known. */
export function sizeWords(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return '<1 MB';
  if (mb < 1024) return `${Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

/** One line for a removal suggestion: when it was last opened, and how big it is. */
export function unusedLine(app: UnusedApp, now: number = Date.now()): string {
  const when = app.never_used ? `No use recorded since it was installed ${sinceWords(app.first_install, now)}` : `Last used ${sinceWords(app.last_used, now)}`;
  const size = sizeWords(app.size_bytes);
  return size ? `${when} · ${size}` : when;
}

/** The rating against the limit, in words a parent reads at a glance. */
export function ratingLine(v: Pick<RequestVetting, 'rating' | 'limit' | 'over_age'>): string | null {
  if (!v.rating) return null;
  if (!v.limit) return `Rated ${v.rating} · no age limit set`;
  return v.over_age ? `Rated ${v.rating} · above the ${v.limit} limit` : `Rated ${v.rating} · within the ${v.limit} limit`;
}

/** An age in years from a YYYY-MM-DD birth date, or null. */
export function ageFrom(birthDate: string | null | undefined, today: Date = new Date()): number | null {
  if (!birthDate || !/^\d{4}-\d{2}-\d{2}$/.test(birthDate)) return null;
  const [y, m, d] = birthDate.split('-').map(Number);
  let age = today.getFullYear() - y;
  if (today.getMonth() + 1 < m || (today.getMonth() + 1 === m && today.getDate() < d)) age -= 1;
  return age >= 0 ? age : null;
}

// --- the device's report ----------------------------------------------------

export interface InstalledAppRow {
  package: string;
  label: string;
  installer: string | null;
  first_install: number | null;
  last_update: number | null;
  launchable: boolean;
  permissions: string[];
  last_used: number | null;
  size_bytes: number | null;
}

/** What changes the app list: a new or removed app, an update, new permissions.
 *  Last use and size move all the time and are sent with the daily refresh. */
export function inventoryFingerprint(apps: InstalledAppRow[]): string {
  return apps
    .map((a) => `${a.package}|${a.last_update ?? ''}|${a.installer ?? ''}|${(a.permissions || []).join(',')}`)
    .sort()
    .join('\n');
}

export const INVENTORY_REFRESH_MS = DAY_MS;

/** Whether to send the list now: it changed, a day has passed, or another person signed in. */
export function shouldSendInventory(
  last: { fingerprint: string; at: number; user: string } | null,
  fingerprint: string,
  user: string,
  now: number = Date.now(),
): boolean {
  if (!last || last.user !== user) return true;
  return last.fingerprint !== fingerprint || now - last.at >= INVENTORY_REFRESH_MS;
}
