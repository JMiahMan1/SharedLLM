import { apiClient } from '../services/api';

/**
 * Devices > Phones: the family's phones as the device hub sees them
 * (services/gateway/phone_routes.py, admins only). A phone is either a Home
 * Assistant companion app registration or a phone fed by Jarvis's own reports.
 */

export type SensorSource = 'companion' | 'jarvis';

export interface PhoneSensor {
  entity_id: string;
  name: string;
  domain: string;
  state: string | null;
  unit: string | null;
  device_class: string | null;
  disabled: boolean;
  source: SensorSource;
  last_updated: string | null;
}

export interface Phone {
  entry_id: string;
  name: string;
  kind: 'companion' | 'jarvis';
  app: Record<string, string | null>;
  jarvis_user: string | null;
  jarvis_fallback: boolean;
  companion_last_seen: number | null;
  companion_fresh: boolean;
  encrypted: boolean;
  push: { relay: boolean; local_channel: boolean };
  notify_service: string;
  state: string;
  sensors: PhoneSensor[];
}

export interface PhoneSettings {
  external_url: string;
  internal_url: string;
  companion_fresh_minutes: number;
  jarvis_fallback: boolean;
  auto_jarvis_phones: boolean;
  jarvis_phone_name: string;
  push_url_allowlist: string[];
}

export interface PhoneList {
  phones: Phone[];
  settings: PhoneSettings;
}

const path = (id: string) => `/api/homehub/phones/${encodeURIComponent(id)}`;

export async function getPhones(): Promise<PhoneList> {
  const resp = await apiClient.get('/api/homehub/phones');
  return resp.data;
}

export async function addJarvisPhone(jarvisUser: string, deviceName?: string): Promise<{ entry_id: string; name: string }> {
  const resp = await apiClient.post('/api/homehub/phones', { jarvis_user: jarvisUser, ...(deviceName ? { device_name: deviceName } : {}) });
  return resp.data;
}

export async function updatePhone(id: string, patch: { jarvis_user?: string | null; jarvis_fallback?: boolean }): Promise<unknown> {
  const resp = await apiClient.patch(path(id), patch);
  return resp.data;
}

export async function removePhone(id: string): Promise<unknown> {
  const resp = await apiClient.delete(path(id));
  return resp.data;
}

export async function savePhoneSettings(patch: Partial<PhoneSettings>): Promise<PhoneSettings> {
  const resp = await apiClient.put('/api/homehub/phones/settings', patch);
  return resp.data;
}

/** "5 min ago" for a companion app's last report (seconds since the epoch). */
export function lastSeenText(epochSeconds: number | null, now = Date.now()): string {
  if (!epochSeconds) return 'never reported here';
  const minutes = Math.round((now - epochSeconds * 1000) / 60000);
  if (minutes < 2) return 'reported just now';
  if (minutes < 90) return `reported ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `reported ${hours} h ago`;
  return `reported ${Math.round(hours / 24)} days ago`;
}

/** The handful of sensors worth showing on a phone's row, the rest behind "all sensors". */
export const KEY_SENSORS = ['_battery_level', '_battery_state', '_charger_type', '_steps_sensor', '_detected_activity',
  '_android_auto', '_wifi_connection', '_next_alarm'];

export function keySensors(phone: Phone): PhoneSensor[] {
  const byKey = (s: PhoneSensor) => KEY_SENSORS.findIndex((k) => s.entity_id.endsWith(k) || s.entity_id.includes(`${k}_`));
  return phone.sensors
    .filter((s) => !s.disabled && byKey(s) >= 0)
    .sort((a, b) => byKey(a) - byKey(b));
}
