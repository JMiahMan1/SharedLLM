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
  /** Why the sensor is unavailable, when its source said (an app too old to report it). */
  reason?: string | null;
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
  /** The Jarvis app's room-detection beacon as the phone last reported it; null or absent when never. */
  beacon?: PhoneBeacon | null;
}

/** A phone's room-detection beacon (gateway: the Jarvis app's last report, and whether the hub took it). */
export interface PhoneBeacon {
  state: 'advertising' | 'bluetooth_off' | 'disabled' | 'unavailable';
  reason: string | null;
  uuid: string | null;
  major: number | null;
  minor: number | null;
  tx_power: number | null;
  tx_power_level: string | null;
  reported_at: string | null;
  hub_registered: boolean;
  hub_detail: string | null;
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
export const KEY_SENSORS = ['_battery_level', '_battery_state', '_charger_type', '_is_charging', '_steps_sensor',
  '_detected_activity', '_android_auto', '_wifi_connection', '_next_alarm'];

export function keySensors(phone: Phone): PhoneSensor[] {
  const byKey = (s: PhoneSensor) => KEY_SENSORS.findIndex((k) => s.entity_id.endsWith(k) || s.entity_id.includes(`${k}_`));
  return phone.sensors
    .filter((s) => !s.disabled && byKey(s) >= 0)
    .sort((a, b) => byKey(a) - byKey(b));
}

const BATTERY_STATES: Record<string, string> = {
  charging: 'Charging', discharging: 'On battery', full: 'Full', not_charging: 'Not charging',
};
const CHARGER_TYPES: Record<string, string> = { ac: 'AC', usb: 'USB', wireless: 'Wireless', dock: 'Dock', none: 'None' };
/** How a plug reads inside "Charging (...)". */
const PLUG_WORDS: Record<string, string> = { ac: 'AC', usb: 'USB', wireless: 'wireless', dock: 'dock' };
const NO_VALUE = new Set(['unknown', 'unavailable']);

const sensorWith = (phone: Phone, suffix: string) =>
  phone.sensors.find((s) => !s.disabled && s.entity_id.endsWith(suffix));

const hasValue = (s: PhoneSensor | undefined): s is PhoneSensor & { state: string } =>
  !!s && s.state != null && !NO_VALUE.has(s.state);

/** "Tue 6:30 AM": a next alarm (ISO 8601) in the viewer's time, or the zone given. */
export function alarmText(iso: string, timeZone?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit', timeZone }).format(at);
}

/** A sensor's value for people to read: charging words, alarm times, units. */
export function sensorText(s: PhoneSensor, timeZone?: string): string {
  if (s.state == null) return '-';
  if (NO_VALUE.has(s.state)) {
    // A next alarm reads unavailable when none is set (as in HA), unless a reason says it was not reported.
    return s.entity_id.endsWith('_next_alarm') && s.state === 'unavailable' && !s.reason ? 'None set' : s.state;
  }
  if (s.entity_id.endsWith('_battery_state')) return BATTERY_STATES[s.state] ?? s.state;
  if (s.entity_id.endsWith('_charger_type')) return CHARGER_TYPES[s.state] ?? s.state;
  if (s.domain === 'binary_sensor' && s.entity_id.endsWith('_is_charging')) return s.state === 'on' ? 'Yes' : 'No';
  if (s.device_class === 'timestamp') return alarmText(s.state, timeZone);
  return `${s.state}${s.unit ? ` ${s.unit}` : ''}`;
}

/**
 * The phone's power and next alarm in a line each, or null when its app has
 * never reported them: "Charging (wireless)", "Next alarm Tue 6:30 AM".
 */
export function phoneStatus(phone: Phone, timeZone?: string): { power: string | null; alarm: string | null } {
  const state = sensorWith(phone, '_battery_state');
  const plug = sensorWith(phone, '_charger_type');
  const charging = sensorWith(phone, '_is_charging');
  let power: string | null = null;
  if (state?.reason || (!hasValue(state) && charging?.reason)) {
    power = 'Charging state unavailable';
  } else if (hasValue(state)) {
    power = BATTERY_STATES[state.state] ?? state.state;
  } else if (hasValue(charging)) {
    power = charging.state === 'on' ? 'Charging' : 'On battery';
  }
  if (power && !power.endsWith('unavailable') && hasValue(plug) && plug.state !== 'none' && power !== 'On battery') {
    power = `${power} (${PLUG_WORDS[plug.state] ?? plug.state})`;
  }
  const alarmSensor = sensorWith(phone, '_next_alarm');
  let alarm: string | null = null;
  if (alarmSensor && alarmSensor.state != null && alarmSensor.state !== 'unknown') {
    if (alarmSensor.state !== 'unavailable') alarm = `Next alarm ${alarmText(alarmSensor.state, timeZone)}`;
    else alarm = alarmSensor.reason ? 'Next alarm unavailable' : 'No alarm set';
  }
  return { power, alarm };
}

/**
 * The beacon line for a phone: "Room beacon: on", "Room beacon: waiting for
 * Bluetooth", "Room beacon: off", or "Room detection unavailable: <reason>",
 * plus a problem when the hub did not take the registration. Null when the
 * phone never reported a beacon.
 */
export function beaconText(phone: Phone): { line: string; ok: boolean; hubProblem: string | null } | null {
  const b = phone.beacon;
  if (!b) return null;
  let line: string;
  switch (b.state) {
    case 'advertising': line = 'Room beacon: on'; break;
    case 'bluetooth_off': line = 'Room beacon: waiting for Bluetooth'; break;
    case 'disabled': line = 'Room beacon: off'; break;
    default: line = `Room detection unavailable: ${b.reason || 'the phone did not say why'}`;
  }
  const hubProblem = b.hub_registered ? null : `Not registered with the hub: ${b.hub_detail || 'no answer yet'}`;
  return { line, ok: b.state === 'advertising' && b.hub_registered, hubProblem };
}

/** The phone's room sensor (sensor.<phone>_room), if the hub has one. */
export function roomSensor(phone: Phone): PhoneSensor | undefined {
  return phone.sensors.find((s) => !s.disabled && s.domain === 'sensor' && s.entity_id.endsWith('_room'));
}

/** "Den", "Away", "Not known" for a room sensor's state. */
export function roomText(s: PhoneSensor): string {
  if (s.state == null || s.state === 'unknown' || s.state === 'unavailable') return 'Not known';
  if (s.state === 'not_home') return 'Away';
  return s.state;
}
