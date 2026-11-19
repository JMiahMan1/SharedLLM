import { apiClient } from '../services/api';

/**
 * Devices > a device > Restarts: an ESPHome node's restarts and dropped connections, with the likely cause
 * (services/homehub/esphome_restarts.py through services/gateway/discovery_routes.py, admins only).
 * Nothing here names a device: every event, sensor and line comes from the hub.
 */

export type RestartKind = 'restart' | 'drop' | 'unknown';
export type LogLevel = 'off' | 'error' | 'warning' | 'info' | 'debug';
/** `[when, level letter, text]`. */
export type LogLine = [string, string, string];

export interface MemoryReading {
  before: number | null;
  lowest: number | null;
  after: number | null;
}

export interface RestartEvent {
  id: string;
  at: string;
  reconnected_at: string | null;
  kind: RestartKind;
  summary: string;
  evidence: string;
  cause: string | null;
  hint: string | null;
  downtime_s: number | null;
  expected: boolean;
  error: string | null;
  silent_s: number | null;
  reset_reason: string | null;
  last_reset_reason: string | null;
  last_crash: string | null;
  memory: Record<string, MemoryReading>;
  context_text: string[];
  log_level: LogLevel | null;
  dropped_lines: number;
  lines_before: LogLine[];
  lines_after: LogLine[];
}

export interface RestartGuidance {
  missing: string[];
  yaml: string;
  text: string;
}

export interface DeviceRestarts {
  supported: boolean;
  device_id: string;
  name?: string;
  title?: string;
  connected?: boolean;
  log_level?: LogLevel;
  log_levels?: LogLevel[];
  subscribed?: boolean;
  subscribe_error?: string | null;
  sensors?: string[];
  has_debug?: boolean;
  guidance?: RestartGuidance | null;
  current?: Record<string, number | string>;
  buffer?: { lines: number; max: number; dropped: number };
  offline?: { since: string; for_s: number; reconnected: boolean } | null;
  counts_1h?: Record<RestartKind, number>;
  counts_24h?: Record<RestartKind, number>;
  events?: RestartEvent[];
  recent_lines?: LogLine[];
}

export const restartsKey = (id: string) => ['homehub-restarts', id];

export async function getDeviceRestarts(id: string): Promise<DeviceRestarts> {
  const resp = await apiClient.get(`/api/homehub/devices/${encodeURIComponent(id)}/restarts`);
  return resp.data as DeviceRestarts;
}

export async function setRestartLogLevel(id: string, level: LogLevel): Promise<DeviceRestarts> {
  const resp = await apiClient.patch(`/api/homehub/devices/${encodeURIComponent(id)}/restarts`, { log_level: level });
  return resp.data as DeviceRestarts;
}

export const KIND_TEXT: Record<RestartKind, string> = {
  restart: 'Restarted',
  drop: 'Connection dropped',
  unknown: 'Restart or drop not known',
};

export const LEVEL_TEXT: Record<LogLevel, string> = {
  off: 'Off',
  error: 'Errors only',
  warning: 'Warnings (normal)',
  info: 'Info',
  debug: 'Debug (investigating)',
};

const MEMORY_TEXT: Record<string, string> = {
  heap_free: 'Free heap', heap_min_free: 'Lowest free heap', heap_max_block: 'Largest free block', psram_free: 'Free PSRAM',
};

export function bytesText(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '?';
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} kB`;
  return `${Math.round(n)} B`;
}

export function durationText(s: number | null | undefined): string {
  if (s === null || s === undefined) return '';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** "Free heap 12 kB before, 195 kB after" for each reading the node had. */
export function memoryLines(memory: Record<string, MemoryReading> | undefined): string[] {
  return Object.entries(memory ?? {}).map(([role, m]) => {
    const parts = [
      m.before !== null ? `${bytesText(m.before)} before` : null,
      m.lowest !== null && m.lowest !== m.before ? `lowest ${bytesText(m.lowest)}` : null,
      m.after !== null ? `${bytesText(m.after)} after` : null,
    ].filter(Boolean);
    return `${MEMORY_TEXT[role] ?? role}: ${parts.join(', ')}`;
  });
}

/** One sentence for the 24 h counts ("2 restarts, 1 dropped connection"), or null when nothing happened. */
export function countsText(counts: Record<RestartKind, number> | undefined): string | null {
  if (!counts) return null;
  const parts: string[] = [];
  const add = (n: number, one: string, many: string) => { if (n) parts.push(`${n} ${n === 1 ? one : many}`); };
  add(counts.restart, 'restart', 'restarts');
  add(counts.drop, 'dropped connection', 'dropped connections');
  add(counts.unknown, 'disconnect of unknown cause', 'disconnects of unknown cause');
  return parts.length ? parts.join(', ') : null;
}
