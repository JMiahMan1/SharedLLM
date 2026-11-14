import { getServerOrigin } from './serverUrl';
import type { MediaToken } from './mediaToken';
import { isUnavailable, type HubEntity } from './dashboards';

/**
 * What the controls for buttons, numbers, selects, times, remotes, update and
 * image entities and a vacuum's rooms read off an entity's state. Everything
 * comes from the attributes its driver published (HA's meaning); nothing here
 * names a device or an integration.
 */

const num = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};

export interface NumberRange { min: number; max: number; step: number; mode: 'slider' | 'box'; unit: string | null }

/** A number entity's range, HA's defaults (0 to 100, step 1) where it said nothing. `auto` is a slider when the range is small. */
export function numberRange(e: Pick<HubEntity, 'attributes' | 'unit'>): NumberRange {
  const a = e.attributes ?? {};
  let min = num(a.min) ?? 0;
  let max = num(a.max) ?? 100;
  if (max < min) [min, max] = [max, min];
  const rawStep = num(a.step);
  const step = rawStep != null && rawStep > 0 ? rawStep : 1;
  const unit = (typeof a.unit_of_measurement === 'string' && a.unit_of_measurement) || e.unit || null;
  const mode = a.mode === 'box' ? 'box' : a.mode === 'slider' ? 'slider' : (max - min) / step <= 256 ? 'slider' : 'box';
  return { min, max, step, mode, unit };
}

/** Decimals a step needs, so 0.1 steps do not show as 0.30000000000000004. */
function decimals(step: number): number {
  const s = String(step);
  return s.includes('e-') ? Number(s.split('e-')[1]) : (s.split('.')[1]?.length ?? 0);
}

/** A value on the entity's grid, inside its range. */
export function snapNumber(value: number, r: NumberRange): number {
  const clamped = Math.min(r.max, Math.max(r.min, value));
  const stepped = r.min + Math.round((clamped - r.min) / r.step) * r.step;
  return Number(Math.min(r.max, stepped).toFixed(decimals(r.step)));
}

/** "21.5 °C" for a number entity's value. */
export function formatNumber(value: number | null, r: NumberRange): string {
  if (value == null) return '--';
  const v = value.toFixed(decimals(r.step));
  return r.unit ? `${v} ${r.unit}` : v;
}

/** A select's options, as the entity publishes them. */
export function selectOptions(e: Pick<HubEntity, 'attributes'>): string[] {
  const o = e.attributes?.options;
  return Array.isArray(o) ? o.map(String) : [];
}

/** A time entity's state ("22:00:00") as an `<input type="time">` value ("22:00"), or "" when unknown. */
export function timeInputValue(state: string): string {
  const m = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(state.trim());
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return '';
  return `${m[1].padStart(2, '0')}:${m[2]}`;
}

/** What `time.set_value` takes for an input's value ("21:15" -> "21:15:00"); null when it is not a time. */
export function timeServiceValue(input: string): string | null {
  const v = timeInputValue(input);
  return v ? `${v}:00` : null;
}

/** The key names a remote's driver knows (`command_list`), and the activities it can start. */
export function remoteCommands(e: Pick<HubEntity, 'attributes'>): { commands: string[]; activities: string[] } {
  const list = (v: unknown) => (Array.isArray(v) ? v.map(String).filter(Boolean) : []);
  return { commands: list(e.attributes?.command_list), activities: list(e.attributes?.activity_list) };
}

/** "volume_up" -> "Volume up". */
export function commandLabel(c: string): string {
  const s = c.replace(/_/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export interface UpdateInfo {
  installed: string | null;
  latest: string | null;
  skipped: string | null;
  /** A newer version is offered and not skipped (HA's `on`). */
  available: boolean;
  /** The integration can install it (the entity advertises install, HA's feature 1). */
  canInstall: boolean;
  inProgress: boolean;
  releaseUrl: string | null;
  summary: string | null;
  title: string | null;
}

/** An update entity's versions and what can be done about them. */
export function updateInfo(e: Pick<HubEntity, 'state' | 'attributes' | 'abilities'>): UpdateInfo {
  const a = e.attributes ?? {};
  const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v : typeof v === 'number' ? String(v) : null);
  const features = num(a.supported_features) ?? 0;
  const url = str(a.release_url);
  const canInstall = Array.isArray(e.abilities) ? e.abilities.includes('update') : (features & 1) === 1;
  return {
    installed: str(a.installed_version), latest: str(a.latest_version), skipped: str(a.skipped_version),
    available: e.state === 'on', canInstall: canInstall && !isUnavailable(e.state),
    inProgress: a.in_progress === true || typeof a.in_progress === 'number',
    releaseUrl: url && /^https?:\/\//i.test(url) ? url : null,
    summary: str(a.release_summary), title: str(a.title),
  };
}

/** One line for an update: "Up to date (1.2.0)", "2.0.0 available (1.2.0 installed)", ... */
export function updateLine(u: UpdateInfo): string {
  if (u.inProgress) return 'Installing...';
  if (u.available && u.latest) return `${u.latest} available${u.installed ? ` (${u.installed} installed)` : ''}`;
  if (u.skipped && u.latest && u.skipped === u.latest) return `${u.latest} skipped${u.installed ? ` (${u.installed} installed)` : ''}`;
  if (u.installed && u.latest) return `Up to date (${u.installed})`;
  if (u.installed) return `${u.installed} installed; latest not known`;
  return 'Version not known';
}

export interface Segment { id: number; name: string }

/** A vacuum's rooms (`segments`, published by drivers that can clean chosen rooms). */
export function vacuumSegments(e: Pick<HubEntity, 'attributes'>): Segment[] {
  const raw = e.attributes?.segments;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((s) => {
    const id = num((s as { id?: unknown })?.id);
    const name = (s as { name?: unknown })?.name;
    return id == null ? [] : [{ id, name: typeof name === 'string' && name ? name : `Room ${id}` }];
  });
}

/** The `vacuum.send_command` data that cleans the chosen rooms once (the command HA's Roborock integration documents). */
export function segmentCleanData(ids: number[], repeat = 1): Record<string, unknown> {
  return { command: 'app_segment_clean', params: [{ segments: [...ids], repeat }] };
}

/** An image entity's picture through the gateway (`<img>` cannot send a header, so it is signed). */
export function imageMediaUrl(token: MediaToken | null, entityId: string, version?: string): string | null {
  if (!token) return null;
  const query = new URLSearchParams({ user: token.user, mt: token.token });
  // The state is when the picture last changed: a new one is a new URL, so the browser fetches it.
  if (version && !isUnavailable(version)) query.set('v', version);
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/images/${encodeURIComponent(entityId)}?${query.toString()}`;
}
