import { getServerOrigin } from './serverUrl';
import type { MediaToken } from './mediaToken';
import type { CameraRealtime } from './cameraLive';

/** One camera as the gateway lists it (homehub's `/api/homehub/cameras`). */
export interface CameraInfo {
  entity_id: string;
  name: string;
  area?: string | null;
  platform: string;
  /** `idle`, or `unavailable` when the hub cannot reach it. */
  state: string;
  /** Whether recorded footage can be listed and played. */
  recordings: boolean;
  live: string[];
  /** `camera` when the live view is the camera's own video, `snapshots` when it is built from stills. */
  stream?: 'camera' | 'snapshots';
  snapshot_interval?: number | null;
  /** Battery cameras sleep; every picture wakes them. */
  battery?: boolean;
  sleeping?: boolean;
  model?: string;
  /** Present when go2rtc can play this camera in real time (WebRTC, MSE or MP4). */
  realtime?: CameraRealtime | null;
}

export interface CameraRecording {
  file: string;
  start: string;
  end: string;
  /** Seconds. */
  duration: number;
  triggers: string[];
  size?: number | null;
  stream?: string | null;
}

export interface CameraRecordings {
  entity_id: string;
  start: string;
  end: string;
  recordings: CameraRecording[];
}

export const isCameraOnline = (camera: CameraInfo) => camera.state !== 'unavailable' && camera.state !== 'unknown';

/**
 * A camera's picture, live stream or recording for an `<img>` or `<video>`,
 * signed with the media token (they cannot send the API key). Null until a
 * token is held; the page re-renders when `useMediaToken()` gets one.
 */
export function cameraMediaUrl(
  token: MediaToken | null,
  entityId: string,
  kind: 'snapshot' | 'stream' | 'play',
  extra: Record<string, string> = {},
): string | null {
  if (!token) return null;
  const path = kind === 'play' ? 'recordings/play' : kind;
  const query = new URLSearchParams({ ...extra, user: token.user, mt: token.token });
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/cameras/${encodeURIComponent(entityId)}/${path}?${query.toString()}`;
}

/** Local midnight to the end of that day (or now, for today), as ISO strings for the recordings query. */
export function dayRange(day: Date, now: Date = new Date()): { start: string; end: string } {
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const endOfDay = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1, 0, 0, 0, -1);
  const end = endOfDay > now ? now : endOfDay;
  return { start: start.toISOString(), end: (end > start ? end : endOfDay).toISOString() };
}

/** `yyyy-mm-dd` in local time, for `<input type="date">`. */
export function toDateInput(day: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

/** The local day an `<input type="date">` value names, or null for junk. */
export function fromDateInput(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  const day = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(day.getTime()) ? null : day;
}

export function addDays(day: Date, days: number): Date {
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() + days);
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** `0:42`, `3:05`, `1:02:07`. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** A recording's clock time, in the viewer's time zone. */
export function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

const TRIGGER_LABELS: Record<string, string> = {
  person: 'Person',
  vehicle: 'Vehicle',
  animal: 'Animal',
  package: 'Package',
  face: 'Face',
  doorbell: 'Doorbell',
  motion: 'Motion',
  crossline: 'Line crossed',
  intrusion: 'Intrusion',
  linger: 'Loitering',
  forgotten_item: 'Item left',
  taken_item: 'Item taken',
  crying: 'Crying',
  io: 'Sensor',
  timer: 'Scheduled',
};

export function triggerLabel(trigger: string): string {
  return TRIGGER_LABELS[trigger] ?? trigger.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

const TRIGGER_STYLES: Record<string, string> = {
  person: 'bg-amber-500/15 text-amber-200 border-amber-500/30',
  vehicle: 'bg-sky-500/15 text-sky-200 border-sky-500/30',
  animal: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  package: 'bg-purple-500/15 text-purple-200 border-purple-500/30',
};

export function triggerStyle(trigger: string): string {
  return TRIGGER_STYLES[trigger] ?? 'bg-white/5 text-slate-300 border-white/10';
}

/** How often a tile refreshes its picture: battery cameras never on their own, since each picture wakes them. */
export function tileRefreshMs(camera: CameraInfo): number | null {
  if (camera.battery) return null;
  return 10_000;
}
