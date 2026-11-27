/**
 * What the phone can answer by itself with no family phone online: only from
 * the household information it holds, and always saying how old it is.
 * Anything else is queued for a family phone that is online.
 */
import type { CachedItem, OfflineCommand } from '../plugins/offlineMode';

export function asOf(ms: number | null | undefined, now = Date.now()): string {
  if (!ms) return 'time unknown';
  const d = new Date(ms);
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const sameDay = new Date(now).toDateString() === d.toDateString();
  return sameDay ? `as of ${time}` : `as of ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${time}`;
}

interface LocationRow {
  user?: string;
  username?: string;
  user_id?: string;
  name?: string;
  display_name?: string;
  zone?: string;
  place?: string;
  location_name?: string;
  latitude?: number;
  longitude?: number;
  lat?: number;
  lon?: number;
  timestamp?: number | string;
  updated_at?: number | string;
}

/** The rows of a cached locations answer, whatever its shape. */
export function locationRows(data: unknown): LocationRow[] {
  if (Array.isArray(data)) return data as LocationRow[];
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>;
    for (const k of ['locations', 'users', 'people']) if (Array.isArray(d[k])) return d[k] as LocationRow[];
    return Object.entries(d)
      .filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v))
      .map(([user, v]) => ({ user, ...(v as LocationRow) }));
  }
  return [];
}

function rowName(r: LocationRow): string {
  return String(r.display_name ?? r.name ?? r.username ?? r.user ?? r.user_id ?? '');
}

function rowPlace(r: LocationRow): string | null {
  const named = r.zone ?? r.place ?? r.location_name;
  if (named) return String(named);
  const lat = r.latitude ?? r.lat;
  const lon = r.longitude ?? r.lon;
  return typeof lat === 'number' && typeof lon === 'number' ? `${lat.toFixed(4)}, ${lon.toFixed(4)}` : null;
}

/**
 * An answer from the cache, or null when the question needs Jarvis itself.
 * Today: "where is <name>" from the last shared locations.
 */
export function localAnswer(question: string, cache: CachedItem[], now = Date.now()): string | null {
  const m = /\bwhere(?:'s| is| are)\s+([a-z][a-z' -]{0,40}?)\s*\??$/i.exec(question.trim());
  if (!m) return null;
  const who = m[1].replace(/'s$/i, '').trim().toLowerCase();
  const item = cache.find((c) => c.key === 'locations');
  if (!item) return null;
  const row = locationRows(item.data).find((r) => {
    const names = [rowName(r), r.username, r.user, r.user_id].filter(Boolean).map((n) => String(n).toLowerCase());
    return names.some((n) => n === who || n.split(/[ .]/)[0] === who);
  });
  if (!row) return null;
  const place = rowPlace(row);
  if (!place) return null;
  return `${rowName(row) || who} was at ${place} (${asOf(item.version, now)}, the last location this phone has).`;
}

/** The shopping list rows from the cached note text (the app's own format). */
export function shoppingRows(item: CachedItem | undefined): Array<{ text: string; checked: boolean }> {
  const text = String((item?.data as { text?: string } | undefined)?.text ?? '');
  const out: Array<{ text: string; checked: boolean }> = [];
  for (const line of text.split('\n')) {
    const box = /^-\s*\[([ xX])\]\s*(.+?)\s*$/.exec(line);
    if (box && box[2]) out.push({ text: box[2], checked: box[1].toLowerCase() === 'x' });
  }
  return out;
}

const normalise = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');

/**
 * The list as it will be once queued edits reach the server: added items and
 * ticks are shown provisionally until the server confirms them.
 */
export function provisionalShopping(
  rows: Array<{ text: string; checked: boolean }>,
  edits: Array<{ kind: string; item: string; status: string }>,
): Array<{ text: string; checked: boolean; provisional: boolean }> {
  const out = rows.map((r) => ({ ...r, provisional: false }));
  for (const e of edits) {
    if (e.status === 'expired') continue;
    const key = normalise(e.item);
    const at = out.find((r) => normalise(r.text) === key);
    if (e.kind === 'shopping_add' && !at) out.push({ text: e.item.trim(), checked: false, provisional: e.status !== 'answered' });
    if (e.kind === 'shopping_check' && at && !at.checked) {
      at.checked = true;
      at.provisional = e.status !== 'answered';
    }
  }
  return out;
}

const ACTION_WORDS: Record<OfflineCommand['action'], string> = { lock: 'Locked', unlock: 'Unlocked', bonus: 'Extra time' };

function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

/** "Locked at 3:41 PM, confirmed by Sam's phone" or "Not reached yet". */
export function commandDeliveryLine(c: OfflineCommand, childName: string): string {
  const what = c.action === 'bonus' ? `${c.arg} more minutes` : ACTION_WORDS[c.action];
  if (c.confirmed_at && c.result === 'applied') return `${what} at ${clock(c.confirmed_at)}, confirmed by ${childName}'s phone`;
  if (c.confirmed_at) return `${childName}'s phone refused it (${c.result?.replace(/^refused_/, '').replace(/_/g, ' ')})`;
  return `${what} sent at ${clock(c.issued_at)}. Not reached yet`;
}
