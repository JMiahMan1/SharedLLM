import { apiClient, getNdjson } from '../services/api';

/**
 * Devices > an ESPHome device > Logs: the node's live log, like Home Assistant's ESPHome dashboard "Logs" button,
 * built into the hub (services/homehub/esphome_logs.py through services/gateway/discovery_routes.py, admins only).
 * The hub fans every viewer out from its one connection to the node; while a view is open the node is asked for
 * that view's level and it drops back when the last one closes. Nothing here names a device.
 */

export type ViewLevel = 'error' | 'warning' | 'info' | 'debug' | 'verbose';
export type LineLevel = 'error' | 'warning' | 'info' | 'config' | 'debug' | 'verbose' | 'very_verbose';

export interface LogLineItem {
  type: 'line';
  seq: number;
  at: string;
  level: LineLevel;
  tag: string;
  message: string;
}

export interface LogMarkerItem {
  type: 'marker';
  seq: number | null;
  at: string;
  /** `online`, `offline`, `restart`, `drop`, `unknown`, `dump_config` or `skipped`. */
  event: string;
  message: string;
}

export type LogItem = LogLineItem | LogMarkerItem;

export type StreamItem =
  | LogItem
  | { type: 'hello'; seq: number; level: ViewLevel; device_id: string; title?: string; connected: boolean }
  | { type: 'ping'; seq: number }
  | { type: 'end'; reason: string; seq: number };

export const VIEW_LEVELS: ViewLevel[] = ['error', 'warning', 'info', 'debug', 'verbose'];

export const VIEW_LEVEL_TEXT: Record<ViewLevel, string> = {
  error: 'Errors',
  warning: 'Warnings',
  info: 'Info',
  debug: 'Debug',
  verbose: 'Verbose',
};

/** How detailed each level is (aioesphomeapi's numbers); `info` includes the config dump. */
const RANK: Record<LineLevel, number> = { error: 1, warning: 2, info: 3, config: 4, debug: 5, verbose: 6, very_verbose: 7 };
const VIEW_RANK: Record<ViewLevel, number> = { error: 1, warning: 2, info: 4, debug: 5, verbose: 6 };

/** Whether a view at `view` shows the item (markers always show). */
export function shownAt(item: LogItem, view: ViewLevel): boolean {
  return item.type === 'marker' || (RANK[item.level] ?? 3) <= VIEW_RANK[view];
}

/** Case-insensitive text search over the tag and the message. */
export function matches(item: LogItem, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  if (item.type === 'marker') return item.message.toLowerCase().includes(q);
  return item.message.toLowerCase().includes(q) || item.tag.toLowerCase().includes(q);
}

/** The level's one-letter mark, as ESPHome prints it. */
export function levelLetter(level: LineLevel): string {
  return { error: 'E', warning: 'W', info: 'I', config: 'C', debug: 'D', verbose: 'V', very_verbose: 'VV' }[level] ?? '?';
}

/** `12:34:56.789` in the viewer's own time. */
export function timeText(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

/** The view as a plain-text file. */
export function logText(items: LogItem[]): string {
  return items.map((i) => (i.type === 'marker'
    ? `${i.at} ---- ${i.message} ----`
    : `${i.at} [${levelLetter(i.level)}]${i.tag ? `[${i.tag}]` : ''} ${i.message}`)).join('\n') + (items.length ? '\n' : '');
}

/**
 * Add new items to a view's list, keeping it in sequence order without duplicates (a reopened stream sends what
 * came after its `since`, but a marker without a sequence number is always new), and at most `max` long.
 */
export function mergeItems(current: LogItem[], incoming: LogItem[], max: number): LogItem[] {
  if (!incoming.length) return current;
  const last = current.reduce((m, i) => (i.seq !== null && i.seq > m ? i.seq : m), 0);
  const fresh = incoming.filter((i) => i.seq === null || i.seq > last);
  if (!fresh.length) return current;
  const out = current.concat(fresh);
  return out.length > max ? out.slice(out.length - max) : out;
}

export async function requestConfigDump(id: string): Promise<void> {
  await apiClient.post(`/api/homehub/devices/${encodeURIComponent(id)}/logs/dump_config`);
}

/** Open the live stream from `since` at `level`; resolves when the server ends it (the caller reopens it). */
export async function streamDeviceLogs(
  id: string,
  level: ViewLevel,
  since: number | null,
  onItem: (item: StreamItem) => void,
  signal: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({ level });
  if (since !== null) q.set('since', String(since));
  await getNdjson<StreamItem>(`/api/homehub/devices/${encodeURIComponent(id)}/logs/stream?${q}`, onItem, signal,
    'The device log could not be opened');
}
