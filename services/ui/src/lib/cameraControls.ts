import { apiClient } from '../services/api';
import type { HubEntity } from './dashboards';

/**
 * What a camera's device can do, found by the hub (homehub camera_controls.py)
 * and filtered by the gateway for the viewer: the actions a person takes from
 * the camera (a door opener, the siren, lights, locks...), what it reports
 * (detections, door state, battery), and, for admins, the device's settings.
 */

export interface CameraControl extends HubEntity {
  group: 'actions' | 'status' | 'settings';
  /** opener | siren | light | lock | cover | valve | fan | button | switch, or a detection/reading for status. */
  role: string;
  /** For an opener: the door sensor it is paired with. */
  door?: HubEntity;
}

export interface CameraControls {
  entity_id: string;
  actions: CameraControl[];
  status: CameraControl[];
  settings: CameraControl[];
}

export async function getCameraControls(entityId: string): Promise<CameraControls> {
  return (await apiClient.get<CameraControls>(`/api/dashboard/cameras/${encodeURIComponent(entityId)}/controls`)).data;
}

export const isOffline = (e: { state: string }) => e.state === 'unavailable' || e.state === 'unknown';

/** A door sensor's "on" is open. */
export const doorIsOpen = (door: { state: string } | undefined) => door?.state === 'on';

/**
 * A control's name without the camera's own name in front ("Backyard Camera 1
 * Person" on the Backyard camera reads "Person"); the whole name when that
 * would leave nothing.
 */
export function shortName(name: string, cameraName: string): string {
  // Only the camera's whole name comes off ("Backyard Camera 1 Person" on "Backyard Camera 1" is
  // "Person"): cutting single words would turn "Garage Door" on "Garage Camera" into a bare "Door".
  const prefix = cameraName.trim();
  const full = name.trim();
  if (prefix && full.toLowerCase().startsWith(`${prefix.toLowerCase()} `)) {
    const rest = full.slice(prefix.length).trim();
    if (rest) return rest[0].toUpperCase() + rest.slice(1);
  }
  return full;
}

/** What a status item says: a detection is Detected/Clear, a door Open/Closed, a reading its value. */
export function statusText(item: Pick<CameraControl, 'domain' | 'state' | 'device_class' | 'unit'>): string {
  if (isOffline(item)) return 'Not reporting';
  if (item.domain === 'binary_sensor') {
    const on = item.state === 'on';
    switch (item.device_class) {
      case 'door': case 'garage_door': case 'opening': case 'window': case 'gate': return on ? 'Open' : 'Closed';
      case 'battery': return on ? 'Low' : 'OK';
      case 'connectivity': return on ? 'Connected' : 'Disconnected';
      default: return on ? 'Detected' : 'Clear';
    }
  }
  const n = Number(item.state);
  const value = Number.isFinite(n) ? String(Math.round(n * 10) / 10) : item.state;
  return item.unit ? `${value}${item.unit === '%' ? '%' : ` ${item.unit}`}` : value;
}

/** Whether a status item should stand out: a detection now, a door open, a low battery. */
export function statusAlert(item: Pick<CameraControl, 'domain' | 'state' | 'device_class'>): boolean {
  if (item.domain === 'binary_sensor') return item.state === 'on' && item.device_class !== 'connectivity';
  if (item.device_class === 'battery') {
    const n = Number(item.state);
    return Number.isFinite(n) && n <= 20;
  }
  return false;
}

/** The service a quick action calls. */
export function actionService(item: Pick<CameraControl, 'domain' | 'role' | 'state'>): [string, string] {
  if (item.role === 'opener' || item.domain === 'button') {
    return item.domain === 'button' ? ['button', 'press'] : [item.domain, 'turn_on'];
  }
  switch (item.domain) {
    case 'lock': return ['lock', item.state === 'locked' ? 'unlock' : 'lock'];
    case 'cover': return ['cover', item.state === 'open' || item.state === 'opening' ? 'close_cover' : 'open_cover'];
    case 'valve': return ['valve', item.state === 'open' || item.state === 'opening' ? 'close_valve' : 'open_valve'];
    default: return [item.domain, item.state === 'on' ? 'turn_off' : 'turn_on'];
  }
}

/** Seconds the siren sounds for when started from the camera view. */
export const SIREN_SECONDS = 10;
