import { isAxiosError } from 'axios';
import { Capacitor } from '@capacitor/core';
import { apiClient } from '../services/api';
import type { Phone } from './phones';
import type { BeaconStatus } from '../plugins/bleBeacon';

/**
 * Devices > Room detection > Map rooms: recording where each room is, as the
 * hub's Bluetooth listeners hear a phone carried around it. Everything here is
 * the hub's map API through the gateway (admins only):
 * `/api/homehub/bluetooth/map[/sessions]`.
 */

export interface MapSession {
  session_id: string;
  area_id: string;
  tracker: string;
  ends_at: string;
}

export interface SessionStatus {
  status: 'recording' | 'done' | 'failed';
  samples: number;
  scanners_heard: number;
  seconds_left: number;
  error?: string | null;
}

export interface MapArea {
  area_id: string;
  name: string;
  samples: number;
  sessions: number;
  last_recorded: string | null;
  /** Share of this room's samples the map places back in it (0 to 1). */
  accuracy: number | null;
}

export interface RoomMap {
  method: 'fingerprint' | 'nearest_scanner';
  areas: MapArea[];
  scanners: unknown[];
  /** recorded room -> room the map placed the sample in -> count. */
  confusion: Record<string, Record<string, number>>;
  overall_accuracy: number | null;
  stale_reason?: string | null;
}

const BASE = '/api/homehub/bluetooth/map';

export async function getRoomMap(): Promise<RoomMap> {
  const resp = await apiClient.get(BASE);
  return resp.data;
}

export async function startMapSession(areaId: string, tracker: string, durationS: number): Promise<MapSession> {
  const resp = await apiClient.post(`${BASE}/sessions`, { area_id: areaId, tracker, duration_s: durationS });
  return resp.data;
}

export async function getMapSession(id: string): Promise<SessionStatus> {
  const resp = await apiClient.get(`${BASE}/sessions/${encodeURIComponent(id)}`);
  return resp.data;
}

export async function cancelMapSession(id: string): Promise<void> {
  await apiClient.delete(`${BASE}/sessions/${encodeURIComponent(id)}`);
}

export async function clearMappedRoom(areaId: string): Promise<void> {
  await apiClient.delete(`${BASE}/${encodeURIComponent(areaId)}`);
}

export async function clearWholeMap(): Promise<void> {
  await apiClient.delete(BASE, { params: { confirm: 1 } });
}

/** How long a pass records, in seconds; the first is the default. */
export const PASS_SECONDS = [60, 30, 120];

/** A recording older than this is stale: furniture and listeners move. */
export const STALE_AFTER_DAYS = 90;

/** Something to record: this phone's beacon, another phone, or a tag. */
export interface TrackerOption {
  id: string;
  label: string;
  /** Why it cannot be recorded, when it cannot. */
  unavailable?: string;
}

/** The tracker id of this phone's own beacon: `uuid-major-minor`, as the hub names beacons. */
export function beaconTrackerId(status: Pick<BeaconStatus, 'uuid' | 'major' | 'minor'>): string {
  return `${status.uuid}-${status.major}-${status.minor}`;
}

/** Whether this device is the Jarvis Android app with its beacon on the air. */
export function beaconIsLive(status: BeaconStatus | null): boolean {
  return !!status && Capacitor.getPlatform() === 'android' && status.state === 'advertising' && !!status.uuid;
}

/**
 * The phones the hub can follow by Bluetooth. The hub publishes a phone's
 * room as `sensor.<phone>_room` and takes the phone itself as the tracker
 * `sensor.<phone>`, so the tracker is the room sensor's id without `_room`. A
 * phone with no room sensor has no beacon registered and is listed as such.
 */
export function phoneTrackers(phones: Phone[]): TrackerOption[] {
  return phones.map((p) => {
    const room = p.sensors.find((s) => s.domain === 'sensor' && s.entity_id.endsWith('_room'));
    return room
      ? { id: room.entity_id.slice(0, -'_room'.length), label: p.name }
      : { id: `phone:${p.entry_id}`, label: p.name, unavailable: 'no beacon registered with the hub' };
  });
}

/** A tag or beacon id typed in: `uuid-major-minor`. */
export function validTagId(text: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-\d{1,5}-\d{1,5}$/i.test(text.trim());
}

/** "82%" for an accuracy (0 to 1; a value over 1 is already a percentage). */
export function percent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'not measured';
  return `${Math.round(value <= 1 ? value * 100 : value)}%`;
}

export type RoomState = 'not_mapped' | 'mapped' | 'stale';

export interface RoomRow {
  area_id: string;
  name: string;
  state: RoomState;
  accuracy: number | null;
  sessions: number;
  samples: number;
  last_recorded: string | null;
  /** Why the room counts as stale. */
  staleWhy?: string;
}

/** Every hub area with how well it is mapped, in the hub's area order. */
export function roomRows(areas: { area_id: string; name: string }[], map: RoomMap | undefined,
  now: number = Date.now()): RoomRow[] {
  const mapped = new Map((map?.areas ?? []).map((a) => [a.area_id, a]));
  return areas.map((area) => {
    const m = mapped.get(area.area_id);
    if (!m || !m.samples) {
      return { area_id: area.area_id, name: area.name, state: 'not_mapped', accuracy: null, sessions: 0, samples: 0, last_recorded: null };
    }
    const at = m.last_recorded ? Date.parse(m.last_recorded) : NaN;
    const old = Number.isFinite(at) && now - at > STALE_AFTER_DAYS * 86_400_000;
    const staleWhy = map?.stale_reason || (old ? `recorded more than ${STALE_AFTER_DAYS} days ago` : undefined);
    return {
      area_id: area.area_id, name: area.name, state: staleWhy ? 'stale' : 'mapped', accuracy: m.accuracy,
      sessions: m.sessions, samples: m.samples, last_recorded: m.last_recorded, staleWhy,
    };
  });
}

export interface ConfusedPair {
  a: string;
  b: string;
  /** Samples of either room the map placed in the other. */
  count: number;
}

/** The room pairs the map mixes up most (both directions added), worst first. */
export function confusedPairs(confusion: RoomMap['confusion'] | undefined, names: Record<string, string>,
  limit = 3): ConfusedPair[] {
  const sums = new Map<string, ConfusedPair>();
  for (const [from, row] of Object.entries(confusion ?? {})) {
    for (const [to, count] of Object.entries(row ?? {})) {
      if (from === to || !(count > 0)) continue;
      const [x, y] = [from, to].sort();
      const key = `${x}\u0000${y}`;
      const pair = sums.get(key) ?? { a: names[x] ?? x, b: names[y] ?? y, count: 0 };
      pair.count += count;
      sums.set(key, pair);
    }
  }
  return [...sums.values()].sort((p, q) => q.count - p.count || p.a.localeCompare(q.a)).slice(0, limit);
}

/** The message for a recording that could not start, in plain words. */
export function startErrorText(err: unknown, trackerIsThisPhone: boolean): string {
  if (isAxiosError(err)) {
    const status = err.response?.status;
    const data = err.response?.data as { detail?: string; message?: string } | undefined;
    const hub = data?.detail || data?.message || '';
    if (status === 409) {
      const lead = trackerIsThisPhone
        ? "Your phone isn't being heard. Check that Room detection is on in the Jarvis app."
        : "That tracker isn't being heard by any listener right now.";
      return hub ? `${lead} (${hub})` : lead;
    }
    if (status === 404) return hub || 'The hub does not know that room any more. Reload the page.';
    if (status === 403 || status === 401) return 'Mapping rooms is for admins.';
    if (hub) return hub;
  }
  return 'The recording could not start. Try again.';
}

/** "1:05" for a countdown in seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
